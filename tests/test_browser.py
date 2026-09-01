"""Real Chromium tests for deterministic browser execution semantics."""

import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import socket
from threading import Thread

import pytest

from agentverify.browser import (
    BaseURLValidationError,
    BrowserEvidenceConfig,
    BrowserVerifier,
)
from agentverify.browser_plan import BrowserVerificationPlan
from agentverify.domain import Verdict
from agentverify.evidence import EvidenceKind, EvidenceLimits, EvidenceStore
from agentverify.run import build_browser_verification_results

FIXTURE_HTML = b"""<!doctype html>
<html lang="en">
  <body>
    <label>Name <input id="name"></label>
    <button id="greet">Greet</button>
    <p id="message" hidden></p>
    <p id="never-visible" hidden>Hidden forever</p>
    <p id="clean-context" hidden>Context is clean</p>
    <script>
      if (localStorage.getItem("touched") === null) {
        document.querySelector("#clean-context").hidden = false;
      }
      document.querySelector("#greet").addEventListener("click", async () => {
        console.error("Authorization: Bearer console-secret password=hunter2");
        await fetch("/api/ping?token=network-secret");
        setTimeout(() => { throw new Error("runtime token=runtime-secret"); }, 0);
        await new Promise((resolve) => setTimeout(resolve, 30));
        const name = document.querySelector("#name").value;
        const message = document.querySelector("#message");
        message.textContent = `Hello, ${name}!`;
        message.hidden = name !== "Ada";
        localStorage.setItem("touched", "yes");
      });
    </script>
  </body>
</html>
"""


