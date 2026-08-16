"""Real M6 CLI, process, Chromium, evidence, receipt, and cleanup tests."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pytest import CaptureFixture

from agentverify.cli import EXIT_FAIL, EXIT_PASS, EXIT_UNKNOWN, main
from agentverify.domain import Verdict
from agentverify.evidence import EvidenceKind, EvidenceStore
from agentverify.receipt import ProofReceipt

REPOSITORY_ROOT = Path(__file__).parents[1]
SAMPLE_APP = REPOSITORY_ROOT / "examples" / "greeting_app.py"
PASS_PLAN = REPOSITORY_ROOT / "examples" / "greeting.plan.json"


def unused_tcp_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def cleanup_pid(pid: int | None) -> None:
    if pid is None or not process_is_alive(pid):
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
) -> list[str]:
    return [
        "verify",
        "--plan",
        str(plan),
        "--base-url",
        f"http://127.0.0.1:{port}",
        "--run-dir",
        str(run_dir),
        "--startup-timeout-ms",
        str(startup_timeout_ms),
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
) -> tuple[ProofReceipt, set[EvidenceKind]]:
    receipt_json = run_dir / "receipt.json"
    receipt_text = run_dir / "receipt.txt"
    manifest_path = run_dir / "evidence-manifest.json"
    assert receipt_json.is_file()
    assert receipt_text.is_file()
    assert manifest_path.is_file()

    receipt = ProofReceipt.model_validate_json(receipt_json.read_text(encoding="utf-8"))
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
    finally:
        if pid is None and pid_file.exists():
            pid = read_pid(pid_file)
        cleanup_pid(pid)

    captured = capsys.readouterr()
    assert exit_code == EXIT_PASS
    assert "Verdict: PASS" in captured.out
    assert captured.err == ""
    assert pid is not None and not process_is_alive(pid)
    receipt, kinds = assert_review_directory(
        run_dir,
        expected_verdict=Verdict.PASS,
        completed=True,
    )
    assert receipt.criteria[0].evidence_refs
    assert EvidenceKind.BROWSER_OBSERVATION in kinds
    assert EvidenceKind.PROCESS_LOG in kinds


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
            )
        )
        pid = read_pid(pid_file)
    finally:
        if pid is None and pid_file.exists():
            pid = read_pid(pid_file)
        cleanup_pid(pid)

    captured = capsys.readouterr()
    assert exit_code == EXIT_FAIL
    assert "Verdict: FAIL" in captured.out
    assert pid is not None and not process_is_alive(pid)
    receipt, kinds = assert_review_directory(
        run_dir,
        expected_verdict=Verdict.FAIL,
        completed=True,
    )
    assert receipt.criteria[0].verdict is Verdict.FAIL
    assert EvidenceKind.BROWSER_OBSERVATION in kinds


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
    finally:
        if pid is None and pid_file.exists():
            pid = read_pid(pid_file)
        cleanup_pid(pid)

    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert "Verdict: UNKNOWN" in captured.out
    assert pid is not None and not process_is_alive(pid)
    receipt, kinds = assert_review_directory(
        run_dir,
        expected_verdict=Verdict.UNKNOWN,
        completed=False,
    )
    assert receipt.criteria[0].reason == "Application readiness timed out"
    assert EvidenceKind.BROWSER_OBSERVATION not in kinds
    assert EvidenceKind.PROCESS_LOG in kinds


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
    finally:
        if verifier.poll() is None:
            verifier.kill()
            verifier.wait(timeout=5)
        if app_pid is None and pid_file.exists():
            app_pid = read_pid(pid_file)
        cleanup_pid(app_pid)

    assert verifier.returncode == EXIT_UNKNOWN
    assert "Verdict: UNKNOWN" in stdout
    assert "Traceback" not in stderr
    assert app_pid is not None and not process_is_alive(app_pid)
    receipt, kinds = assert_review_directory(
        run_dir,
        expected_verdict=Verdict.UNKNOWN,
        completed=False,
    )
    assert receipt.criteria[0].reason == "Verification was interrupted"
    assert EvidenceKind.PROCESS_LOG in kinds
