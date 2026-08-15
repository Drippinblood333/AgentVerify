"""Real Chromium tests for deterministic browser execution semantics."""

import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socket import socket
from threading import Thread

import pytest

from agentverify.browser import BaseURLValidationError, BrowserVerifier
from agentverify.browser_plan import BrowserVerificationPlan
from agentverify.domain import Verdict

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
      document.querySelector("#greet").addEventListener("click", () => {
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
                ),
            },
            {
                "id": "AC-ISOLATED",
                "description": "Prior local storage does not leak",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "assert_visible", "selector": "#clean-context"},
                ),
            },
            {
                "id": "AC-FAIL",
                "description": "A hidden element is visible",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "assert_visible", "selector": "#never-visible"},
                ),
            },
            {
                "id": "AC-UNKNOWN",
                "description": "A missing target can be clicked",
                "procedure": procedure(
                    {"type": "navigate", "path": "/"},
                    {"type": "click", "selector": "#missing-button"},
                    {"type": "assert_visible", "selector": "#message"},
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
    assert [result.verdict for result in results] == [
        Verdict.PASS,
        Verdict.PASS,
        Verdict.FAIL,
        Verdict.UNKNOWN,
    ]
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
