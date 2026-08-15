"""Observable behavior tests for the command-line interface."""

import json
from pathlib import Path

import pytest
from pytest import CaptureFixture

from agentverify import __version__
from agentverify.cli import EXIT_SUCCESS, EXIT_USAGE, main

VALID_PLAN = {
    "schema_version": 1,
    "task": "Implement password reset",
    "criteria": [
        {
            "id": "AC-001",
            "description": "A user can request a password reset",
        },
        {
            "id": "AC-002",
            "description": "Invalid reset tokens are rejected",
        },
    ],
}


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
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(VALID_PLAN), encoding="utf-8")

    exit_code = main(["verify", "--plan", str(plan)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_SUCCESS
    assert captured.out == (
        "Verification plan is valid.\n"
        "\n"
        "Task: Implement password reset\n"
        "Criteria: 2\n"
        "Schema version: 1\n"
        "Plan digest: "
        "sha256:b29aa3da660a1ad6474ed475abf617a2dc79f77b686724eb6a2b8ee1e3af1e91\n"
        "\n"
        "Verification execution is not implemented yet.\n"
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


def test_verify_rejects_malformed_json(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "malformed.json"
    plan.write_text("{", encoding="utf-8")

    exit_code = main(["verify", "--plan", str(plan)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert captured.out == ""
    assert "malformed JSON at line 1" in captured.err
    assert_no_traceback(captured.out, captured.err)


def test_verify_rejects_invalid_utf8(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "invalid-utf8.json"
    plan.write_bytes(b"\xff")

    exit_code = main(["verify", "--plan", str(plan)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert captured.out == ""
    assert "plan is not valid UTF-8" in captured.err
    assert_no_traceback(captured.out, captured.err)


def test_verify_rejects_invalid_schema(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "invalid.json"
    invalid_plan = dict(VALID_PLAN, schema_version=3)
    plan.write_text(json.dumps(invalid_plan), encoding="utf-8")

    exit_code = main(["verify", "--plan", str(plan)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert captured.out == ""
    assert "schema_version" in captured.err
    assert_no_traceback(captured.out, captured.err)


def test_verify_validates_v2_without_launching_browser(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "browser-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "task": "Verify greeting flow",
                "criteria": [
                    {
                        "id": "AC-001",
                        "description": "Greeting appears",
                        "procedure": {
                            "type": "browser",
                            "steps": [
                                {"type": "navigate", "path": "/"},
                                {"type": "assert_visible", "selector": "#message"},
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["verify", "--plan", str(plan)])

    captured = capsys.readouterr()
    assert exit_code == EXIT_SUCCESS
    assert "Schema version: 2" in captured.out
    assert captured.out.endswith("Verification execution is not implemented yet.\n")
    assert captured.err == ""
