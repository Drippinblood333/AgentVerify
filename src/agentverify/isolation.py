"""Optional Docker isolation baseline for one locally verified application."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from agentverify.application import (
    ApplicationCleanupError,
    ApplicationStartError,
    ApplicationState,
    BoundedOutputDrain,
    ProcessOutput,
    ReadinessResult,
    ShutdownResult,
    endpoint_accepts_connection,
)

DOCKER_PROFILE_NAME = "agentverify-docker-baseline-v1"
DOCKER_MINIMUM_SERVER_MAJOR = 28
DOCKER_MEMORY_LIMIT = "512m"
DOCKER_CPU_LIMIT = "1.0"
DOCKER_PID_LIMIT = 256
DOCKER_SHM_LIMIT = "64m"
DOCKER_TMPFS_LIMIT_BYTES = 64 * 1024 * 1024
DOCKER_USER = "65534:65534"

_CLI_TIMEOUT_SECONDS = 10.0
_CLEANUP_TIMEOUT_SECONDS = 10.0
_IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class DockerIsolationConfigurationError(ValueError):
    """The requested Docker isolation route is invalid or unsupported."""


@dataclass(frozen=True, slots=True)
class DockerIsolationPreflight:
    """Validated immutable inputs used to start the Docker baseline."""

    docker_executable: str
    docker_server_version: str
    image_reference: str
    image_id: str
    source_root: Path
    port: int


def preflight_docker_isolation(
    *,
    image_reference: str,
    base_url: str,
    run_dir: Path,
    source_root: Path | None = None,
) -> DockerIsolationPreflight:
    """Validate the Docker host, image, URL, and filesystem boundary."""
    if not image_reference.strip():
        raise DockerIsolationConfigurationError(
            "Docker isolation requires --isolation-image"
        )
    if (
        "\x00" in image_reference
        or image_reference != image_reference.strip()
        or image_reference.startswith("-")
    ):
        raise DockerIsolationConfigurationError("Docker isolation image reference is invalid")

    resolved_source = _resolve_source_root(source_root or Path.cwd())
    _validate_run_directory_boundary(run_dir, source_root=resolved_source)
    port = _docker_base_url_port(base_url)

    docker_executable = shutil.which("docker")
    if docker_executable is None:
        raise DockerIsolationConfigurationError(
            "Docker executable is unavailable; install Docker Engine or Docker Desktop"
        )

    version_result = _run_cli(
        (
            docker_executable,
            "version",
            "--format",
            "{{.Server.Version}}",
        )
    )
    if version_result.returncode != 0:
        raise DockerIsolationConfigurationError(
            "Docker daemon is unavailable; start a reachable local Docker daemon"
        )
    server_version = version_result.stdout.strip()
    if not server_version:
        raise DockerIsolationConfigurationError(
            "Docker server metadata could not be inspected reliably"
        )
    version_match = re.match(r"^(\d+)(?:\.|$)", server_version)
    if version_match is None:
        raise DockerIsolationConfigurationError(
            f"Docker server version is unsupported or unrecognized: {server_version}"
        )
    if int(version_match.group(1)) < DOCKER_MINIMUM_SERVER_MAJOR:
        raise DockerIsolationConfigurationError(
            "Docker isolation requires Docker Engine server version 28 or newer; "
            f"found {server_version}"
        )
    os_result = _run_cli(
        (docker_executable, "info", "--format", "{{.OSType}}")
    )
    if os_result.returncode != 0 or not os_result.stdout.strip():
        raise DockerIsolationConfigurationError(
            "Docker server operating-system metadata could not be inspected reliably"
        )
    if os_result.stdout.strip().lower() != "linux":
        raise DockerIsolationConfigurationError(
            "Docker isolation requires the server to use Linux containers"
        )

    context_result = _run_cli(
        (
            docker_executable,
            "context",
            "inspect",
            "--format",
            "{{json .Endpoints.docker.Host}}",
        )
    )
    if context_result.returncode != 0:
        raise DockerIsolationConfigurationError(
            "Docker endpoint metadata could not be inspected reliably"
        )
    try:
        docker_endpoint = json.loads(context_result.stdout)
    except json.JSONDecodeError as error:
        raise DockerIsolationConfigurationError(
            "Docker endpoint metadata could not be inspected reliably"
        ) from error
    if not isinstance(docker_endpoint, str) or not docker_endpoint.startswith(
        ("unix://", "npipe://")
    ):
        raise DockerIsolationConfigurationError(
            "Docker isolation requires a local Unix-socket or named-pipe endpoint; "
            "remote Docker hosts are unsupported"
        )
    docker_host_override = os.environ.get("DOCKER_HOST")
    if docker_host_override is not None and not docker_host_override.startswith(
        ("unix://", "npipe://")
    ):
        raise DockerIsolationConfigurationError(
            "Docker isolation does not support a remote DOCKER_HOST endpoint"
        )

    image_result = _run_cli(
        (docker_executable, "image", "inspect", image_reference)
    )
    if image_result.returncode != 0:
        raise DockerIsolationConfigurationError(
            "Docker isolation image is not available locally; pull/build the image first"
        )
    try:
        image_payload = json.loads(image_result.stdout)
        if not isinstance(image_payload, list) or len(image_payload) != 1:
            raise ValueError
        metadata = image_payload[0]
        if not isinstance(metadata, dict):
            raise ValueError
        image_id = metadata["Id"]
        config = metadata["Config"]
        if not isinstance(image_id, str) or not isinstance(config, dict):
            raise ValueError
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise DockerIsolationConfigurationError(
            "Docker image metadata could not be inspected reliably"
        ) from error
    if _IMAGE_ID_PATTERN.fullmatch(image_id) is None:
        raise DockerIsolationConfigurationError(
            "Docker image resolved to an unsupported local image ID"
        )
    declared_volumes = config.get("Volumes")
    if declared_volumes:
        raise DockerIsolationConfigurationError(
            "Docker isolation image declares writable VOLUME paths and is unsupported"
        )

    return DockerIsolationPreflight(
        docker_executable=docker_executable,
        docker_server_version=server_version,
        image_reference=image_reference,
        image_id=image_id,
        source_root=resolved_source,
        port=port,
    )


class DockerManagedApplication:
    """Own one attached Docker CLI process and its exact managed resources."""

    def __init__(
        self,
        *,
        preflight: DockerIsolationPreflight,
        container_name: str,
        network_name: str,
        process: subprocess.Popen[bytes],
        drain: BoundedOutputDrain,
        reader: threading.Thread,
    ) -> None:
        self._preflight = preflight
        self.container_name = container_name
        self.network_name = network_name
        self._process = process
        self._drain = drain
        self._reader = reader
        self.state = ApplicationState.RUNNING

    @classmethod
    def start(
        cls,
        preflight: DockerIsolationPreflight,
        app_command: Sequence[str],
        *,
        max_log_bytes: int,
    ) -> DockerManagedApplication:
        """Create one internal network and start one attached managed container."""
        suffix = uuid.uuid4().hex
        container_name = f"agentverify-{suffix}-app"
        network_name = f"agentverify-{suffix}-net"
        try:
            network_result = _run_cli(
                (
                    preflight.docker_executable,
                    "network",
                    "create",
                    "--internal",
                    "--label",
                    "agentverify.managed=true",
                    network_name,
                )
            )
        except DockerIsolationConfigurationError as error:
            cleanup_confirmed = _cleanup_partial_start(
                docker=preflight.docker_executable,
                container_name=container_name,
                network_name=network_name,
                process=None,
            )
            message = (
                "Docker internal network creation did not complete after successful preflight"
            )
            if not cleanup_confirmed:
                message += "; partial Docker resource cleanup could not be confirmed"
            raise ApplicationStartError(
                message
            ) from error
        if network_result.returncode != 0:
            cleanup_confirmed = _cleanup_partial_start(
                docker=preflight.docker_executable,
                container_name=container_name,
                network_name=network_name,
                process=None,
            )
            message = "Docker internal network could not be created after successful preflight"
            if not cleanup_confirmed:
                message += "; partial Docker resource cleanup could not be confirmed"
            raise ApplicationStartError(message)

        argv = _docker_run_argv(
            preflight=preflight,
            app_command=app_command,
            container_name=container_name,
            network_name=network_name,
        )
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
            )
        except OSError as error:
            cleanup_confirmed = _cleanup_partial_start(
                docker=preflight.docker_executable,
                container_name=container_name,
                network_name=network_name,
                process=None,
            )
            message = "Docker CLI could not start the managed container"
            if not cleanup_confirmed:
                message += "; partial Docker resource cleanup could not be confirmed"
            raise ApplicationStartError(message) from error
        except KeyboardInterrupt as error:
            cleanup_confirmed = _cleanup_partial_start(
                docker=preflight.docker_executable,
                container_name=container_name,
                network_name=network_name,
                process=None,
            )
            if not cleanup_confirmed:
                raise ApplicationStartError(
                    "partial Docker resource cleanup could not be confirmed after interrupt"
                ) from error
            raise

        if process.stdout is None:
            cleanup_confirmed = _cleanup_partial_start(
                docker=preflight.docker_executable,
                container_name=container_name,
                network_name=network_name,
                process=process,
            )
            message = "Docker application output pipe could not be created"
            if not cleanup_confirmed:
                message += "; partial Docker resource cleanup could not be confirmed"
            raise ApplicationStartError(message)
        drain = BoundedOutputDrain(max_bytes=max_log_bytes)
        reader = threading.Thread(
            target=drain.drain,
            args=(process.stdout,),
            name="agentverify-docker-output",
            daemon=True,
        )
        reader.start()
        return cls(
            preflight=preflight,
            container_name=container_name,
            network_name=network_name,
            process=process,
            drain=drain,
            reader=reader,
        )

    def poll(self) -> int | None:
        return self._process.poll()

    def wait_for_readiness(
        self,
        base_url: str,
        *,
        timeout_ms: int,
        poll_interval_seconds: float = 0.05,
    ) -> ReadinessResult:
        """Reuse bounded host-loopback readiness while observing docker-run exit."""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            if self._process.poll() is not None:
                self.state = ApplicationState.EXITED_BEFORE_READINESS
                return ReadinessResult(False, "Docker application exited before readiness")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ReadinessResult(False, "Docker application readiness timed out")
            if endpoint_accepts_connection(
                base_url,
                timeout_seconds=min(0.2, remaining),
            ):
                if self._process.poll() is not None:
                    self.state = ApplicationState.EXITED_BEFORE_READINESS
                    return ReadinessResult(False, "Docker application exited before readiness")
                self.state = ApplicationState.READY
                return ReadinessResult(True, "Docker application endpoint accepted a connection")
            time.sleep(min(poll_interval_seconds, max(remaining, 0)))

    def note_unexpected_exit(self) -> bool:
        if self._process.poll() is None:
            return False
        if self.state is ApplicationState.READY:
            self.state = ApplicationState.EXITED_UNEXPECTEDLY
        return True

    def stop(self, *, grace_seconds: float = 1.0) -> ShutdownResult:
        """Stop/remove the exact container and network, then finalize the client."""
        errors: list[str] = []
        force_killed = False
        docker = self._preflight.docker_executable

        container_state = _inspect_container_state(docker, self.container_name)
        remove_result: subprocess.CompletedProcess[str] | None = None
        if container_state is None:
            errors.append("managed Docker container existence could not be inspected")
        if container_state != "absent":
            stop_result = _run_cleanup_cli(
                (
                    docker,
                    "container",
                    "stop",
                    "--time",
                    str(max(1, int(grace_seconds))),
                    self.container_name,
                ),
                timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
            )
            after_stop = _inspect_container_state(docker, self.container_name)
            if after_stop is None:
                errors.append("managed Docker container state after stop could not be inspected")
                _run_cleanup_cli((docker, "container", "kill", self.container_name))
                force_killed = True
            elif after_stop == "running":
                force_killed = True
                _run_cleanup_cli((docker, "container", "kill", self.container_name))
            if (
                (stop_result is None or stop_result.returncode != 0)
                and after_stop == "running"
            ):
                errors.append("managed Docker container did not accept bounded stop")
            remove_result = _run_cleanup_cli(
                (docker, "container", "rm", "--force", self.container_name)
            )
        final_container_state = _inspect_container_state(docker, self.container_name)
        if (
            container_state != "absent"
            and (remove_result is None or remove_result.returncode != 0)
            and final_container_state != "absent"
        ):
            errors.append("managed Docker container could not be removed")
        if final_container_state != "absent":
            errors.append("managed Docker container removal could not be confirmed")

        network_remove = _run_cleanup_cli((docker, "network", "rm", self.network_name))
        network_state = _inspect_network_state(docker, self.network_name)
        if (
            (network_remove is None or network_remove.returncode != 0)
            and network_state != "absent"
        ):
            errors.append("managed Docker network could not be removed")
        if network_state != "absent":
            errors.append("managed Docker network removal could not be confirmed")

        try:
            exit_code = self._finalize_client(grace_seconds=grace_seconds)
        except ApplicationCleanupError as error:
            exit_code = self._process.poll() or -1
            errors.append(str(error))
        try:
            self._join_reader()
        except ApplicationCleanupError as error:
            errors.append(str(error))
        if errors:
            raise ApplicationCleanupError("; ".join(dict.fromkeys(errors)))
        self.state = (
            ApplicationState.FORCE_KILLED if force_killed else ApplicationState.TERMINATED
        )
        return ShutdownResult(exit_code=exit_code, force_killed=force_killed)

    def output(self) -> ProcessOutput:
        if self._process.poll() is not None:
            self._join_reader()
        return self._drain.snapshot()

    def _finalize_client(self, *, grace_seconds: float) -> int:
        try:
            return self._process.wait(timeout=max(grace_seconds, 0.1))
        except subprocess.TimeoutExpired:
            self._process.terminate()
        try:
            return self._process.wait(timeout=max(grace_seconds, 0.1))
        except subprocess.TimeoutExpired:
            self._process.kill()
            try:
                return self._process.wait(timeout=max(grace_seconds, 0.1))
            except subprocess.TimeoutExpired as error:
                raise ApplicationCleanupError(
                    "Docker CLI process remained alive after force termination"
                ) from error

    def _join_reader(self) -> None:
        self._reader.join(timeout=1)
        if self._reader.is_alive():
            raise ApplicationCleanupError("Docker application output reader did not finish")


def _docker_run_argv(
    *,
    preflight: DockerIsolationPreflight,
    app_command: Sequence[str],
    container_name: str,
    network_name: str,
) -> tuple[str, ...]:
    source_mount = (
        f"type=bind,source={preflight.source_root},target=/workspace,readonly"
    )
    return (
        preflight.docker_executable,
        "run",
        "--name",
        container_name,
        "--label",
        "agentverify.managed=true",
        "--network",
        network_name,
        "--pull",
        "never",
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,nosuid,nodev,size={DOCKER_TMPFS_LIMIT_BYTES}",
        "--user",
        DOCKER_USER,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--no-healthcheck",
        "--memory",
        DOCKER_MEMORY_LIMIT,
        "--cpus",
        DOCKER_CPU_LIMIT,
        "--pids-limit",
        str(DOCKER_PID_LIMIT),
        "--shm-size",
        DOCKER_SHM_LIMIT,
        "--mount",
        source_mount,
        "--workdir",
        "/workspace",
        "--env",
        "HOME=/tmp",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTHONUNBUFFERED=1",
        "--publish",
        f"127.0.0.1:{preflight.port}:{preflight.port}/tcp",
        "--entrypoint",
        app_command[0],
        preflight.image_id,
        *app_command[1:],
    )


def _resolve_source_root(source_root: Path) -> Path:
    try:
        resolved = source_root.expanduser().resolve(strict=True)
    except OSError as error:
        raise DockerIsolationConfigurationError(
            "Docker source root could not be resolved"
        ) from error
    if not resolved.is_dir():
        raise DockerIsolationConfigurationError("Docker source root must be a directory")
    if resolved == Path(resolved.anchor):
        raise DockerIsolationConfigurationError(
            "Docker source root must not be a filesystem or drive root"
        )
    source_text = str(resolved)
    if "\x00" in source_text or "," in source_text:
        raise DockerIsolationConfigurationError(
            "Docker source root cannot be represented safely by the managed bind mount"
        )
    return resolved


def _validate_run_directory_boundary(run_dir: Path, *, source_root: Path) -> None:
    try:
        candidate = run_dir.expanduser().resolve(strict=False)
    except OSError as error:
        raise DockerIsolationConfigurationError(
            "Docker run directory boundary could not be resolved"
        ) from error
    if candidate == source_root or candidate.is_relative_to(source_root):
        raise DockerIsolationConfigurationError(
            "Docker isolation run directory must be outside the source root"
        )


def _docker_base_url_port(base_url: str) -> int:
    parsed = urlsplit(base_url)
    try:
        port = parsed.port
    except ValueError as error:
        raise DockerIsolationConfigurationError(
            "Docker isolation base URL must include a valid explicit TCP port"
        ) from error
    if parsed.hostname != "127.0.0.1":
        raise DockerIsolationConfigurationError(
            "Docker isolation base URL host must be exactly 127.0.0.1"
        )
    if port is None:
        raise DockerIsolationConfigurationError(
            "Docker isolation base URL must include an explicit TCP port"
        )
    return port


def _run_cli(
    argv: Sequence[str],
    *,
    timeout_seconds: float = _CLI_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            tuple(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DockerIsolationConfigurationError(
            "Docker CLI operation did not complete within its bounded timeout"
        ) from error


def _run_cleanup_cli(
    argv: Sequence[str],
    *,
    timeout_seconds: float = _CLEANUP_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return _run_cli(argv, timeout_seconds=timeout_seconds)
    except DockerIsolationConfigurationError:
        return None


def _cleanup_partial_start(
    *,
    docker: str,
    container_name: str,
    network_name: str,
    process: subprocess.Popen[bytes] | None,
) -> bool:
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    _run_cleanup_cli((docker, "container", "stop", "--time", "1", container_name))
    _run_cleanup_cli((docker, "container", "rm", "--force", container_name))
    container_absent = _inspect_container_state(docker, container_name) == "absent"
    _run_cleanup_cli((docker, "network", "rm", network_name))
    network_absent = _inspect_network_state(docker, network_name) == "absent"
    client_stopped = process is None or process.poll() is not None
    return container_absent and network_absent and client_stopped


def _inspect_container_state(docker: str, name: str) -> str | None:
    try:
        result = _run_cli(
            (docker, "container", "inspect", "--format", "{{.State.Running}}", name),
            timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
        )
    except DockerIsolationConfigurationError:
        return None
    if result.returncode == 0:
        return "running" if result.stdout.strip().lower() == "true" else "stopped"
    if "no such" in result.stderr.lower():
        return "absent"
    return None


def _inspect_network_state(docker: str, name: str) -> str | None:
    try:
        result = _run_cli(
            (docker, "network", "inspect", name),
            timeout_seconds=_CLEANUP_TIMEOUT_SECONDS,
        )
    except DockerIsolationConfigurationError:
        return None
    if result.returncode == 0:
        return "present"
    if "no such" in result.stderr.lower() or "not found" in result.stderr.lower():
        return "absent"
    return None
