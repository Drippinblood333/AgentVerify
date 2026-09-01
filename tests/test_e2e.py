"""Real CLI, process, Chromium, evidence, receipt, and cleanup tests."""

from __future__ import annotations

import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest
from pytest import CaptureFixture

from agentverify_evidence.application import endpoint_accepts_connection
from agentverify_evidence.cli import EXIT_FAIL, EXIT_PASS, EXIT_UNKNOWN, EXIT_USAGE, main
from agentverify_evidence.domain import Verdict
from agentverify_evidence.evidence import EvidenceKind, EvidenceStore
from agentverify_evidence.inspection import RunIntegrityError, sha256_file
from agentverify_evidence.plan import load_plan, plan_digest
from agentverify_evidence.receipt import (
    CurrentWorktreeSourceSelection,
    DirectExecutionMetadata,
    ProofReceiptV4,
    RepositoryPlanSource,
    load_receipt,
)

REPOSITORY_ROOT = Path(__file__).parents[1]
SAMPLE_APP = REPOSITORY_ROOT / "examples" / "greeting_app.py"
PASS_PLAN = REPOSITORY_ROOT / "examples" / "greeting.plan.json"

_WINDOWS_SYNCHRONIZE = 0x00100000
_WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WINDOWS_PROCESS_TERMINATE = 0x0001
_WINDOWS_WAIT_OBJECT_0 = 0x00000000
_WINDOWS_WAIT_TIMEOUT = 0x00000102
_WINDOWS_WAIT_FAILED = 0xFFFFFFFF
_WINDOWS_ERROR_INVALID_PARAMETER = 87
_WINDOWS_CLEANUP_TIMEOUT_MS = 2_000


def unused_tcp_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _windows_kernel32() -> Any:
    ctypes_windows = cast(Any, ctypes)
    kernel32 = ctypes_windows.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.TerminateProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _open_windows_process(pid: int, access: int) -> tuple[Any, int | None]:
    ctypes_windows = cast(Any, ctypes)
    kernel32 = _windows_kernel32()
    ctypes_windows.set_last_error(0)
    handle = kernel32.OpenProcess(access, False, pid)
    if handle:
        return kernel32, int(handle)
    error = int(ctypes_windows.get_last_error())
    if error == _WINDOWS_ERROR_INVALID_PARAMETER:
        return kernel32, None
    raise ctypes_windows.WinError(error)


def _close_windows_handle(kernel32: Any, handle: int) -> None:
    ctypes_windows = cast(Any, ctypes)
    if not kernel32.CloseHandle(handle):
        raise ctypes_windows.WinError(ctypes_windows.get_last_error())


def process_is_alive(pid: int) -> bool:
    if os.name == "nt":
        ctypes_windows = cast(Any, ctypes)
        kernel32, handle = _open_windows_process(
            pid,
            _WINDOWS_SYNCHRONIZE | _WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION,
        )
        if handle is None:
            return False
        try:
            result = int(kernel32.WaitForSingleObject(handle, 0))
            if result == _WINDOWS_WAIT_TIMEOUT:
                return True
            if result == _WINDOWS_WAIT_OBJECT_0:
                return False
            if result == _WINDOWS_WAIT_FAILED:
                raise ctypes_windows.WinError(ctypes_windows.get_last_error())
            raise RuntimeError(f"unexpected WaitForSingleObject result: {result}")
        finally:
            _close_windows_handle(kernel32, handle)

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        fields = proc_stat.read_text(encoding="utf-8").split()
        if len(fields) > 2 and fields[2] == "Z":
            return False
    return True


