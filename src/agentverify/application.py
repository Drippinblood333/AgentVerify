"""Bounded local application lifecycle management for M6 verification runs."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import BinaryIO
from urllib.parse import urlsplit

_WINDOWS_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_POSIX_FORCE_KILL = getattr(signal, "SIGKILL", 9)


class ApplicationError(Exception):
    """Base class for expected managed-application failures."""


class ApplicationStartError(ApplicationError):
    """The configured application process could not be started."""


class ApplicationCleanupError(ApplicationError):
    """The managed application lifecycle could not be cleaned up."""


class ApplicationState(StrEnum):
    """Small observable lifecycle state for one directly managed process."""

    RUNNING = "running"
    EXITED_BEFORE_READINESS = "exited_before_readiness"
    READY = "ready"
    EXITED_UNEXPECTEDLY = "exited_unexpectedly"
    SHUTDOWN_REQUESTED = "shutdown_requested"
    TERMINATED = "terminated"
    FORCE_KILLED = "force_killed"


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    """Outcome of a bounded local TCP readiness wait."""

    ready: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ProcessOutput:
    """Bounded combined stdout/stderr retained from the managed process."""

    text: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class ShutdownResult:
    """Observable result of bounded termination and escalation."""

    exit_code: int
    force_killed: bool


class BoundedOutputDrain:
    """Drain one merged pipe forever while retaining only a bounded prefix."""

    _TRUNCATION_MARKER = b"\n[agentverify: process output truncated]\n"

    def __init__(self, *, max_bytes: int) -> None:
        if max_bytes < len(self._TRUNCATION_MARKER):
            raise ValueError("process log byte limit is too small")
        self._max_bytes = max_bytes
        self._retained = bytearray()
        self._truncated = False
        self._lock = threading.Lock()

    def drain(self, pipe: BinaryIO) -> None:
        try:
            while chunk := pipe.read(8192):
                with self._lock:
                    remaining = self._max_bytes - len(self._retained)
                    if remaining > 0:
                        self._retained.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self._truncated = True
        finally:
            pipe.close()

    def snapshot(self) -> ProcessOutput:
        with self._lock:
            retained = bytes(self._retained)
            truncated = self._truncated

        decoded = retained.decode("utf-8", errors="replace")
        marker = self._TRUNCATION_MARKER.decode("ascii") if truncated else ""
        available = self._max_bytes - len(marker.encode("utf-8"))
        encoded = decoded.encode("utf-8")
        if len(encoded) > available:
            truncated = True
            marker = self._TRUNCATION_MARKER.decode("ascii")
            available = self._max_bytes - len(self._TRUNCATION_MARKER)
            encoded = encoded[:available]
            decoded = encoded.decode("utf-8", errors="ignore")
        return ProcessOutput(text=f"{decoded}{marker}", truncated=truncated)


def _endpoint_address(base_url: str) -> tuple[str, int]:
    parsed = urlsplit(base_url)
    host = parsed.hostname
    if host is None:
        raise ValueError("base URL must include a host")
    return host, parsed.port or (443 if parsed.scheme == "https" else 80)


def endpoint_accepts_connection(
    base_url: str,
    *,
    timeout_seconds: float = 0.2,
) -> bool:
    """Return whether the validated local endpoint currently accepts TCP."""
    if timeout_seconds <= 0:
        raise ValueError("endpoint probe timeout must be positive")
    try:
        connection = socket.create_connection(
            _endpoint_address(base_url),
            timeout=timeout_seconds,
        )
    except OSError:
        return False
    connection.close()
    return True


class ManagedApplication:
    """Own one local child process, its output drain, readiness, and cleanup."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        drain: BoundedOutputDrain,
        reader: threading.Thread,
    ) -> None:
        self._process = process
        self._drain = drain
        self._reader = reader
        self.state = ApplicationState.RUNNING

    @classmethod
    def start(
        cls,
        argv: Sequence[str],
        *,
        max_log_bytes: int,
    ) -> ManagedApplication:
        """Start an argv directly without a shell and immediately drain output."""
        creation_flags = 0
        start_new_session = False
        if os.name == "nt":
            creation_flags = _WINDOWS_NEW_PROCESS_GROUP
        else:
            start_new_session = True

        try:
            process = subprocess.Popen(
                tuple(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                creationflags=creation_flags,
                start_new_session=start_new_session,
            )
        except OSError as error:
            raise ApplicationStartError("application executable could not start") from error

        if process.stdout is None:
            process.kill()
            process.wait()
            raise ApplicationStartError("application output pipe could not be created")

        drain = BoundedOutputDrain(max_bytes=max_log_bytes)
        reader = threading.Thread(
            target=drain.drain,
            args=(process.stdout,),
            name="agentverify-application-output",
            daemon=True,
        )
        reader.start()
        return cls(process, drain, reader)

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self) -> int | None:
        """Return the process exit code without waiting."""
        return self._process.poll()

    def wait_for_readiness(
        self,
        base_url: str,
        *,
        timeout_ms: int,
        poll_interval_seconds: float = 0.05,
    ) -> ReadinessResult:
        """Wait until the configured loopback endpoint accepts a TCP connection."""
        address = _endpoint_address(base_url)
        deadline = time.monotonic() + timeout_ms / 1000

        while True:
            if self._process.poll() is not None:
                self.state = ApplicationState.EXITED_BEFORE_READINESS
                return ReadinessResult(False, "Application exited before readiness")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return ReadinessResult(False, "Application readiness timed out")

            try:
                connection = socket.create_connection(
                    address,
                    timeout=min(0.2, remaining),
                )
            except OSError:
                time.sleep(min(poll_interval_seconds, max(remaining, 0)))
                continue

            connection.close()
            if self._process.poll() is not None:
                self.state = ApplicationState.EXITED_BEFORE_READINESS
                return ReadinessResult(False, "Application exited before readiness")
            self.state = ApplicationState.READY
            return ReadinessResult(True, "Application endpoint accepted a connection")

    def note_unexpected_exit(self) -> bool:
        """Record an exit that happened after readiness but before shutdown."""
        if self._process.poll() is None:
            return False
        if self.state is ApplicationState.READY:
            self.state = ApplicationState.EXITED_UNEXPECTEDLY
        return True

    def stop(self, *, grace_seconds: float = 0.5) -> ShutdownResult:
        """Stop the direct child and, on POSIX, its dedicated process group."""
        if os.name == "nt":
            return self._stop_windows(grace_seconds=grace_seconds)
        return self._stop_posix(grace_seconds=grace_seconds)

    def _stop_windows(self, *, grace_seconds: float) -> ShutdownResult:
        existing_exit = self._process.poll()
        if existing_exit is not None:
            self._join_reader()
            return ShutdownResult(existing_exit, force_killed=False)

        self.state = ApplicationState.SHUTDOWN_REQUESTED
        try:
            self._process.terminate()
        except OSError as error:
            raise ApplicationCleanupError(
                "application process could not be terminated"
            ) from error
        try:
            exit_code = self._process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                self._process.kill()
            except OSError as error:
                raise ApplicationCleanupError(
                    "application process could not be force terminated"
                ) from error
            try:
                exit_code = self._process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired as error:
                raise ApplicationCleanupError(
                    "application process remained alive after force termination"
                ) from error
            self.state = ApplicationState.FORCE_KILLED
            force_killed = True
        else:
            self.state = ApplicationState.TERMINATED
            force_killed = False

        self._join_reader()
        if self._process.poll() is None:
            raise ApplicationCleanupError("application process cleanup was not confirmed")
        return ShutdownResult(exit_code, force_killed=force_killed)

    def _stop_posix(self, *, grace_seconds: float) -> ShutdownResult:
        existing_exit = self._process.poll()
        if existing_exit is not None and not self._process_group_exists():
            self._join_reader()
            return ShutdownResult(existing_exit, force_killed=False)

        preserve_exit_state = self.state in {
            ApplicationState.EXITED_BEFORE_READINESS,
            ApplicationState.EXITED_UNEXPECTEDLY,
        }
        if not preserve_exit_state:
            self.state = ApplicationState.SHUTDOWN_REQUESTED

        self._signal_process_group(signal.SIGTERM)
        exit_code = self._wait_for_process_group_shutdown(grace_seconds)
        force_killed = False
        if exit_code is None:
            self._signal_process_group(_POSIX_FORCE_KILL)
            if self._process.poll() is None:
                try:
                    self._process.kill()
                except OSError as error:
                    raise ApplicationCleanupError(
                        "application process could not be force terminated"
                    ) from error
            exit_code = self._wait_for_process_group_shutdown(grace_seconds)
            if exit_code is None:
                raise ApplicationCleanupError(
                    "managed process group remained alive after force termination"
                )
            force_killed = True

        if not preserve_exit_state:
            self.state = (
                ApplicationState.FORCE_KILLED
                if force_killed
                else ApplicationState.TERMINATED
            )
        self._join_reader()
        return ShutdownResult(exit_code, force_killed=force_killed)

    def output(self) -> ProcessOutput:
        """Return the retained combined process output after cleanup."""
        if self._process.poll() is not None:
            self._join_reader()
        return self._drain.snapshot()

    def _process_group_exists(self) -> bool:
        try:
            os.kill(-self._process.pid, 0)
        except ProcessLookupError:
            return False
        except OSError as error:
            raise ApplicationCleanupError(
                "managed process group could not be inspected"
            ) from error
        return True

    def _signal_process_group(self, requested_signal: int) -> None:
        try:
            os.kill(-self._process.pid, requested_signal)
        except ProcessLookupError:
            return
        except OSError as error:
            raise ApplicationCleanupError(
                "managed process group could not be signalled"
            ) from error

    def _wait_for_process_group_shutdown(self, timeout_seconds: float) -> int | None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            exit_code = self._process.poll()
            if exit_code is not None and not self._process_group_exists():
                return exit_code
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.01)

    def _join_reader(self) -> None:
        self._reader.join(timeout=1)
        if self._reader.is_alive():
            raise ApplicationCleanupError("application output reader did not finish")
