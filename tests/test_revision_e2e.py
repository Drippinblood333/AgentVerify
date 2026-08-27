"""Real Git, Chromium, receipt, mutation, and cleanup tests for M9."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pytest import CaptureFixture, MonkeyPatch

from agentverify.cli import EXIT_FAIL, EXIT_PASS, EXIT_UNKNOWN, EXIT_USAGE, main
from agentverify.domain import Verdict
from agentverify.receipt import (
    GitWorktreeSourceSelection,
    ProofReceiptV4,
    load_receipt,
)
from agentverify.worktree import ManagedGitWorktree

REPOSITORY_ROOT = Path(__file__).parents[1]
SAMPLE_APP = REPOSITORY_ROOT / "examples" / "greeting_app.py"
PASS_PLAN = REPOSITORY_ROOT / "examples" / "greeting.plan.json"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=False,
        shell=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def unused_tcp_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def create_two_commit_repository(
    tmp_path: Path,
    *,
    mutate_source_at_runtime: bool = False,
) -> tuple[Path, str, str]:
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "AgentVerify Test")
    git(repo, "config", "user.email", "agentverify@example.invalid")
    app = repo / "app.py"
    shutil.copy2(SAMPLE_APP, app)
    if mutate_source_at_runtime:
        content = app.read_text(encoding="utf-8")
        content = content.replace(
            "def main() -> None:\n",
            "def main() -> None:\n"
            "    Path(__file__).with_name('runtime-mutation.txt').write_text("
            "'changed', encoding='utf-8')\n",
        )
        app.write_text(content, encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "revision A")
    revision_a = git(repo, "rev-parse", "HEAD")

    content = app.read_text(encoding="utf-8").replace('id="message"', 'id="changed"')
    app.write_text(content, encoding="utf-8")
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "revision B")
    return repo, revision_a, git(repo, "rev-parse", "HEAD")


def revision_args(
    *,
    plan: Path,
    revision: str,
    port: int,
    run_dir: Path,
    pid_file: Path,
) -> list[str]:
    return [
        "verify",
        "--plan",
        str(plan),
        "--base-url",
        f"http://127.0.0.1:{port}",
        "--run-dir",
        str(run_dir),
        "--revision",
        revision,
        "--app-command",
        sys.executable,
        "app.py",
        "--port",
        str(port),
        "--pid-file",
        str(pid_file),
    ]


def load_v4(run_dir: Path) -> ProofReceiptV4:
    receipt = load_receipt(run_dir / "receipt.json")
    assert isinstance(receipt, ProofReceiptV4)
    return receipt


def test_revision_a_runs_while_caller_is_b_and_dirty_state_is_preserved(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    repo, revision_a, revision_b = create_two_commit_repository(tmp_path)
    (repo / "app.py").write_text(
        (repo / "app.py").read_text(encoding="utf-8") + "# unstaged\n",
        encoding="utf-8",
    )
    (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
    git(repo, "add", "staged.txt")
    (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    caller_plan = repo / "caller.plan.json"
    shutil.copy2(PASS_PLAN, caller_plan)
    caller_status = git(repo, "status", "--porcelain=v1", "--untracked-files=normal")
    worktrees_before = git(repo, "worktree", "list", "--porcelain")
    monkeypatch.chdir(repo)
    run_dir = tmp_path / "revision-run"

    exit_code = main(
        revision_args(
            plan=caller_plan,
            revision=revision_a,
            port=unused_tcp_port(),
            run_dir=run_dir,
            pid_file=tmp_path / "app.pid",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_PASS
    assert "Verdict: PASS" in captured.out
    assert git(repo, "rev-parse", "HEAD") == revision_b
    assert git(repo, "status", "--porcelain=v1", "--untracked-files=normal") == caller_status
    assert git(repo, "worktree", "list", "--porcelain") == worktrees_before
    receipt = load_v4(run_dir)
    assert receipt.source_provenance.revision == revision_a
    assert receipt.source_provenance.dirty_worktree is False
    assert isinstance(receipt.source_selection, GitWorktreeSourceSelection)
    assert receipt.source_selection.resolved_revision == revision_a
    assert receipt.source_selection.caller_head_revision == revision_b
    assert receipt.source_selection.caller_dirty_worktree is True
    assert receipt.source_selection.post_run_dirty_worktree is False
    assert receipt.source_selection.cleanup_confirmed is True
    assert receipt.plan_source.kind == "repository"
    assert receipt.plan_source.repository_relative_path == "caller.plan.json"
    assert receipt.plan_source.caller_source_revision == revision_b
    assert receipt.plan_source.caller_dirty_worktree is True
    assert "agentverify-worktree-" not in (run_dir / "receipt.json").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("failing_plan", "expected_exit", "expected_verdict"),
    ((False, EXIT_UNKNOWN, Verdict.UNKNOWN), (True, EXIT_FAIL, Verdict.FAIL)),
)
def test_source_mutation_downgrades_pass_but_real_fail_dominates(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    failing_plan: bool,
    expected_exit: int,
    expected_verdict: Verdict,
) -> None:
    repo, revision_a, _ = create_two_commit_repository(
        tmp_path,
        mutate_source_at_runtime=True,
    )
    plan = PASS_PLAN
    if failing_plan:
        plan = tmp_path / "fail.plan.json"
        payload = json.loads(PASS_PLAN.read_text(encoding="utf-8"))
        payload["criteria"][0]["procedure"]["steps"][-1]["selector"] = "#never-visible"
        plan.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.chdir(repo)
    run_dir = tmp_path / "mutation-run"

    assert main(
        revision_args(
            plan=plan,
            revision=revision_a,
            port=unused_tcp_port(),
            run_dir=run_dir,
            pid_file=tmp_path / "mutation.pid",
        )
    ) == expected_exit
    capsys.readouterr()

    receipt = load_v4(run_dir)
    assert receipt.overall_verdict is expected_verdict
    assert receipt.completed is False
    assert isinstance(receipt.source_selection, GitWorktreeSourceSelection)
    assert receipt.source_selection.post_run_dirty_worktree is True
    assert receipt.source_selection.cleanup_confirmed is True
    assert "runtime-mutation.txt" not in git(repo, "status", "--porcelain=v1")
    assert "agentverify-worktree-" not in git(repo, "worktree", "list", "--porcelain")


@pytest.mark.parametrize(
    ("failing_plan", "expected_exit", "expected_verdict"),
    ((False, EXIT_UNKNOWN, Verdict.UNKNOWN), (True, EXIT_FAIL, Verdict.FAIL)),
)
def test_cleanup_failure_is_structured_and_preserves_fail_dominance(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    failing_plan: bool,
    expected_exit: int,
    expected_verdict: Verdict,
) -> None:
    repo, revision_a, _ = create_two_commit_repository(tmp_path)
    plan = PASS_PLAN
    if failing_plan:
        plan = tmp_path / "cleanup-fail.plan.json"
        payload = json.loads(PASS_PLAN.read_text(encoding="utf-8"))
        payload["criteria"][0]["procedure"]["steps"][-1]["selector"] = "#never-visible"
        plan.write_text(json.dumps(payload), encoding="utf-8")
    original_cleanup = ManagedGitWorktree.cleanup

    def remove_but_report_unconfirmed(worktree: ManagedGitWorktree) -> bool:
        original_cleanup(worktree)
        return False

    monkeypatch.setattr(ManagedGitWorktree, "cleanup", remove_but_report_unconfirmed)
    monkeypatch.chdir(repo)
    run_dir = tmp_path / "cleanup-failure-run"

    assert main(
        revision_args(
            plan=plan,
            revision=revision_a,
            port=unused_tcp_port(),
            run_dir=run_dir,
            pid_file=tmp_path / "cleanup.pid",
        )
    ) == expected_exit
    capsys.readouterr()

    receipt = load_v4(run_dir)
    assert receipt.overall_verdict is expected_verdict
    assert receipt.completed is False
    assert isinstance(receipt.source_selection, GitWorktreeSourceSelection)
    assert receipt.source_selection.cleanup_confirmed is False
    assert "agentverify-worktree-" not in git(repo, "worktree", "list", "--porcelain")


def test_revision_mode_detects_drift_at_original_caller_plan_path(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    repo, revision_a, _ = create_two_commit_repository(tmp_path)
    caller_plan = repo / "caller.plan.json"
    original_payload = json.loads(PASS_PLAN.read_text(encoding="utf-8"))
    caller_plan.write_text(json.dumps(original_payload), encoding="utf-8")
    changed_payload = dict(original_payload)
    changed_payload["task"] = "Changed after the caller snapshot"
    mutation = (
        "from pathlib import Path; "
        f"Path({str(caller_plan)!r}).write_text({json.dumps(changed_payload)!r}, "
        "encoding='utf-8'); "
        "exec(compile(Path('app.py').read_bytes(), 'app.py', 'exec'))"
    )
    monkeypatch.chdir(repo)
    port = unused_tcp_port()
    run_dir = tmp_path / "revision-plan-drift-run"

    exit_code = main(
        [
            "verify",
            "--plan",
            str(caller_plan),
            "--base-url",
            f"http://127.0.0.1:{port}",
            "--run-dir",
            str(run_dir),
            "--revision",
            revision_a,
            "--app-command",
            sys.executable,
            "-c",
            mutation,
            "--port",
            str(port),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_PASS
    assert "plan file changed after verification snapshot" in captured.err
    receipt = load_v4(run_dir)
    assert receipt.task == original_payload["task"]
    assert receipt.overall_verdict is Verdict.PASS
    assert isinstance(receipt.source_selection, GitWorktreeSourceSelection)
    assert receipt.source_selection.post_run_dirty_worktree is False
    assert receipt.source_selection.cleanup_confirmed is True


def test_invalid_revision_is_usage_error_without_run_app_or_worktree(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    repo, _, _ = create_two_commit_repository(tmp_path)
    marker = tmp_path / "application-started"
    run_dir = tmp_path / "invalid-run"
    worktrees_before = git(repo, "worktree", "list", "--porcelain")
    monkeypatch.chdir(repo)

    exit_code = main(
        [
            "verify",
            "--plan",
            str(PASS_PLAN),
            "--base-url",
            f"http://127.0.0.1:{unused_tcp_port()}",
            "--run-dir",
            str(run_dir),
            "--revision",
            "does-not-exist",
            "--app-command",
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "revision does not identify one local commit" in captured.err
    assert not run_dir.exists()
    assert not marker.exists()
    assert git(repo, "worktree", "list", "--porcelain") == worktrees_before


def test_revision_readiness_unknown_cleans_registered_worktree(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    repo, revision_a, _ = create_two_commit_repository(tmp_path)
    before = git(repo, "worktree", "list", "--porcelain")
    monkeypatch.chdir(repo)
    port = unused_tcp_port()
    run_dir = tmp_path / "readiness-unknown-run"
    args = revision_args(
        plan=PASS_PLAN,
        revision=revision_a,
        port=port,
        run_dir=run_dir,
        pid_file=tmp_path / "readiness.pid",
    )
    app_option = args.index("--app-command")
    args[app_option:app_option] = ["--startup-timeout-ms", "200"]
    args.extend(("--startup-delay-ms", "5000"))

    assert main(args) == EXIT_UNKNOWN
    capsys.readouterr()
    receipt = load_v4(run_dir)
    assert receipt.overall_verdict is Verdict.UNKNOWN
    assert receipt.criteria[0].reason == "Application readiness timed out"
    assert isinstance(receipt.source_selection, GitWorktreeSourceSelection)
    assert receipt.source_selection.cleanup_confirmed is True
    assert git(repo, "worktree", "list", "--porcelain") == before


@pytest.mark.skipif(os.name == "nt", reason="reliable CLI SIGINT test is POSIX-specific")
def test_revision_interrupt_cleans_application_and_registered_worktree(tmp_path: Path) -> None:
    repo, revision_a, _ = create_two_commit_repository(tmp_path)
    before = git(repo, "worktree", "list", "--porcelain")
    port = unused_tcp_port()
    run_dir = tmp_path / "revision-interrupt-run"
    pid_file = tmp_path / "revision-interrupt.pid"
    command = [
        sys.executable,
        "-m",
        "agentverify",
        *revision_args(
            plan=PASS_PLAN,
            revision=revision_a,
            port=port,
            run_dir=run_dir,
            pid_file=pid_file,
        ),
    ]
    app_option = command.index("--app-command")
    command[app_option:app_option] = ["--startup-timeout-ms", "30000"]
    command.extend(("--startup-delay-ms", "10000"))
    verifier = subprocess.Popen(
        command,
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            if verifier.poll() is not None:
                break
            time.sleep(0.02)
        assert pid_file.is_file()
        verifier.send_signal(signal.SIGINT)
        stdout, stderr = verifier.communicate(timeout=15)
    finally:
        if verifier.poll() is None:
            verifier.kill()
            verifier.wait(timeout=5)

    assert verifier.returncode == EXIT_UNKNOWN
    assert "Verdict: UNKNOWN" in stdout
    assert "Traceback" not in stderr
    receipt = load_v4(run_dir)
    assert receipt.criteria[0].reason == "Verification was interrupted"
    assert isinstance(receipt.source_selection, GitWorktreeSourceSelection)
    assert receipt.source_selection.cleanup_confirmed is True
    assert git(repo, "worktree", "list", "--porcelain") == before
