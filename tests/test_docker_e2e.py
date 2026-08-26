"""Real Linux Docker isolation, boundary, receipt, and cleanup tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from pytest import CaptureFixture

from agentverify.cli import EXIT_FAIL, EXIT_PASS, EXIT_SUCCESS, EXIT_UNKNOWN, main
from agentverify.domain import Verdict
from agentverify.evidence import EvidenceKind, EvidenceStore
from agentverify.isolation import (
    DOCKER_PROFILE_NAME,
    DockerManagedApplication,
    preflight_docker_isolation,
)
from agentverify.plan import load_plan, plan_digest
from agentverify.receipt import DockerExecutionMetadata, ProofReceiptV3, load_receipt

REPOSITORY_ROOT = Path(__file__).parents[1]
PASS_PLAN = REPOSITORY_ROOT / "examples" / "greeting.plan.json"
SAMPLE_APP = REPOSITORY_ROOT / "examples" / "greeting_app.py"
DOCKER_IMAGE = "python:3.12-slim"
MANAGED_LABEL = "agentverify.managed=true"


def docker_cli(
    docker: str,
    args: Sequence[str],
    *,
    timeout: float = 20,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (docker, *args),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        check=False,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def docker_executable() -> str:
    required = os.environ.get("AGENTVERIFY_REQUIRE_DOCKER") == "1"

    def unavailable(reason: str) -> None:
        if required:
            pytest.fail(reason)
        pytest.skip(reason)

    docker = shutil.which("docker")
    if docker is None:
        unavailable("real Docker tests require the Docker CLI")
        raise AssertionError("unreachable")
    version = docker_cli(
        docker,
        ("version", "--format", "{{.Server.Version}}\t{{.Server.Os}}"),
    )
    if version.returncode != 0:
        unavailable("real Docker tests require a reachable Docker daemon")
    fields = version.stdout.strip().split("\t")
    if len(fields) != 2 or fields[1].lower() != "linux":
        unavailable("real Docker tests require Linux-container mode")
    match = re.match(r"^(\d+)(?:\.|$)", fields[0])
    if match is None or int(match.group(1)) < 28:
        unavailable("real Docker tests require Docker Engine server 28+")
    image = docker_cli(docker, ("image", "inspect", DOCKER_IMAGE))
    if image.returncode != 0:
        unavailable(f"real Docker tests require local image {DOCKER_IMAGE}")
    return docker


def unused_tcp_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def managed_resources(docker: str) -> tuple[frozenset[str], frozenset[str]]:
    containers = docker_cli(
        docker,
        (
            "container",
            "ls",
            "--all",
            "--filter",
            f"label={MANAGED_LABEL}",
            "--format",
            "{{.Names}}",
        ),
    )
    networks = docker_cli(
        docker,
        ("network", "ls", "--filter", f"label={MANAGED_LABEL}", "--format", "{{.Name}}"),
    )
    assert containers.returncode == 0, containers.stderr
    assert networks.returncode == 0, networks.stderr
    return (
        frozenset(containers.stdout.splitlines()),
        frozenset(networks.stdout.splitlines()),
    )


def docker_verify_args(
    *,
    plan: Path,
    port: int,
    run_dir: Path,
    app_command: Sequence[str],
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
        "--isolation",
        "docker",
        "--isolation-image",
        DOCKER_IMAGE,
        "--app-command",
        *app_command,
    ]


def direct_verify_args(*, port: int, run_dir: Path) -> list[str]:
    return [
        "verify",
        "--plan",
        str(PASS_PLAN),
        "--base-url",
        f"http://127.0.0.1:{port}",
        "--run-dir",
        str(run_dir),
        "--app-command",
        sys.executable,
        str(SAMPLE_APP),
        "--port",
        str(port),
    ]


def greeting_container_command(port: int, *extra: str) -> tuple[str, ...]:
    return (
        "python",
        "examples/greeting_app.py",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        *extra,
    )


def load_v3(run_dir: Path) -> ProofReceiptV3:
    receipt = load_receipt(run_dir / "receipt.json")
    assert isinstance(receipt, ProofReceiptV3)
    return receipt


def browser_observation_bytes(run_dir: Path) -> tuple[bytes, ...]:
    store = EvidenceStore(run_dir)
    manifest = store.load_manifest()
    return tuple(
        (run_dir / artifact.relative_path).read_bytes()
        for artifact in manifest.artifacts
        if artifact.kind is EvidenceKind.BROWSER_OBSERVATION
    )


def test_direct_and_docker_pass_have_stable_semantics_and_v3_inspection(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    docker_executable: str,
) -> None:
    before = managed_resources(docker_executable)
    direct_port = unused_tcp_port()
    docker_port = unused_tcp_port()
    direct_run = tmp_path / "direct-run"
    docker_run = tmp_path / "docker-run"

    assert main(direct_verify_args(port=direct_port, run_dir=direct_run)) == EXIT_PASS
    capsys.readouterr()
    assert (
        main(
            docker_verify_args(
                plan=PASS_PLAN,
                port=docker_port,
                run_dir=docker_run,
                app_command=greeting_container_command(docker_port),
            )
        )
        == EXIT_PASS
    )
    docker_output = capsys.readouterr()

    direct_receipt = load_v3(direct_run)
    docker_receipt = load_v3(docker_run)
    assert docker_output.err == ""
    assert docker_receipt.overall_verdict is Verdict.PASS
    assert docker_receipt.completed
    assert docker_receipt.plan_digest == plan_digest(load_plan(PASS_PLAN))
    assert isinstance(docker_receipt.execution, DockerExecutionMetadata)
    assert docker_receipt.execution.isolation_profile == DOCKER_PROFILE_NAME
    assert docker_receipt.execution.image_reference == DOCKER_IMAGE
    assert docker_receipt.execution.image_id.startswith("sha256:")
    assert [
        (criterion.criterion_id, criterion.verdict, criterion.reason)
        for criterion in direct_receipt.criteria
    ] == [
        (criterion.criterion_id, criterion.verdict, criterion.reason)
        for criterion in docker_receipt.criteria
    ]
    assert browser_observation_bytes(direct_run) == browser_observation_bytes(docker_run)

    assert main(["inspect", "--run-dir", str(docker_run)]) == EXIT_SUCCESS
    inspected = capsys.readouterr()
    assert "Integrity: OK" in inspected.out
    assert "Receipt schema: 3" in inspected.out
    assert managed_resources(docker_executable) == before


def test_isolated_assertion_fail_remains_fail_and_cleans_resources(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    docker_executable: str,
) -> None:
    before = managed_resources(docker_executable)
    fail_plan = tmp_path / "fail.plan.json"
    payload = json.loads(PASS_PLAN.read_text(encoding="utf-8"))
    payload["criteria"][0]["procedure"]["steps"][-1] = {
        "type": "assert_visible",
        "selector": "#never-visible",
    }
    fail_plan.write_text(json.dumps(payload), encoding="utf-8")
    port = unused_tcp_port()
    run_dir = tmp_path / "fail-run"

    exit_code = main(
        docker_verify_args(
            plan=fail_plan,
            port=port,
            run_dir=run_dir,
            app_command=greeting_container_command(port),
        )
    )

    capsys.readouterr()
    receipt = load_v3(run_dir)
    assert exit_code == EXIT_FAIL
    assert receipt.overall_verdict is Verdict.FAIL
    assert receipt.completed
    assert receipt.criteria[0].verdict is Verdict.FAIL
    assert managed_resources(docker_executable) == before


@pytest.mark.parametrize(
    ("app_command_factory", "timeout_ms", "expected_reason"),
    [
        (
            lambda port: greeting_container_command(
                port, "--startup-delay-ms", "5000"
            ),
            200,
            "Docker application readiness timed out",
        ),
        (
            lambda port: ("agentverify-command-that-does-not-exist",),
            2000,
            "Docker application exited before readiness",
        ),
    ],
)
def test_isolated_runtime_uncertainty_is_reviewable_and_cleans_partial_resources(
    tmp_path: Path,
    capsys: CaptureFixture[str],
    docker_executable: str,
    app_command_factory: Callable[[int], tuple[str, ...]],
    timeout_ms: int,
    expected_reason: str,
) -> None:
    before = managed_resources(docker_executable)
    port = unused_tcp_port()
    run_dir = tmp_path / f"unknown-{timeout_ms}"
    exit_code = main(
        docker_verify_args(
            plan=PASS_PLAN,
            port=port,
            run_dir=run_dir,
            app_command=app_command_factory(port),
            startup_timeout_ms=timeout_ms,
        )
    )

    capsys.readouterr()
    receipt = load_v3(run_dir)
    assert exit_code == EXIT_UNKNOWN
    assert receipt.overall_verdict is Verdict.UNKNOWN
    assert not receipt.completed
    assert receipt.criteria[0].reason == expected_reason
    assert managed_resources(docker_executable) == before


def test_runtime_inspection_proves_documented_docker_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    docker_executable: str,
) -> None:
    before = managed_resources(docker_executable)
    port = unused_tcp_port()
    run_dir = tmp_path / "outside-source-run"
    canary = tmp_path / "outside-source-canary"
    canary.write_text("host only", encoding="utf-8")
    source_marker = REPOSITORY_ROOT / ".agentverify-m8-source-write-marker"
    source_marker.unlink(missing_ok=True)
    monkeypatch.setenv("AGENTVERIFY_M8_HOST_SECRET", "must-not-enter-container")
    script = """
