"""Real subprocess lifecycle, readiness, output, and cleanup tests."""

from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import pytest

from agentverify.application import (
    ApplicationStartError,
    ApplicationState,
    ManagedApplication,
    endpoint_accepts_connection,
)

SAMPLE_APP = Path(__file__).parents[1] / "examples" / "greeting_app.py"


def unused_tcp_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_exit(application: ManagedApplication, *, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while application.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert application.poll() is not None


def process_is_running(pid: int) -> bool:
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


def test_start_failure_is_narrow_and_actionable() -> None:
    with pytest.raises(ApplicationStartError, match="could not start"):
        ManagedApplication.start(
            ["agentverify-executable-that-does-not-exist"],
            max_log_bytes=1024,
        )


def test_output_is_bounded_truncated_and_continuously_drained() -> None:
    application = ManagedApplication.start(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.stdout.buffer.write(b'x' * 1000000); "
                "sys.stderr.buffer.write(b'y' * 1000000)"
            ),
        ],
        max_log_bytes=1024,
    )
    try:
        wait_for_exit(application)
        output = application.output()
    finally:
        application.stop()

    assert output.truncated is True
    assert "process output truncated" in output.text
    assert len(output.text.encode("utf-8")) <= 1024


def test_readiness_timeout_then_cleanup_leaves_direct_process_dead() -> None:
    port = unused_tcp_port()
    application = ManagedApplication.start(
        [
            sys.executable,
            str(SAMPLE_APP),
            "--port",
            str(port),
            "--startup-delay-ms",
            "5000",
        ],
        max_log_bytes=4096,
    )
    try:
        readiness = application.wait_for_readiness(
            f"http://127.0.0.1:{port}",
            timeout_ms=150,
        )
        assert readiness.ready is False
        assert readiness.reason == "Application readiness timed out"
    finally:
        application.stop(grace_seconds=0.2)

    assert application.poll() is not None


def test_unused_endpoint_allows_normal_startup_and_readiness() -> None:
    port = unused_tcp_port()
    base_url = f"http://127.0.0.1:{port}"
    assert endpoint_accepts_connection(base_url, timeout_seconds=0.05) is False

    application = ManagedApplication.start(
        [sys.executable, str(SAMPLE_APP), "--port", str(port)],
        max_log_bytes=4096,
    )
    try:
        readiness = application.wait_for_readiness(base_url, timeout_ms=5000)
        assert readiness.ready is True
        assert endpoint_accepts_connection(base_url) is True
    finally:
        application.stop(grace_seconds=0.2)

    assert application.poll() is not None


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM ignore/escalation is POSIX-specific")
def test_shutdown_escalates_to_force_kill_when_termination_is_ignored(
) -> None:
    port = unused_tcp_port()
    application = ManagedApplication.start(
        [
            sys.executable,
            str(SAMPLE_APP),
            "--port",
            str(port),
            "--ignore-terminate",
        ],
        max_log_bytes=4096,
    )
    try:
        readiness = application.wait_for_readiness(
            f"http://127.0.0.1:{port}",
            timeout_ms=5000,
        )
        assert readiness.ready is True
        shutdown = application.stop(grace_seconds=0.1)
    finally:
        if application.poll() is None:
            application.stop(grace_seconds=0.1)

    assert shutdown.force_killed is True
    assert application.state is ApplicationState.FORCE_KILLED
    assert application.poll() is not None


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group cleanup test")
def test_posix_shutdown_cleans_a_descendant_in_the_managed_process_group(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "child.pid"
    parent_code = (
        "import pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    application = ManagedApplication.start(
        [sys.executable, "-c", parent_code],
        max_log_bytes=4096,
    )
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 5
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child_pid_file.is_file()
        child_pid = int(child_pid_file.read_text(encoding="ascii"))
        application.stop(grace_seconds=0.2)
        deadline = time.monotonic() + 5
        while process_is_running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        if application.poll() is None:
            application.stop(grace_seconds=0.1)
        if child_pid is not None and process_is_running(child_pid):
            os.kill(child_pid, 9)

    assert child_pid is not None and not process_is_running(child_pid)


@pytest.mark.skipif(os.name == "nt", reason="POSIX orphaned process-group cleanup test")
def test_posix_shutdown_cleans_descendant_after_group_leader_exits(
    tmp_path: Path,
) -> None:
    port = unused_tcp_port()
    child_pid_file = tmp_path / "orphan-child.pid"
    parent_code = (
        "import pathlib, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child.pid))"
    )
    application = ManagedApplication.start(
        [sys.executable, "-c", parent_code],
        max_log_bytes=4096,
    )
    child_pid: int | None = None
    try:
        deadline = time.monotonic() + 5
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child_pid_file.is_file()
        child_pid = int(child_pid_file.read_text(encoding="ascii"))

        readiness = application.wait_for_readiness(
            f"http://127.0.0.1:{port}",
            timeout_ms=1000,
        )
        assert readiness.ready is False
        assert application.state is ApplicationState.EXITED_BEFORE_READINESS
        assert process_is_running(child_pid)

        application.stop(grace_seconds=0.2)
        deadline = time.monotonic() + 5
        while process_is_running(child_pid) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        if application.poll() is None:
            application.stop(grace_seconds=0.1)
        if child_pid is not None and process_is_running(child_pid):
            os.kill(child_pid, 9)

    assert child_pid is not None and not process_is_running(child_pid)
    assert application.state is ApplicationState.EXITED_BEFORE_READINESS
