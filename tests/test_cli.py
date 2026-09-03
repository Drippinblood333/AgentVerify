"""Observable CLI validation, exit-code, and no-start-on-invalid-input tests."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest
from pytest import CaptureFixture

from donewitness import __version__
from donewitness.cli import EXIT_UNKNOWN, EXIT_USAGE, main
from donewitness.isolation import DockerIsolationPreflight

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
    isolation: str | None = None,
    isolation_image: str | None = None,
    output_format: str | None = None,
) -> list[str]:
    isolation_args: list[str] = []
    if isolation is not None:
        isolation_args.extend(("--isolation", isolation))
    if isolation_image is not None:
        isolation_args.extend(("--isolation-image", isolation_image))
    if output_format is not None:
        isolation_args.extend(("--output-format", output_format))
    return [
        "verify",
        "--plan",
        str(plan),
        "--base-url",
        base_url,
        "--run-dir",
        str(run_dir),
        *isolation_args,
        "--app-command",
        *app_command,
    ]


def test_help_exits_successfully(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert "usage: donewitness" in captured.out
    assert "verify" in captured.out
    assert captured.err == ""


def test_version_exits_successfully(capsys: CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    captured = capsys.readouterr()
    assert exc_info.value.code == 0
    assert captured.out == f"DoneWitness {__version__}\n"
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
            output_format="json",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert expected_error in captured.err
    assert captured.out == ""
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
            app_command=["donewitness-executable-that-does-not-exist"],
            output_format="json",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_UNKNOWN
    summary = json.loads(captured.out)
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1
    assert summary["output_schema_version"] == 1
    assert summary["verdict"] == "UNKNOWN"
    assert summary["completed"] is False
    assert summary["exit_code"] == EXIT_UNKNOWN
    assert summary["receipt_schema_version"] == 4
    assert Path(summary["receipt_json_path"]) == (run_dir / "receipt.json").resolve()
    assert captured.err == ""
    assert (run_dir / "receipt.json").is_file()
    assert (run_dir / "receipt.txt").is_file()
    assert (run_dir / "evidence-manifest.json").is_file()
    assert_no_traceback(captured.out, captured.err)


def test_docker_isolation_requires_image_and_never_starts_application(
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
            isolation="docker",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "requires --isolation-image" in captured.err
    assert not marker.exists()


def test_isolation_image_is_rejected_in_direct_mode_without_startup(
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
            isolation_image="python:3.12-slim",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "only valid with --isolation docker" in captured.err
    assert not marker.exists()


def test_docker_isolation_unavailable_is_exit_2_before_startup(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    marker = tmp_path / "started.txt"
    monkeypatch.setattr("donewitness.isolation.shutil.which", lambda executable: None)

    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=tmp_path / "run",
            app_command=marker_command(marker),
            isolation="docker",
            isolation_image="python:3.12-slim",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "Docker executable is unavailable" in captured.err
    assert not marker.exists()


def test_docker_run_directory_inside_source_is_exit_2_before_docker_lookup(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    marker = tmp_path / "started.txt"
    looked_up = False

    def lookup(_: str) -> str | None:
        nonlocal looked_up
        looked_up = True
        return None

    monkeypatch.setattr("donewitness.isolation.shutil.which", lookup)
    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=Path.cwd() / ".donewitness-m8-must-not-create",
            app_command=marker_command(marker),
            isolation="docker",
            isolation_image="python:3.12-slim",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "outside the source root" in captured.err
    assert not looked_up
    assert not marker.exists()


@pytest.mark.parametrize(
    "base_url",
    ["http://localhost:8765", "http://[::1]:8765", "http://127.0.0.1"],
)
def test_docker_base_url_boundary_is_exit_2_before_startup(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    base_url: str,
) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    marker = tmp_path / "started.txt"

    exit_code = main(
        verify_args(
            plan=plan,
            run_dir=tmp_path / "run",
            app_command=marker_command(marker),
            base_url=base_url,
            isolation="docker",
            isolation_image="python:3.12-slim",
        )
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "Docker isolation base URL" in captured.err
    assert not marker.exists()


def test_docker_preexisting_endpoint_is_rejected_before_container_start(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = tmp_path / "plan.json"
    write_plan(plan)
    marker = tmp_path / "started.txt"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        preflight = DockerIsolationPreflight(
            docker_executable="docker",
            docker_server_version="28.3.1",
            image_reference="python:3.12-slim",
            image_id=f"sha256:{'a' * 64}",
            source_root=Path.cwd(),
            port=port,
        )
        monkeypatch.setattr(
            "donewitness.run.preflight_docker_isolation",
            lambda **kwargs: preflight,
        )

        def unexpected_start(*args: object, **kwargs: object) -> None:
            raise AssertionError("container startup must not occur")

        monkeypatch.setattr(
            "donewitness.run.DockerManagedApplication.start", unexpected_start
        )
        run_dir = tmp_path / "run"
        exit_code = main(
            verify_args(
                plan=plan,
                run_dir=run_dir,
                app_command=marker_command(marker),
                base_url=f"http://127.0.0.1:{port}",
                isolation="docker",
                isolation_image="python:3.12-slim",
            )
        )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE
    assert "already accepting connections" in captured.err
    assert not run_dir.exists()
    assert not marker.exists()
