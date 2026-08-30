"""Verify an AgentVerify wheel or sdist in an owned, isolated environment."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECTED_VERSION = "0.1.0.dev0"
EXPECTED_VERSION_OUTPUT = f"AgentVerify {EXPECTED_VERSION}"
EXPECTED_REQUIRES_PYTHON_PARTS = frozenset({">=3.12", "<3.15"})


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_agentverify(venv: Path) -> Path:
    return venv / (
        "Scripts/agentverify.exe" if os.name == "nt" else "bin/agentverify"
    )


def _run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    expected_exit: int = 0,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout,
        check=False,
    )
    if result.returncode != expected_exit:
        print(f"command failed ({result.returncode}): {argv!r}", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise RuntimeError(
            f"expected exit {expected_exit}, received {result.returncode}"
        )
    return result


def _unused_tcp_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _assert_installed_source(module_path: Path, venv: Path, repository: Path) -> None:
    resolved = module_path.resolve()
    if not resolved.is_relative_to(venv.resolve()):
        raise RuntimeError(f"AgentVerify did not import from the smoke environment: {resolved}")
    if resolved.is_relative_to((repository / "src").resolve()):
        raise RuntimeError(f"AgentVerify imported from repository source: {resolved}")


def _requires_python_is_supported_range(value: str) -> bool:
    return frozenset(part.strip() for part in value.split(",")) == (
        EXPECTED_REQUIRES_PYTHON_PARTS
    )


def smoke_distribution(artifact: Path, *, cli_only: bool) -> None:
    repository = Path(__file__).resolve().parents[1]
    resolved_artifact = artifact.resolve()
    if not resolved_artifact.is_file():
        raise ValueError(f"distribution artifact does not exist: {resolved_artifact}")

    with tempfile.TemporaryDirectory(prefix="agentverify-distribution-") as temporary:
        owned_root = Path(temporary).resolve()
        if owned_root.is_relative_to(repository):
            raise RuntimeError("smoke root must be outside the repository checkout")
        venv = owned_root / "venv"
        _run([sys.executable, "-m", "venv", str(venv)], cwd=owned_root, timeout=120)
        python = _venv_python(venv)
        _run(
            [str(python), "-m", "pip", "install", str(resolved_artifact)],
            cwd=owned_root,
            timeout=300,
        )
        _run([str(python), "-m", "pip", "check"], cwd=owned_root, timeout=60)
        agentverify = _venv_agentverify(venv)
        if not agentverify.is_file():
            raise RuntimeError(f"installed console entrypoint is missing: {agentverify}")

        version = _run(
            [str(agentverify), "--version"],
            cwd=owned_root,
            timeout=30,
        ).stdout.strip()
        if version != EXPECTED_VERSION_OUTPUT:
            raise RuntimeError(f"unexpected CLI version: {version!r}")
        _run(
            [str(agentverify), "--help"],
            cwd=owned_root,
            timeout=30,
        )
        probe = _run(
            [
                str(python),
                "-c",
                "import agentverify; from importlib.metadata import metadata; "
                "print(agentverify.__file__); print(agentverify.__version__); "
                "print(metadata('agentverify')['Requires-Python'])",
            ],
            cwd=owned_root,
            timeout=30,
        ).stdout.splitlines()
        if (
            len(probe) != 3
            or probe[1] != EXPECTED_VERSION
            or not _requires_python_is_supported_range(probe[2])
        ):
            raise RuntimeError(f"unexpected installed module probe: {probe!r}")
        installed_module = Path(probe[0])
        _assert_installed_source(installed_module, venv, repository)

        print(f"Artifact: {resolved_artifact.name}")
        print(f"Console entrypoint: {agentverify.resolve()}")
        print(f"Console --version: {version}")
        print("Console --help: OK")
        print(f"Installed module: {installed_module.resolve()}")
        print(f"Installed version: {probe[1]}")
        print(f"Installed Requires-Python: {probe[2]}")
        print("pip check: OK")
        if cli_only:
            print("CLI smoke: OK")
            return

        playwright_args = [str(python), "-m", "playwright", "install"]
        if sys.platform.startswith("linux"):
            playwright_args.append("--with-deps")
        playwright_args.append("chromium-headless-shell")
        _run(playwright_args, cwd=owned_root, timeout=600)

        workspace = owned_root / "workspace"
        workspace.mkdir()
        for filename in ("greeting.plan.json", "greeting_app.py"):
            shutil.copyfile(repository / "examples" / filename, workspace / filename)
        port = _unused_tcp_port()
        run_dir = workspace / "run"
        verification = _run(
            [
                str(agentverify),
                "verify",
                "--plan",
                "greeting.plan.json",
                "--base-url",
                f"http://127.0.0.1:{port}",
                "--run-dir",
                str(run_dir),
                "--output-format",
                "json",
                "--app-command",
                str(python),
                "greeting_app.py",
                "--port",
                str(port),
            ],
            cwd=workspace,
            timeout=120,
        )
        summary = json.loads(verification.stdout)
        if (
            summary.get("output_schema_version") != 1
            or summary.get("verdict") != "PASS"
            or summary.get("completed") is not True
            or summary.get("exit_code") != 0
            or summary.get("receipt_schema_version") != 4
        ):
            raise RuntimeError(f"unexpected machine summary: {summary!r}")
        for key in (
            "receipt_json_path",
            "receipt_text_path",
            "evidence_manifest_path",
        ):
            if not Path(summary[key]).is_file():
                raise RuntimeError(f"machine summary path is missing: {key}")
        inspection = _run(
            [
                str(agentverify),
                "inspect",
                "--run-dir",
                str(run_dir),
            ],
            cwd=workspace,
            timeout=30,
        )
        if "Integrity: OK" not in inspection.stdout:
            raise RuntimeError(f"unexpected inspection output: {inspection.stdout!r}")
        print(f"Machine summary: {verification.stdout.strip()}")
        print("Receipt schema: 4")
        print("Verification: PASS")
        print("Inspect: Integrity: OK")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--cli-only",
        action="store_true",
        help="stop after clean install, pip check, version, help, and import isolation",
    )
    args = parser.parse_args()
    smoke_distribution(args.artifact, cli_only=args.cli_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