def defensive_cleanup_pid(pid: int | None) -> None:
    if pid is None:
        return
    if os.name == "nt":
        ctypes_windows = cast(Any, ctypes)
        kernel32, handle = _open_windows_process(
            pid,
            _WINDOWS_SYNCHRONIZE | _WINDOWS_PROCESS_TERMINATE,
        )
        if handle is None:
            return
        try:
            result = int(kernel32.WaitForSingleObject(handle, 0))
            if result == _WINDOWS_WAIT_OBJECT_0:
                return
            if result == _WINDOWS_WAIT_FAILED:
                raise ctypes_windows.WinError(ctypes_windows.get_last_error())
            if result != _WINDOWS_WAIT_TIMEOUT:
                raise RuntimeError(f"unexpected WaitForSingleObject result: {result}")
            if not kernel32.TerminateProcess(handle, 1):
                error = int(ctypes_windows.get_last_error())
                if int(kernel32.WaitForSingleObject(handle, 0)) == _WINDOWS_WAIT_OBJECT_0:
                    return
                raise ctypes_windows.WinError(error)
            result = int(
                kernel32.WaitForSingleObject(handle, _WINDOWS_CLEANUP_TIMEOUT_MS)
            )
            if result == _WINDOWS_WAIT_OBJECT_0:
                return
            if result == _WINDOWS_WAIT_TIMEOUT:
                raise AssertionError(f"process {pid} did not terminate within 2 seconds")
            if result == _WINDOWS_WAIT_FAILED:
                raise ctypes_windows.WinError(ctypes_windows.get_last_error())
            raise RuntimeError(f"unexpected WaitForSingleObject result: {result}")
        finally:
            _close_windows_handle(kernel32, handle)

    if not process_is_alive(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + 1
    while process_is_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    if process_is_alive(pid):
        try:
            os.kill(pid, 9)
        except OSError:
            return


def test_process_is_alive_observation_is_non_mutating_and_detects_exit() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert process.poll() is None
        for _ in range(3):
            assert process_is_alive(process.pid) is True
            assert process.poll() is None
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    assert process.poll() is not None
    assert process_is_alive(process.pid) is False


def read_pid(pid_file: Path) -> int:
    return int(pid_file.read_text(encoding="ascii"))


def cli_args(
    *,
    plan: Path,
    port: int,
    run_dir: Path,
    pid_file: Path,
    extra_app_args: tuple[str, ...] = (),
    startup_timeout_ms: int = 5000,
    output_format: str | None = None,
) -> list[str]:
    args = [
        "verify",
        "--plan",
        str(plan),
        "--base-url",
        f"http://127.0.0.1:{port}",
        "--run-dir",
        str(run_dir),
        "--startup-timeout-ms",
        str(startup_timeout_ms),
    ]
    if output_format is not None:
        args.extend(("--output-format", output_format))
    return [
        *args,
        "--app-command",
        sys.executable,
        str(SAMPLE_APP),
        "--port",
        str(port),
        "--pid-file",
        str(pid_file),
        *extra_app_args,
    ]


def assert_review_directory(
    run_dir: Path,
    *,
    expected_verdict: Verdict,
    completed: bool,
) -> tuple[ProofReceiptV4, set[EvidenceKind]]:
    receipt_json = run_dir / "receipt.json"
    receipt_text = run_dir / "receipt.txt"
    manifest_path = run_dir / "evidence-manifest.json"
    assert receipt_json.is_file()
    assert receipt_text.is_file()
    assert manifest_path.is_file()

    receipt = load_receipt(receipt_json)
    assert isinstance(receipt, ProofReceiptV4)
    assert receipt.overall_verdict is expected_verdict
    assert receipt.completed is completed
    assert f"Verdict: {expected_verdict.value}" in receipt_text.read_text(encoding="utf-8")

    store = EvidenceStore(run_dir)
    manifest = store.load_manifest()
    store.verify_manifest(manifest)
    artifacts_by_path = {
        artifact.relative_path: artifact for artifact in manifest.artifacts
    }
    for criterion in receipt.criteria:
        for evidence_ref in criterion.evidence_refs:
            assert evidence_ref in artifacts_by_path
            store.verify_artifact(artifacts_by_path[evidence_ref])
    return receipt, {artifact.kind for artifact in manifest.artifacts}


def test_end_to_end_pass_creates_reviewable_outputs_and_cleans_process(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    port = unused_tcp_port()
    run_dir = tmp_path / "pass-run"
    pid_file = tmp_path / "pass.pid"
    pid: int | None = None
    try:
        exit_code = main(
            cli_args(
                plan=PASS_PLAN,
                port=port,
                run_dir=run_dir,
                pid_file=pid_file,
            )
        )
        pid = read_pid(pid_file)
        assert pid is not None
        assert not process_is_alive(pid)

        captured = capsys.readouterr()
        assert exit_code == EXIT_PASS
        assert "Verdict: PASS" in captured.out
        assert f"Receipt: {run_dir.resolve() / 'receipt.txt'}" in captured.out
        assert f"Receipt JSON: {run_dir.resolve() / 'receipt.json'}" in captured.out
        assert (
            f"Evidence manifest: {run_dir.resolve() / 'evidence-manifest.json'}"
            in captured.out
        )
        assert captured.err == ""
        receipt, kinds = assert_review_directory(
            run_dir,
            expected_verdict=Verdict.PASS,
            completed=True,
        )
        assert receipt.criteria[0].evidence_refs
        assert receipt.schema_version == 4
        assert isinstance(receipt.execution, DirectExecutionMetadata)
        assert isinstance(receipt.source_selection, CurrentWorktreeSourceSelection)
        assert isinstance(receipt.plan_source, RepositoryPlanSource)
        assert receipt.plan_source.repository_relative_path == "examples/greeting.plan.json"
        assert receipt.execution.isolation_mode == "none"
        assert receipt.plan_digest == plan_digest(load_plan(PASS_PLAN))
        assert receipt.environment.agentverify_version
        assert receipt.environment.python_version
        assert receipt.environment.platform
        assert receipt.environment.playwright_version
        assert receipt.source_provenance.kind == "git"
        assert receipt.source_provenance.revision is not None
        assert len(receipt.source_provenance.revision) == 40
        expected_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()
        assert receipt.source_provenance.revision == expected_head
        assert isinstance(receipt.source_provenance.dirty_worktree, bool)
        assert receipt.evidence_manifest_digest == sha256_file(
            run_dir / "evidence-manifest.json"
        )
        assert EvidenceKind.BROWSER_OBSERVATION in kinds
        assert EvidenceKind.PROCESS_LOG in kinds
    finally:
        if pid is None and pid_file.exists():
            pid = read_pid(pid_file)
        defensive_cleanup_pid(pid)


def test_json_pass_is_one_object_with_resolved_created_paths(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    port = unused_tcp_port()
    run_dir = tmp_path / "json-pass-run"
    pid_file = tmp_path / "json-pass.pid"
    exit_code = main(
        cli_args(
            plan=PASS_PLAN,
            port=port,
            run_dir=run_dir,
            pid_file=pid_file,
            output_format="json",
        )
    )

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == EXIT_PASS
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1
    assert "Verdict:" not in captured.out
    assert captured.err == ""
    assert summary == {
        "completed": True,
        "evidence_manifest_path": str(
            (run_dir / "evidence-manifest.json").resolve()
        ),
        "exit_code": EXIT_PASS,
        "output_schema_version": 1,
        "receipt_json_path": str((run_dir / "receipt.json").resolve()),
        "receipt_schema_version": 4,
        "receipt_text_path": str((run_dir / "receipt.txt").resolve()),
        "verdict": "PASS",
    }
    assert all(Path(summary[key]).is_file() for key in (
        "receipt_json_path",
        "receipt_text_path",
        "evidence_manifest_path",
    ))


def test_real_run_manifest_tampering_is_reported_by_cli_inspect(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    port = unused_tcp_port()
    run_dir = tmp_path / "manifest-tamper-run"
    pid_file = tmp_path / "manifest-tamper.pid"

    assert (
        main(cli_args(plan=PASS_PLAN, port=port, run_dir=run_dir, pid_file=pid_file))
        == EXIT_PASS
    )
    capsys.readouterr()
    manifest_path = run_dir / "evidence-manifest.json"
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

    exit_code = main(["inspect", "--run-dir", str(run_dir)])
    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert "integrity warning" in captured.err
    assert "does not match" in captured.err


def test_real_run_artifact_tampering_is_reported_by_cli_inspect(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    port = unused_tcp_port()
    run_dir = tmp_path / "artifact-tamper-run"
    pid_file = tmp_path / "artifact-tamper.pid"

    assert (
        main(cli_args(plan=PASS_PLAN, port=port, run_dir=run_dir, pid_file=pid_file))
        == EXIT_PASS
    )
    capsys.readouterr()
    manifest = EvidenceStore(run_dir).load_manifest()
    observation = next(
        artifact
        for artifact in manifest.artifacts
        if artifact.kind is EvidenceKind.BROWSER_OBSERVATION
    )
    artifact_path = run_dir / observation.relative_path
    artifact_path.write_bytes(b"tampered browser observation")

    exit_code = main(["inspect", "--run-dir", str(run_dir)])
    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert "integrity warning" in captured.err
    assert "mismatch" in captured.err


def test_repeat_runs_preserve_stable_semantics_and_browser_observation_bytes(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    run_dirs: list[Path] = []
    receipts: list[ProofReceiptV4] = []
    observations: list[bytes] = []
    for run_number in (1, 2):
        port = unused_tcp_port()
        run_dir = tmp_path / f"repeat-{run_number}"
        pid_file = tmp_path / f"repeat-{run_number}.pid"
        assert (
            main(cli_args(plan=PASS_PLAN, port=port, run_dir=run_dir, pid_file=pid_file))
            == EXIT_PASS
        )
        capsys.readouterr()
        loaded = load_receipt(run_dir / "receipt.json")
        assert isinstance(loaded, ProofReceiptV4)
        manifest = EvidenceStore(run_dir).load_manifest()
        observation = next(
            artifact
            for artifact in manifest.artifacts
            if artifact.kind is EvidenceKind.BROWSER_OBSERVATION
        )
        run_dirs.append(run_dir)
        receipts.append(loaded)
        observations.append((run_dir / observation.relative_path).read_bytes())

    first, second = receipts
    assert first.plan_digest == second.plan_digest
    assert first.criteria == second.criteria
    assert first.overall_verdict is second.overall_verdict is Verdict.PASS
    assert first.completed is second.completed is True
    assert first.source_provenance == second.source_provenance
    assert first.environment == second.environment
    assert observations[0] == observations[1]
    assert run_dirs[0] != run_dirs[1]


def test_plan_change_after_cli_snapshot_warns_but_preserves_frozen_pass(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    mutable_plan = tmp_path / "mutable.plan.json"
    mutable_plan.write_bytes(PASS_PLAN.read_bytes())
    frozen_digest = plan_digest(load_plan(mutable_plan))
    changed_payload = json.loads(PASS_PLAN.read_text(encoding="utf-8"))
    changed_payload["criteria"][0]["description"] = "Changed after snapshot"
    replacement = json.dumps(changed_payload)
    mutation_code = (
        "from pathlib import Path; "
        f"Path({str(mutable_plan)!r}).write_text({replacement!r}, encoding='utf-8'); "
        f"exec(compile(Path({str(SAMPLE_APP)!r}).read_bytes(), "
        f"{str(SAMPLE_APP)!r}, 'exec'))"
    )
    port = unused_tcp_port()
    run_dir = tmp_path / "plan-drift-run"
    pid_file = tmp_path / "plan-drift.pid"
    exit_code = main(
        [
            "verify",
            "--plan",
            str(mutable_plan),
            "--base-url",
            f"http://127.0.0.1:{port}",
            "--run-dir",
            str(run_dir),
            "--output-format",
            "json",
            "--app-command",
            sys.executable,
            "-c",
            mutation_code,
            "--port",
            str(port),
            "--pid-file",
            str(pid_file),
        ]
    )

    captured = capsys.readouterr()
    receipt = load_receipt(run_dir / "receipt.json")
    summary = json.loads(captured.out)
    assert isinstance(receipt, ProofReceiptV4)
    assert exit_code == EXIT_PASS
    assert summary["verdict"] == "PASS"
    assert summary["exit_code"] == EXIT_PASS
    assert "plan file changed after verification snapshot" in captured.err
    assert frozen_digest in captured.err
    assert receipt.plan_digest == frozen_digest


def test_real_verification_outside_git_remains_pass_with_unavailable_provenance(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside_git = tmp_path / "outside-git"
    outside_git.mkdir()
    monkeypatch.chdir(outside_git)
    port = unused_tcp_port()
    run_dir = tmp_path / "outside-git-run"
    pid_file = tmp_path / "outside-git.pid"

    exit_code = main(
        cli_args(plan=PASS_PLAN, port=port, run_dir=run_dir, pid_file=pid_file)
    )

    captured = capsys.readouterr()
    receipt = load_receipt(run_dir / "receipt.json")
    assert isinstance(receipt, ProofReceiptV4)
    assert exit_code == EXIT_PASS
    assert "Verdict: PASS" in captured.out
    assert receipt.source_provenance.kind == "unavailable"
    assert receipt.source_provenance.revision is None
    assert any("Source provenance unavailable" in item for item in receipt.limitations)


def test_final_self_check_failure_never_reports_successful_pass(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_self_check(run_dir: Path) -> None:
        raise RunIntegrityError(f"forced self-check mismatch in {run_dir.name}")

    monkeypatch.setattr("agentverify_evidence.run.inspect_run_directory", fail_self_check)
    port = unused_tcp_port()
    run_dir = tmp_path / "self-check-run"
    pid_file = tmp_path / "self-check.pid"

    exit_code = main(
        cli_args(
            plan=PASS_PLAN,
            port=port,
            run_dir=run_dir,
            pid_file=pid_file,
            output_format="json",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert captured.out == ""
    assert "integrity warning" in captured.err
    assert "final integrity self-check" in captured.err
    assert (run_dir / "receipt.json").is_file()


def test_end_to_end_real_assertion_fail_is_authoritative_and_cleans_process(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    fail_plan = tmp_path / "fail.plan.json"
    fail_plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "task": "Verify an intentional contradiction",
                "criteria": [
                    {
                        "id": "AC-FAIL",
                        "description": "The permanently hidden element is visible",
                        "procedure": {
                            "type": "browser",
                            "timeout_ms": 500,
                            "steps": [
                                {"type": "navigate", "path": "/"},
                                {
                                    "type": "assert_visible",
                                    "selector": "#never-visible",
                                },
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    port = unused_tcp_port()
    run_dir = tmp_path / "fail-run"
    pid_file = tmp_path / "fail.pid"
    pid: int | None = None
    try:
        exit_code = main(
            cli_args(
                plan=fail_plan,
                port=port,
                run_dir=run_dir,
                pid_file=pid_file,
                output_format="json",
            )
        )
        pid = read_pid(pid_file)
        assert pid is not None
        assert not process_is_alive(pid)

        captured = capsys.readouterr()
        summary = json.loads(captured.out)
        assert exit_code == EXIT_FAIL
        assert summary["verdict"] == "FAIL"
        assert summary["completed"] is True
        assert summary["exit_code"] == EXIT_FAIL
        assert summary["receipt_schema_version"] == 4
        receipt, kinds = assert_review_directory(
            run_dir,
            expected_verdict=Verdict.FAIL,
            completed=True,
        )
        assert receipt.criteria[0].verdict is Verdict.FAIL
        assert EvidenceKind.BROWSER_OBSERVATION in kinds
    finally:
        if pid is None and pid_file.exists():
            pid = read_pid(pid_file)
        defensive_cleanup_pid(pid)


def test_readiness_timeout_is_unknown_reviewable_and_cleans_process(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    port = unused_tcp_port()
    run_dir = tmp_path / "timeout-run"
    pid_file = tmp_path / "timeout.pid"
    pid: int | None = None
    try:
        exit_code = main(
            cli_args(
                plan=PASS_PLAN,
                port=port,
                run_dir=run_dir,
                pid_file=pid_file,
                extra_app_args=("--startup-delay-ms", "5000"),
                startup_timeout_ms=200,
            )
        )
        pid = read_pid(pid_file)
        assert pid is not None
        assert not process_is_alive(pid)

        captured = capsys.readouterr()
        assert exit_code == EXIT_UNKNOWN
        assert "Verdict: UNKNOWN" in captured.out
        receipt, kinds = assert_review_directory(
            run_dir,
            expected_verdict=Verdict.UNKNOWN,
            completed=False,
        )
        assert receipt.criteria[0].reason == "Application readiness timed out"
        assert EvidenceKind.BROWSER_OBSERVATION not in kinds
        assert EvidenceKind.PROCESS_LOG in kinds

        inspect_exit = main(["inspect", "--run-dir", str(run_dir)])
        inspect_output = capsys.readouterr()
        assert inspect_exit == EXIT_PASS
        assert "Verdict: UNKNOWN" in inspect_output.out
        assert "Integrity: OK" in inspect_output.out
        assert inspect_output.err == ""
    finally:
        if pid is None and pid_file.exists():
            pid = read_pid(pid_file)
        defensive_cleanup_pid(pid)


def test_application_exit_before_readiness_is_unknown_with_process_log(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    port = unused_tcp_port()
    run_dir = tmp_path / "early-exit-run"
    exit_code = main(
        [
            "verify",
            "--plan",
            str(PASS_PLAN),
            "--base-url",
            f"http://127.0.0.1:{port}",
            "--run-dir",
            str(run_dir),
            "--startup-timeout-ms",
            "1000",
            "--app-command",
            sys.executable,
            "-c",
            "print('application exited deliberately')",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert "Verdict: UNKNOWN" in captured.out
    receipt, kinds = assert_review_directory(
        run_dir,
        expected_verdict=Verdict.UNKNOWN,
        completed=False,
    )
    assert receipt.criteria[0].reason == "Application exited before readiness"
    assert EvidenceKind.PROCESS_LOG in kinds


def test_preexisting_endpoint_is_rejected_without_starting_managed_command(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    port = unused_tcp_port()
    base_url = f"http://127.0.0.1:{port}"
    external_server = subprocess.Popen(
        [sys.executable, str(SAMPLE_APP), "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    managed_pid_file = tmp_path / "managed.pid"
    run_dir = tmp_path / "conflicting-run"
    managed_pid: int | None = None
    try:
        deadline = time.monotonic() + 5
        while (
            not endpoint_accepts_connection(base_url, timeout_seconds=0.05)
            and time.monotonic() < deadline
        ):
            if external_server.poll() is not None:
                break
            time.sleep(0.01)
        assert endpoint_accepts_connection(base_url) is True

        managed_code = (
            "import os, pathlib, time; "
            f"pathlib.Path({str(managed_pid_file)!r}).write_text(str(os.getpid())); "
            "time.sleep(60)"
        )
        exit_code = main(
            [
                "verify",
                "--plan",
                str(PASS_PLAN),
                "--base-url",
                base_url,
                "--run-dir",
                str(run_dir),
                "--app-command",
                sys.executable,
                "-c",
                managed_code,
            ]
        )
        if managed_pid_file.exists():
            managed_pid = read_pid(managed_pid_file)
    finally:
        defensive_cleanup_pid(managed_pid)
        if external_server.poll() is None:
            external_server.terminate()
            try:
                external_server.wait(timeout=2)
            except subprocess.TimeoutExpired:
                external_server.kill()
                external_server.wait(timeout=2)

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "endpoint is already accepting connections" in captured.err
    assert captured.out == ""
    assert not managed_pid_file.exists()
    assert not run_dir.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX orphaned process-group E2E test")
def test_early_exit_e2e_cleans_orphaned_descendant_process_group(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    port = unused_tcp_port()
    run_dir = tmp_path / "orphan-run"
    child_pid_file = tmp_path / "orphan-child.pid"
    parent_code = (
        "import pathlib, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid))"
    )
    child_pid: int | None = None
    try:
        exit_code = main(
            [
                "verify",
                "--plan",
                str(PASS_PLAN),
                "--base-url",
                f"http://127.0.0.1:{port}",
                "--run-dir",
                str(run_dir),
                "--startup-timeout-ms",
                "1000",
                "--app-command",
                sys.executable,
                "-c",
                parent_code,
            ]
        )
        assert child_pid_file.is_file()
        child_pid = read_pid(child_pid_file)
        assert child_pid is not None
        assert not process_is_alive(child_pid)

        captured = capsys.readouterr()
        assert exit_code == EXIT_UNKNOWN
        assert "Verdict: UNKNOWN" in captured.out
        receipt, kinds = assert_review_directory(
            run_dir,
            expected_verdict=Verdict.UNKNOWN,
            completed=False,
        )
        assert receipt.criteria[0].reason == "Application exited before readiness"
        assert EvidenceKind.BROWSER_OBSERVATION not in kinds
    finally:
        if child_pid is None and child_pid_file.exists():
            child_pid = read_pid(child_pid_file)
        defensive_cleanup_pid(child_pid)


@pytest.mark.skipif(os.name == "nt", reason="reliable CLI SIGINT test is POSIX-specific")
def test_interrupt_creates_incomplete_receipt_and_cleans_application(
    tmp_path: Path,
) -> None:
    port = unused_tcp_port()
    run_dir = tmp_path / "interrupt-run"
    pid_file = tmp_path / "interrupt.pid"
    command = [
        sys.executable,
        "-m",
        "agentverify",
        *cli_args(
            plan=PASS_PLAN,
            port=port,
            run_dir=run_dir,
            pid_file=pid_file,
            extra_app_args=("--startup-delay-ms", "10000"),
            startup_timeout_ms=30_000,
        ),
    ]
    verifier = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    app_pid: int | None = None
    try:
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            if verifier.poll() is not None:
                break
            time.sleep(0.02)
        assert pid_file.is_file()
        app_pid = read_pid(pid_file)
        verifier.send_signal(signal.SIGINT)
        stdout, stderr = verifier.communicate(timeout=15)
        assert app_pid is not None
        assert not process_is_alive(app_pid)

        assert verifier.returncode == EXIT_UNKNOWN
        assert "Verdict: UNKNOWN" in stdout
        assert "Traceback" not in stderr
        receipt, kinds = assert_review_directory(
            run_dir,
            expected_verdict=Verdict.UNKNOWN,
            completed=False,
        )
        assert receipt.criteria[0].reason == "Verification was interrupted"
        assert EvidenceKind.PROCESS_LOG in kinds
    finally:
        if verifier.poll() is None:
            verifier.kill()
            verifier.wait(timeout=5)
        if app_pid is None and pid_file.exists():
            app_pid = read_pid(pid_file)
        defensive_cleanup_pid(app_pid)
