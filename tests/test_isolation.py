"""Docker isolation preflight and fixed-profile construction tests."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from agentverify.application import ApplicationStartError
from agentverify.isolation import (
    DOCKER_CPU_LIMIT,
    DOCKER_MEMORY_LIMIT,
    DOCKER_PID_LIMIT,
    DOCKER_SHM_LIMIT,
    DOCKER_TMPFS_LIMIT_BYTES,
    DOCKER_USER,
    DockerIsolationConfigurationError,
    DockerIsolationPreflight,
    DockerManagedApplication,
    _docker_run_argv,
    preflight_docker_isolation,
)

IMAGE_ID = f"sha256:{'a' * 64}"


def result(
    argv: Sequence[str],
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(tuple(argv), returncode, stdout, stderr)


def install_fake_docker(
    monkeypatch: pytest.MonkeyPatch,
    *,
    server_version: str = "28.3.1",
    server_os: str = "linux",
    daemon_returncode: int = 0,
    image_returncode: int = 0,
    image_id: str = IMAGE_ID,
    volumes: object = None,
    docker_endpoint: str = "unix:///var/run/docker.sock",
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.setattr(
        "agentverify.isolation.shutil.which", lambda executable: "/usr/bin/docker"
    )

    def fake_run(argv: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        command = tuple(argv)
        calls.append(command)
        if command[1] == "version":
            return result(
                command,
                returncode=daemon_returncode,
                stdout=f"{server_version}\t{server_os}\n",
                stderr="daemon unavailable" if daemon_returncode else "",
            )
        if command[1:3] == ("image", "inspect"):
            payload = [{"Id": image_id, "Config": {"Volumes": volumes}}]
            return result(
                command,
                returncode=image_returncode,
                stdout=json.dumps(payload) if not image_returncode else "",
                stderr="No such image" if image_returncode else "",
            )
        if command[1:3] == ("context", "inspect"):
            return result(command, stdout=json.dumps(docker_endpoint))
        raise AssertionError(f"unexpected Docker command: {command}")

    monkeypatch.setattr("agentverify.isolation.subprocess.run", fake_run)
    return calls


def valid_paths(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "source"
    source_root.mkdir()
    return source_root, tmp_path / "run"


def test_preflight_resolves_local_image_and_inspects_supported_linux_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, run_dir = valid_paths(tmp_path)
    calls = install_fake_docker(monkeypatch)

    preflight = preflight_docker_isolation(
        image_reference="python:3.12-slim",
        base_url="http://127.0.0.1:8765",
        run_dir=run_dir,
        source_root=source_root,
    )

    assert preflight.docker_server_version == "28.3.1"
    assert preflight.image_reference == "python:3.12-slim"
    assert preflight.image_id == IMAGE_ID
    assert preflight.port == 8765
    assert calls == [
        (
            "/usr/bin/docker",
            "version",
            "--format",
            "{{.Server.Version}}\t{{.Server.Os}}",
        ),
        (
            "/usr/bin/docker",
            "context",
            "inspect",
            "--format",
            "{{json .Endpoints.docker.Host}}",
        ),
        ("/usr/bin/docker", "image", "inspect", "python:3.12-slim"),
    ]


@pytest.mark.parametrize(
    ("server_version", "server_os", "daemon_returncode", "expected"),
    [
        ("28.0.0", "linux", 1, "daemon is unavailable"),
        ("27.5.1", "linux", 0, "version 28 or newer"),
        ("28.0.0", "windows", 0, "Linux containers"),
        ("unexpected", "linux", 0, "unsupported or unrecognized"),
    ],
)
def test_preflight_rejects_unsupported_docker_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_version: str,
    server_os: str,
    daemon_returncode: int,
    expected: str,
) -> None:
    source_root, run_dir = valid_paths(tmp_path)
    install_fake_docker(
        monkeypatch,
        server_version=server_version,
        server_os=server_os,
        daemon_returncode=daemon_returncode,
    )

    with pytest.raises(DockerIsolationConfigurationError, match=expected):
        preflight_docker_isolation(
            image_reference="python:3.12-slim",
            base_url="http://127.0.0.1:8765",
            run_dir=run_dir,
            source_root=source_root,
        )


def test_preflight_rejects_missing_executable_without_invoking_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, run_dir = valid_paths(tmp_path)
    monkeypatch.setattr("agentverify.isolation.shutil.which", lambda executable: None)

    with pytest.raises(DockerIsolationConfigurationError, match="executable is unavailable"):
        preflight_docker_isolation(
            image_reference="python:3.12-slim",
            base_url="http://127.0.0.1:8765",
            run_dir=run_dir,
            source_root=source_root,
        )


def test_preflight_rejects_remote_docker_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, run_dir = valid_paths(tmp_path)
    install_fake_docker(monkeypatch, docker_endpoint="ssh://builder@example.com")

    with pytest.raises(DockerIsolationConfigurationError, match="remote Docker hosts"):
        preflight_docker_isolation(
            image_reference="python:3.12-slim",
            base_url="http://127.0.0.1:8765",
            run_dir=run_dir,
            source_root=source_root,
        )


def test_preflight_rejects_remote_docker_host_environment_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, run_dir = valid_paths(tmp_path)
    install_fake_docker(monkeypatch)
    monkeypatch.setenv("DOCKER_HOST", "tcp://builder.example:2376")

    with pytest.raises(DockerIsolationConfigurationError, match="remote DOCKER_HOST"):
        preflight_docker_isolation(
            image_reference="python:3.12-slim",
            base_url="http://127.0.0.1:8765",
            run_dir=run_dir,
            source_root=source_root,
        )


@pytest.mark.parametrize(
    ("image_returncode", "image_id", "volumes", "expected"),
    [
        (1, IMAGE_ID, None, "not available locally"),
        (0, "python:3.12-slim", None, "unsupported local image ID"),
        (0, IMAGE_ID, {"/data": {}}, "declares writable VOLUME"),
    ],
)
def test_preflight_rejects_unusable_local_image_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    image_returncode: int,
    image_id: str,
    volumes: object,
    expected: str,
) -> None:
    source_root, run_dir = valid_paths(tmp_path)
    install_fake_docker(
        monkeypatch,
        image_returncode=image_returncode,
        image_id=image_id,
        volumes=volumes,
    )

    with pytest.raises(DockerIsolationConfigurationError, match=expected):
        preflight_docker_isolation(
            image_reference="python:3.12-slim",
            base_url="http://127.0.0.1:8765",
            run_dir=run_dir,
            source_root=source_root,
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8765",
        "http://[::1]:8765",
        "http://127.0.0.2:8765",
        "http://example.com:8765",
        "http://127.0.0.1",
    ],
)
def test_preflight_requires_exact_ipv4_loopback_and_explicit_port(
    tmp_path: Path,
    base_url: str,
) -> None:
    source_root, run_dir = valid_paths(tmp_path)

    with pytest.raises(DockerIsolationConfigurationError, match="base URL"):
        preflight_docker_isolation(
            image_reference="python:3.12-slim",
            base_url=base_url,
            run_dir=run_dir,
            source_root=source_root,
        )


def test_preflight_rejects_run_directory_inside_source_before_docker_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, _ = valid_paths(tmp_path)
    looked_up = False

    def lookup(_: str) -> str | None:
        nonlocal looked_up
        looked_up = True
        return None

    monkeypatch.setattr("agentverify.isolation.shutil.which", lookup)
    with pytest.raises(DockerIsolationConfigurationError, match="outside the source root"):
        preflight_docker_isolation(
            image_reference="python:3.12-slim",
            base_url="http://127.0.0.1:8765",
            run_dir=source_root / ".agentverify" / "run",
            source_root=source_root,
        )
    assert not looked_up


def test_preflight_rejects_filesystem_root_and_comma_mount_path(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    with pytest.raises(DockerIsolationConfigurationError, match="filesystem or drive root"):
        preflight_docker_isolation(
            image_reference="python:3.12-slim",
            base_url="http://127.0.0.1:8765",
            run_dir=run_dir,
            source_root=Path(Path.cwd().anchor),
        )

    comma_source = tmp_path / "source,ambiguous"
    comma_source.mkdir()
    with pytest.raises(DockerIsolationConfigurationError, match="represented safely"):
        preflight_docker_isolation(
            image_reference="python:3.12-slim",
            base_url="http://127.0.0.1:8765",
            run_dir=run_dir,
            source_root=comma_source,
        )


def test_fixed_docker_argv_pins_image_and_controls_all_boundaries(tmp_path: Path) -> None:
    source_root, _ = valid_paths(tmp_path)
    preflight = DockerIsolationPreflight(
        docker_executable="/usr/bin/docker",
        docker_server_version="28.3.1",
        image_reference="python:3.12-slim",
        image_id=IMAGE_ID,
        source_root=source_root.resolve(),
        port=8765,
    )
    app_command = ("python", "examples/greeting_app.py", "--message", "hello; $(id)")

    argv = _docker_run_argv(
        preflight=preflight,
        app_command=app_command,
        container_name="agentverify-fixed-app",
        network_name="agentverify-fixed-net",
    )

    assert argv[-len(app_command) - 1 :] == (
        "python",
        IMAGE_ID,
        *app_command[1:],
    )
    assert argv[argv.index("--pull") + 1] == "never"
    assert argv[argv.index("--network") + 1] == "agentverify-fixed-net"
    assert argv[argv.index("--publish") + 1] == "127.0.0.1:8765:8765/tcp"
    assert argv[argv.index("--user") + 1] == DOCKER_USER
    assert argv[argv.index("--memory") + 1] == DOCKER_MEMORY_LIMIT
    assert argv[argv.index("--cpus") + 1] == DOCKER_CPU_LIMIT
    assert argv[argv.index("--pids-limit") + 1] == str(DOCKER_PID_LIMIT)
    assert argv[argv.index("--shm-size") + 1] == DOCKER_SHM_LIMIT
    assert argv[argv.index("--tmpfs") + 1] == (
        f"/tmp:rw,nosuid,nodev,size={DOCKER_TMPFS_LIMIT_BYTES}"
    )
    assert argv[argv.index("--mount") + 1] == (
        f"type=bind,source={source_root.resolve()},target=/workspace,readonly"
    )
    assert "--read-only" in argv
    assert "--cap-drop" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"
    assert "--no-healthcheck" in argv
    assert "--privileged" not in argv
    assert "--cap-add" not in argv
    assert "--device" not in argv
    assert "/var/run/docker.sock" not in " ".join(argv)
    assert "host" not in {argv[argv.index("--network") + 1]}


def test_start_failure_after_network_creation_cleans_only_exact_managed_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, _ = valid_paths(tmp_path)
    preflight = DockerIsolationPreflight(
        docker_executable="/usr/bin/docker",
        docker_server_version="28.3.1",
        image_reference="python:3.12-slim",
        image_id=IMAGE_ID,
        source_root=source_root.resolve(),
        port=8765,
    )
    cleanup_calls: list[tuple[str, ...]] = []

    def successful_network(
        argv: Sequence[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        return result(argv, stdout="managed-network-id\n")

    def failed_popen(*args: object, **kwargs: object) -> None:
        raise OSError("simulated Docker client launch failure")

    def cleanup(
        argv: Sequence[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        cleanup_calls.append(tuple(argv))
        return result(argv)

    monkeypatch.setattr("agentverify.isolation._run_cli", successful_network)
    monkeypatch.setattr("agentverify.isolation.subprocess.Popen", failed_popen)
    monkeypatch.setattr("agentverify.isolation._run_cleanup_cli", cleanup)
    monkeypatch.setattr(
        "agentverify.isolation._inspect_container_state",
        lambda docker, name: "absent",
    )
    monkeypatch.setattr(
        "agentverify.isolation._inspect_network_state",
        lambda docker, name: "absent",
    )

    with pytest.raises(ApplicationStartError, match="could not start"):
        DockerManagedApplication.start(
            preflight,
            ("python", "examples/greeting_app.py"),
            max_log_bytes=4096,
        )

    removed_container = next(
        call[-1] for call in cleanup_calls if call[1:4] == ("container", "rm", "--force")
    )
    removed_network = next(
        call[-1] for call in cleanup_calls if call[1:3] == ("network", "rm")
    )
    assert re.fullmatch(r"agentverify-[0-9a-f]{32}-app", removed_container)
    assert re.fullmatch(r"agentverify-[0-9a-f]{32}-net", removed_network)
