"""Observable behavior tests for the M1 command-line interface."""

from pathlib import Path

import pytest
from pytest import CaptureFixture

from agentverify import __version__
from agentverify.cli import EXIT_SUCCESS, EXIT_USAGE, main


def assert_no_traceback(stdout: str, stderr: str) -> None:
    """Assert that ordinary CLI output does not expose a Python traceback."""
    assert "Traceback" not in stdout
    assert "Traceback" not in stderr


def test_help_exits_successfully(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == EXIT_SUCCESS
    assert "usage: agentverify" in captured.out
    assert "verify" in captured.out
    assert captured.err == ""
    assert_no_traceback(captured.out, captured.err)


def test_version_exits_successfully(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    captured = capsys.readouterr()
    assert exc_info.value.code == EXIT_SUCCESS
    assert captured.out == f"AgentVerify {__version__}\n"
    assert captured.err == ""
    assert_no_traceback(captured.out, captured.err)


def test_verify_accepts_a_regular_plan_file(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "plan.yaml"
    plan.write_text("content is intentionally not parsed in M1\n", encoding="utf-8")

    exit_code = main(["verify", "--plan", str(plan)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_SUCCESS
    assert captured.out == (
        "Verification execution is not implemented yet.\n"
        f"Plan: {plan.resolve()}\n"
    )
    assert captured.err == ""
    assert_no_traceback(captured.out, captured.err)


def test_verify_requires_plan(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["verify"])

    captured = capsys.readouterr()
    assert exc_info.value.code == EXIT_USAGE
    assert "the following arguments are required: --plan" in captured.err
    assert_no_traceback(captured.out, captured.err)


def test_verify_rejects_a_missing_plan(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "missing.yaml"

    exit_code = main(["verify", "--plan", str(plan)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err == f"agentverify: error: plan does not exist: {plan.resolve()}\n"
    assert_no_traceback(captured.out, captured.err)


def test_verify_rejects_a_directory_plan(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    exit_code = main(["verify", "--plan", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert captured.out == ""
    assert captured.err == (
        f"agentverify: error: plan must be a regular file: {tmp_path.resolve()}\n"
    )
    assert_no_traceback(captured.out, captured.err)
