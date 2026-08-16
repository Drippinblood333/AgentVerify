"""Observable CLI validation, exit-code, and no-start-on-invalid-input tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pytest import CaptureFixture

from agentverify import __version__
from agentverify.cli import EXIT_UNKNOWN, EXIT_USAGE, main

VALID_V1_PLAN = {
    "schema_version": 1,
    "task": "Implement password reset",
    "criteria": [{"id": "AC-001", "description": "Reset is available"}],
}
VALID_V2_PLAN = {
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


def assert_no_traceback(stdout: str, stderr: str) -> None:
    assert "Traceback" not in stdout
    assert "Traceback" not in stderr


def write_plan(path: Path, payload: object = VALID_V2_PLAN) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def marker_command(marker: Path) -> list[str]:
    return [
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).write_text('started')",
    ]


def verify_args(
    *,
    plan: Path,
    run_dir: Path,
    app_command: list[str],
    base_url: str = "http://127.0.0.1:8765",
) -> list[str]:
    return [
        "verify",
        "--plan",
        str(plan),
        "--base-url",
        base_url,
        "--run-dir",
        str(run_dir),
        "--app-command",
        *app_command,
    ]


def test_help_exits_successfully(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "usage: agentverify" in captured.out
    assert "verify" in captured.out
    assert captured.err == ""


def test_version_exits_successfully(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.out == f"AgentVerify {__version__}\n"
    assert captured.err == ""


def test_verify_requires_all_run_inputs(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["verify"])

    captured = capsys.readouterr()
    assert exc_info.value.code == EXIT_USAGE
    assert "required" in captured.err
    assert_no_traceback(captured.out, captured.err)


@pytest.mark.parametrize(
    ("content", "expected_error"),
    [
        ("{", "malformed JSON at line 1"),
        (json.dumps({**VALID_V2_PLAN, "schema_version": 3}), "schema_version"),
    ],
)
def test_invalid_plan_never_starts_application(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    content: str,
    expected_error: str,
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(content, encoding="utf-8")
    marker = tmp_path / "started.txt"

    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=tmp_path / "run",
            app_command=marker_command(marker),
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert expected_error in captured.err
    assert not marker.exists()
    assert_no_traceback(captured.out, captured.err)


def test_missing_plan_never_starts_application(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "missing.json"
    marker = tmp_path / "started.txt"

    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=tmp_path / "run",
            app_command=marker_command(marker),
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "plan does not exist" in captured.err
    assert not marker.exists()


def test_directory_plan_never_starts_application(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    marker = tmp_path / "started.txt"

    exit_code = main(
        verify_args(
            plan=tmp_path,
            run_dir=tmp_path / "run",
            app_command=marker_command(marker),
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "plan must be a regular file" in captured.err
    assert not marker.exists()


def test_invalid_utf8_plan_never_starts_application(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "invalid.json"
    plan.write_bytes(b"\xff")
    marker = tmp_path / "started.txt"

    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=tmp_path / "run",
            app_command=marker_command(marker),
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "not valid UTF-8" in captured.err
    assert not marker.exists()


def test_plan_v1_is_not_executable_and_never_starts_application(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "plan-v1.json"
    write_plan(plan, VALID_V1_PLAN)
    marker = tmp_path / "started.txt"

    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=tmp_path / "run",
            app_command=marker_command(marker),
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "requires Plan v2" in captured.err
    assert not marker.exists()


def test_external_base_url_never_starts_application(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    marker = tmp_path / "started.txt"

    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=tmp_path / "run",
            app_command=marker_command(marker),
            base_url="https://example.com",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "loopback" in captured.err
    assert not marker.exists()


def test_empty_application_command_is_invalid(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)

    exit_code = main(
        verify_args(plan=plan, run_dir=tmp_path / "run", app_command=[])
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "application command must not be empty" in captured.err


def test_invalid_startup_timeout_is_argparse_usage_error(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)

    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "verify",
                "--plan",
                str(plan),
                "--base-url",
                "http://127.0.0.1:8765",
                "--run-dir",
                str(tmp_path / "run"),
                "--startup-timeout-ms",
                "99",
                "--app-command",
                sys.executable,
            ]
        )

    captured = capsys.readouterr()
    assert exc_info.value.code == EXIT_USAGE
    assert "100 to 60000" in captured.err


def test_nonempty_run_directory_never_starts_application(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "existing.txt").write_text("keep", encoding="utf-8")
    marker = tmp_path / "started.txt"

    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=run_dir,
            app_command=marker_command(marker),
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "run directory must be empty" in captured.err
    assert not marker.exists()
    assert (run_dir / "existing.txt").read_text(encoding="utf-8") == "keep"


def test_regular_file_run_directory_never_starts_application(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    run_path = tmp_path / "run-file"
    run_path.write_text("keep", encoding="utf-8")
    marker = tmp_path / "started.txt"

    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=run_path,
            app_command=marker_command(marker),
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "must be a directory" in captured.err
    assert not marker.exists()
    assert run_path.read_text(encoding="utf-8") == "keep"


def test_application_start_failure_produces_unknown_receipt_without_traceback(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    run_dir = tmp_path / "run"

    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=run_dir,
            app_command=["agentverify-executable-that-does-not-exist"],
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    assert "Verdict: UNKNOWN" in captured.out
    assert captured.err == ""
    assert (run_dir / "receipt.json").is_file()
    assert (run_dir / "receipt.txt").is_file()
    assert (run_dir / "evidence-manifest.json").is_file()
    assert_no_traceback(captured.out, captured.err)