import json, os, socket, sys
def write_succeeds(path):
    try:
        with open(path, "w", encoding="utf-8") as target:
            target.write("marker")
        return True
    except OSError:
        return False
checks = {
    "source_write": write_succeeds("/workspace/.agentverify-m8-source-write-marker"),
    "root_write": write_succeeds("/agentverify-m8-root-write-marker"),
    "tmp_write": write_succeeds("/tmp/agentverify-m8-tmp-write-marker"),
    "outside_canary_visible": os.path.exists(sys.argv[2]),
    "run_dir_visible": os.path.exists(sys.argv[3]),
    "host_secret_present": "AGENTVERIFY_M8_HOST_SECRET" in os.environ,
    "docker_socket_present": os.path.exists("/var/run/docker.sock"),
}
print("BOUNDARY=" + json.dumps(checks, sort_keys=True), flush=True)
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("0.0.0.0", int(sys.argv[1])))
listener.listen()
while True:
    connection, _ = listener.accept()
    connection.close()
"""
    preflight = preflight_docker_isolation(
        image_reference=DOCKER_IMAGE,
        base_url=f"http://127.0.0.1:{port}",
        run_dir=run_dir,
        source_root=REPOSITORY_ROOT,
    )
    application: DockerManagedApplication | None = None
    try:
        application = DockerManagedApplication.start(
            preflight,
            ("python", "-c", script, str(port), str(canary), str(run_dir)),
            max_log_bytes=64 * 1024,
        )
        readiness = application.wait_for_readiness(
            f"http://127.0.0.1:{port}", timeout_ms=5000
        )
        assert readiness.ready, readiness.reason
        container_result = docker_cli(
            docker_executable,
            ("container", "inspect", application.container_name),
        )
        network_result = docker_cli(
            docker_executable,
            ("network", "inspect", application.network_name),
        )
        assert container_result.returncode == 0, container_result.stderr
        assert network_result.returncode == 0, network_result.stderr
        container = json.loads(container_result.stdout)[0]
        network = json.loads(network_result.stdout)[0]

        host_config = container["HostConfig"]
        assert host_config["ReadonlyRootfs"] is True
        assert "ALL" in host_config["CapDrop"]
        assert any(
            item.startswith("no-new-privileges")
            for item in host_config["SecurityOpt"]
        )
        assert host_config["Memory"] == 512 * 1024 * 1024
        assert host_config["NanoCpus"] == 1_000_000_000
        assert host_config["PidsLimit"] == 256
        assert host_config["ShmSize"] == 64 * 1024 * 1024
        assert host_config["NetworkMode"] == application.network_name
        tmpfs_options = set(host_config["Tmpfs"]["/tmp"].split(","))
        assert {"rw", "nosuid", "nodev"}.issubset(tmpfs_options)
        assert any(option in tmpfs_options for option in {"size=67108864", "size=65536k"})
        assert network["Internal"] is True
        assert container["Config"]["User"] == "65534:65534"
        assert container["Config"]["Entrypoint"] == ["python"]
        assert container["Config"]["Healthcheck"]["Test"] == ["NONE"]
        assert not any(
            item.startswith("AGENTVERIFY_M8_HOST_SECRET=")
            for item in container["Config"]["Env"]
        )
        assert set(container["NetworkSettings"]["Networks"]) == {
            application.network_name
        }
        assert set(host_config["PortBindings"]) == {f"{port}/tcp"}
        assert host_config["PortBindings"][f"{port}/tcp"] == [
            {"HostIp": "127.0.0.1", "HostPort": str(port)}
        ]
        mounts = container["Mounts"]
        bind_mounts = [mount for mount in mounts if mount["Type"] == "bind"]
        assert len(bind_mounts) == 1
        assert Path(bind_mounts[0]["Source"]).resolve() == REPOSITORY_ROOT.resolve()
        assert bind_mounts[0]["Destination"] == "/workspace"
        assert bind_mounts[0]["RW"] is False
        assert all(
            mount["Destination"] in {"/workspace", "/tmp"} for mount in mounts
        )
        assert str(run_dir) not in {mount["Source"] for mount in mounts}
        assert "/var/run/docker.sock" not in {
            mount["Destination"] for mount in mounts
        }
    finally:
        if application is not None:
            try:
                application.stop()
            finally:
                docker_cli(
                    docker_executable,
                    ("container", "rm", "--force", application.container_name),
                )
                docker_cli(
                    docker_executable,
                    ("network", "rm", application.network_name),
                )

    assert application is not None
    output = application.output().text
    boundary_line = next(
        line.removeprefix("BOUNDARY=")
        for line in output.splitlines()
        if line.startswith("BOUNDARY=")
    )
    boundary = json.loads(boundary_line)
    assert boundary == {
        "docker_socket_present": False,
        "host_secret_present": False,
        "outside_canary_visible": False,
        "root_write": False,
        "run_dir_visible": False,
        "source_write": False,
        "tmp_write": True,
    }
    assert not source_marker.exists()
    assert managed_resources(docker_executable) == before


def test_abnormal_container_termination_is_cleaned_exactly(
    tmp_path: Path,
    docker_executable: str,
) -> None:
    before = managed_resources(docker_executable)
    port = unused_tcp_port()
    preflight = preflight_docker_isolation(
        image_reference=DOCKER_IMAGE,
        base_url=f"http://127.0.0.1:{port}",
        run_dir=tmp_path / "abnormal-run",
        source_root=REPOSITORY_ROOT,
    )
    application = DockerManagedApplication.start(
        preflight,
        greeting_container_command(port),
        max_log_bytes=64 * 1024,
    )
    try:
        readiness = application.wait_for_readiness(
            f"http://127.0.0.1:{port}", timeout_ms=5000
        )
        assert readiness.ready, readiness.reason
        killed = docker_cli(
            docker_executable,
            ("container", "kill", application.container_name),
        )
        assert killed.returncode == 0, killed.stderr
        deadline = time.monotonic() + 5
        while application.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert application.note_unexpected_exit()
        application.stop()
    finally:
        docker_cli(
            docker_executable,
            ("container", "rm", "--force", application.container_name),
        )
        docker_cli(docker_executable, ("network", "rm", application.network_name))

    assert managed_resources(docker_executable) == before


@pytest.mark.skipif(os.name == "nt", reason="reliable SIGINT delivery requires POSIX")
def test_docker_cli_interrupt_creates_unknown_receipt_and_cleans_resources(
    tmp_path: Path,
    docker_executable: str,
) -> None:
    before = managed_resources(docker_executable)
    port = unused_tcp_port()
    run_dir = tmp_path / "interrupt-run"
    command = [
        sys.executable,
        "-m",
        "agentverify",
        *docker_verify_args(
            plan=PASS_PLAN,
            port=port,
            run_dir=run_dir,
            app_command=greeting_container_command(
                port, "--startup-delay-ms", "30000"
            ),
            startup_timeout_ms=60000,
        ),
    ]
    process = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + 15
        while managed_resources(docker_executable) == before and time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.1)
        assert process.poll() is None
        os.kill(process.pid, signal.SIGINT)
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == EXIT_UNKNOWN, (stdout, stderr)
        receipt = load_v3(run_dir)
        assert receipt.overall_verdict is Verdict.UNKNOWN
        assert not receipt.completed
        assert receipt.criteria[0].reason == "Verification was interrupted"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        current_containers, current_networks = managed_resources(docker_executable)
        for name in current_containers - before[0]:
            docker_cli(docker_executable, ("container", "rm", "--force", name))
        for name in current_networks - before[1]:
            docker_cli(docker_executable, ("network", "rm", name))

    assert managed_resources(docker_executable) == before