class FixtureHandler(BaseHTTPRequestHandler):
    """Serve one maintained static browser fixture without external dependencies."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/ping"):
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(FIXTURE_HTML)))
        self.end_headers()
        self.wfile.write(FIXTURE_HTML)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def local_app_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    port = server.server_port
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        assert not server_thread.is_alive()


def browser_plan(criteria: list[dict[str, object]]) -> BrowserVerificationPlan:
    return BrowserVerificationPlan.model_validate_json(
        json.dumps(
            {
                "schema_version": 2,
                "task": "Exercise deterministic browser behavior",
                "criteria": criteria,
            }
        )
    )


def procedure(*steps: dict[str, object], timeout_ms: int = 500) -> dict[str, object]:
    return {"type": "browser", "timeout_ms": timeout_ms, "steps": list(steps)}


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://localhost:8000",
        "http://example.com",
        "http://192.0.2.1:8000",
        "http://user:password@localhost:8000",
    ],
)
def test_browser_verifier_rejects_nonlocal_or_unsafe_base_url(base_url: str) -> None:
    with pytest.raises(BaseURLValidationError):
        BrowserVerifier(base_url)


@pytest.mark.parametrize(
    "base_url",
    ["http://localhost:8000", "https://app.localhost", "http://127.0.0.2", "http://[::1]"],
)
def test_browser_verifier_accepts_loopback_http_origins(base_url: str) -> None:
    BrowserVerifier(base_url)


def test_real_browser_outcomes_order_and_context_isolation(local_app_url: str) -> None:
    outcome_timeout_ms = 2_000
    plan = browser_plan(
        [
            {
                "id": "AC-PASS",
                "description": "Greeting appears after interaction",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "fill", "selector": "#name", "value": "Ada"},
                    {"type": "click", "selector": "#greet"},
                    {"type": "assert_visible", "selector": "#message"},
                    timeout_ms=outcome_timeout_ms,
                ),
            },
            {
                "id": "AC-ISOLATED",
                "description": "Prior local storage does not leak",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "assert_visible", "selector": "#clean-context"},
                    timeout_ms=outcome_timeout_ms,
                ),
            },
            {
                "id": "AC-FAIL",
                "description": "A hidden element is visible",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "assert_visible", "selector": "#never-visible"},
                    timeout_ms=outcome_timeout_ms,
                ),
            },
            {
                "id": "AC-UNKNOWN",
                "description": "A missing target can be clicked",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "click", "selector": "#missing-button"},
                    {"type": "assert_visible", "selector": "#message"},
                    timeout_ms=outcome_timeout_ms,
                ),
            },
        ]
    )

    results = BrowserVerifier(local_app_url).verify(plan)

    assert [result.criterion_id for result in results] == [
        "AC-PASS",
        "AC-ISOLATED",
        "AC-FAIL",
        "AC-UNKNOWN",
    ]
    result_diagnostics = [
        {
            "criterion_id": result.criterion_id,
            "verdict": result.verdict.value,
            "reason": result.reason,
            "failed_step_index": result.failed_step_index,
        }
        for result in results
    ]
    assert [result.verdict for result in results] == [
        Verdict.PASS,
        Verdict.PASS,
        Verdict.FAIL,
        Verdict.UNKNOWN,
    ], result_diagnostics
    assert results[0].reason == "all 4 browser steps completed and assertions passed"
    assert results[2].reason == (
        "assert_visible failed at step 2 for selector '#never-visible'"
    )
    assert results[2].failed_step_index == 1
    assert results[3].reason == (
        "click could not execute at step 2 for selector '#missing-button'"
    )


def test_unreachable_local_application_is_unknown() -> None:
    unused_socket = socket()
    unused_socket.bind(("127.0.0.1", 0))
    host, port = unused_socket.getsockname()
    unused_socket.close()
    plan = browser_plan(
        [
            {
                "id": "AC-UNREACHABLE",
                "description": "The application can be reached",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "assert_visible", "selector": "#message"},
                    timeout_ms=200,
                ),
            }
        ]
    )

    result = BrowserVerifier(f"http://{host}:{port}").verify(plan)[0]

    assert result.verdict is Verdict.UNKNOWN
    assert result.reason == "navigate could not execute at step 1"
    assert result.failed_step_index == 0


def test_default_browser_evidence_policy_is_minimal() -> None:
    assert BrowserEvidenceConfig() == BrowserEvidenceConfig(
        capture_browser_observation=True,
        capture_screenshot=False,
        capture_trace=False,
        capture_console_errors=False,
        capture_network_summary=False,
    )


def test_real_browser_captures_and_verifies_opt_in_rich_evidence(
    local_app_url: str,
    tmp_path: Path,
) -> None:
    plan = browser_plan(
        [
            {
                "id": "AC-RICH",
                "description": "Greeting produces rich evidence",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "fill", "selector": "#name", "value": "Ada"},
                    {"type": "click", "selector": "#greet"},
                    {"type": "assert_visible", "selector": "#message"},
                    timeout_ms=2_000,
                ),
            }
        ]
    )
    store = EvidenceStore(tmp_path)

    executions = BrowserVerifier(local_app_url).verify_with_evidence(
        plan,
        evidence_store=store,
        config=BrowserEvidenceConfig(
            capture_screenshot=True,
            capture_trace=True,
            capture_console_errors=True,
            capture_network_summary=True,
        ),
    )
    manifest = store.build_manifest()
    store.verify_manifest(manifest)

    assert executions[0].verdict is Verdict.PASS
    assert executions[0].evidence_issues == ()
    assert len(executions[0].evidence_refs) == 5
    by_kind = {artifact.kind: artifact for artifact in manifest.artifacts}
    assert set(by_kind) == {
        EvidenceKind.BROWSER_OBSERVATION,
        EvidenceKind.SCREENSHOT,
        EvidenceKind.PLAYWRIGHT_TRACE,
        EvidenceKind.CONSOLE_ERRORS,
        EvidenceKind.NETWORK_SUMMARY,
    }

    screenshot = tmp_path / by_kind[EvidenceKind.SCREENSHOT].relative_path
    assert screenshot.stat().st_size > 0
    assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    trace = tmp_path / by_kind[EvidenceKind.PLAYWRIGHT_TRACE].relative_path
    assert trace.stat().st_size > 0
    assert trace.read_bytes().startswith(b"PK")
    assert by_kind[EvidenceKind.PLAYWRIGHT_TRACE].media_type == "application/zip"

    console_artifact = by_kind[EvidenceKind.CONSOLE_ERRORS]
    console_text = (tmp_path / console_artifact.relative_path).read_text(encoding="utf-8")
    assert "console.error" in console_text
    assert "pageerror" in console_text
    assert "[REDACTED]" in console_text
    assert "console-secret" not in console_text
    assert "hunter2" not in console_text
    assert "runtime-secret" not in console_text
    assert console_artifact.redacted is True

    network_text = (
        tmp_path / by_kind[EvidenceKind.NETWORK_SUMMARY].relative_path
    ).read_text(encoding="utf-8")
    network_payload = json.loads(network_text)
    ping_entries = [
        entry for entry in network_payload["entries"] if entry["path"] == "/api/ping"
    ]
    assert ping_entries
    assert ping_entries[0]["method"] == "GET"
    assert ping_entries[0]["status"] == 200
    assert "network-secret" not in network_text
    assert "?" not in network_text

    results = build_browser_verification_results(
        plan=plan,
        executions=executions,
        manifest=manifest,
        evidence_root=tmp_path,
    )
    assert results[0].verdict is Verdict.PASS
    assert results[0].evidence_refs == executions[0].evidence_refs


def test_evidence_capture_failure_does_not_become_application_fail(
    local_app_url: str,
    tmp_path: Path,
) -> None:
    plan = browser_plan(
        [
            {
                "id": "AC-LIMITED",
                "description": "Minimal observation survives a rich capture limit",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "assert_visible", "selector": "#clean-context"},
                ),
            }
        ]
    )
    store = EvidenceStore(
        tmp_path,
        limits=EvidenceLimits(max_artifacts_per_criterion=1),
    )

    execution = BrowserVerifier(local_app_url).verify_with_evidence(
        plan,
        evidence_store=store,
        config=BrowserEvidenceConfig(capture_screenshot=True),
    )[0]

    assert execution.verdict is Verdict.PASS
    assert execution.evidence_issues == ("screenshot capture failed",)
    assert len(execution.evidence_refs) == 1
    assert store.artifacts[0].kind is EvidenceKind.BROWSER_OBSERVATION
    result = build_browser_verification_results(
        plan=plan,
        executions=(execution,),
        manifest=store.build_manifest(),
        evidence_root=tmp_path,
    )[0]
    assert result.verdict is Verdict.PASS


def test_real_screenshot_without_browser_observation_cannot_authorize_pass(
    local_app_url: str,
    tmp_path: Path,
) -> None:
    plan = browser_plan(
        [
            {
                "id": "AC-SCREENSHOT-ONLY",
                "description": "A screenshot alone cannot authorize a pass",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "assert_visible", "selector": "#clean-context"},
                ),
            }
        ]
    )
    store = EvidenceStore(tmp_path)

    execution = BrowserVerifier(local_app_url).verify_with_evidence(
        plan,
        evidence_store=store,
        config=BrowserEvidenceConfig(
            capture_browser_observation=False,
            capture_screenshot=True,
        ),
    )[0]

    assert execution.verdict is Verdict.PASS
    assert execution.evidence_issues == ()
    assert len(execution.evidence_refs) == 1
    assert store.artifacts[0].kind is EvidenceKind.SCREENSHOT
    screenshot = tmp_path / execution.evidence_refs[0]
    assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    result = build_browser_verification_results(
        plan=plan,
        executions=(execution,),
        manifest=store.build_manifest(),
        evidence_root=tmp_path,
    )[0]
    assert result.verdict is Verdict.UNKNOWN
    assert result.evidence_refs == execution.evidence_refs
