"""Deterministic Chromium execution for Verification Plan v2 browser procedures."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from ipaddress import ip_address
from pathlib import Path
from typing import assert_never
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import (
    Browser,
    BrowserContext,
    ConsoleMessage,
    Error,
    Page,
    Request,
    Response,
    expect,
    sync_playwright,
)

from agentverify_evidence.browser_plan import (
    AssertVisibleStep,
    BrowserAcceptanceCriterion,
    BrowserVerificationPlan,
    ClickStep,
    FillStep,
    NavigateStep,
)
from agentverify_evidence.domain import Verdict
from agentverify_evidence.evidence import EvidenceError, EvidenceKind, EvidenceStore


class BaseURLValidationError(ValueError):
    """The supplied application URL is not a loopback HTTP(S) origin."""


@dataclass(frozen=True, slots=True)
class BrowserEvidenceConfig:
    """Run-level capture policy kept deliberately separate from Plan v2."""

    capture_browser_observation: bool = True
    capture_screenshot: bool = False
    capture_trace: bool = False
    capture_console_errors: bool = False
    capture_network_summary: bool = False


@dataclass(frozen=True, slots=True)
class BrowserExecutionResult:
    """Internal browser outcome; it is not ProofReceipt data."""

    criterion_id: str
    verdict: Verdict
    reason: str
    failed_step_index: int | None = None
    evidence_refs: tuple[str, ...] = ()
    evidence_issues: tuple[str, ...] = ()


def _validated_loopback_origin(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise BaseURLValidationError("base URL must be a nonblank string")

    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError as error:
        raise BaseURLValidationError(f"invalid base URL: {error}") from error

    if parsed.scheme not in {"http", "https"}:
        raise BaseURLValidationError("base URL scheme must be http or https")
    if parsed.username is not None or parsed.password is not None:
        raise BaseURLValidationError("base URL must not contain credentials")
    if parsed.hostname is None:
        raise BaseURLValidationError("base URL must contain a host")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise BaseURLValidationError(
            "base URL must be an origin without a path, query, or fragment"
        )

    hostname = parsed.hostname.rstrip(".").lower()
    is_loopback = hostname == "localhost" or hostname.endswith(".localhost")
    if not is_loopback:
        try:
            is_loopback = ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise BaseURLValidationError("base URL host must be a loopback address")

    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


class BrowserVerifier:
    """Run a v2 plan headlessly with one browser and isolated criterion contexts."""

    def __init__(self, base_url: str) -> None:
        self._base_origin = _validated_loopback_origin(base_url)

    def verify(self, plan: BrowserVerificationPlan) -> tuple[BrowserExecutionResult, ...]:
        """Preserve the M4 in-memory execution path without filesystem requirements."""
        return self._verify(plan, evidence_store=None, config=None)

    def verify_with_evidence(
        self,
        plan: BrowserVerificationPlan,
        *,
        evidence_store: EvidenceStore,
        config: BrowserEvidenceConfig | None = None,
    ) -> tuple[BrowserExecutionResult, ...]:
        """Execute the same step engine while durably capturing selected evidence."""
        return self._verify(
            plan,
            evidence_store=evidence_store,
            config=config or BrowserEvidenceConfig(),
        )

    def _verify(
        self,
        plan: BrowserVerificationPlan,
        *,
        evidence_store: EvidenceStore | None,
        config: BrowserEvidenceConfig | None,
    ) -> tuple[BrowserExecutionResult, ...]:
        manager = sync_playwright()
        try:
            playwright = manager.start()
        except Error:
            return self._finish_without_pages(
                self._all_unknown(plan, "Chromium infrastructure could not start"),
                evidence_store,
                config,
            )

        try:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Error:
                return self._finish_without_pages(
                    self._all_unknown(plan, "Chromium could not launch"),
                    evidence_store,
                    config,
                )

            try:
                return tuple(
                    self._execute_criterion(
                        browser,
                        criterion,
                        evidence_store=evidence_store,
                        config=config,
                    )
                    for criterion in plan.criteria
                )
            finally:
                browser.close()
        finally:
            playwright.stop()

    def _finish_without_pages(
        self,
        results: tuple[BrowserExecutionResult, ...],
        evidence_store: EvidenceStore | None,
        config: BrowserEvidenceConfig | None,
    ) -> tuple[BrowserExecutionResult, ...]:
        if evidence_store is None or config is None:
            return results
        return tuple(
            self._capture_observation(result, evidence_store, config) for result in results
        )

    @staticmethod
    def _all_unknown(
        plan: BrowserVerificationPlan,
        reason: str,
    ) -> tuple[BrowserExecutionResult, ...]:
        return tuple(
            BrowserExecutionResult(
                criterion_id=criterion.id,
                verdict=Verdict.UNKNOWN,
                reason=reason,
            )
            for criterion in plan.criteria
        )

    def _execute_criterion(
        self,
        browser: Browser,
        criterion: BrowserAcceptanceCriterion,
        *,
        evidence_store: EvidenceStore | None,
        config: BrowserEvidenceConfig | None,
    ) -> BrowserExecutionResult:
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 720})
        except Error:
            result = BrowserExecutionResult(
                criterion_id=criterion.id,
                verdict=Verdict.UNKNOWN,
                reason="A fresh browser context could not be created",
            )
            if evidence_store is not None and config is not None:
                return self._capture_observation(result, evidence_store, config)
            return result

        trace_started = False
        page: Page | None = None
        console_entries: list[dict[str, str]] = []
        network_entries: list[dict[str, object]] = []
        try:
            if evidence_store is not None and config is not None and config.capture_trace:
                try:
                    context.tracing.start(screenshots=True, snapshots=True, sources=False)
                    trace_started = True
                except Error:
                    pass
            try:
                page = context.new_page()
                page.set_default_timeout(criterion.procedure.timeout_ms)
                page.set_default_navigation_timeout(criterion.procedure.timeout_ms)
            except Error:
                result = BrowserExecutionResult(
                    criterion_id=criterion.id,
                    verdict=Verdict.UNKNOWN,
                    reason="A browser page could not be created",
                )
            else:
                if evidence_store is not None and config is not None:
                    self._register_evidence_listeners(
                        page,
                        evidence_store,
                        config,
                        console_entries,
                        network_entries,
                    )
                result = self._execute_steps(page, criterion)

            if evidence_store is not None and config is not None:
                return self._capture_evidence(
                    context=context,
                    page=page,
                    result=result,
                    store=evidence_store,
                    config=config,
                    console_entries=console_entries,
                    network_entries=network_entries,
                    trace_started=trace_started,
                )
            return result
        finally:
            context.close()

    @staticmethod
    def _register_evidence_listeners(
        page: Page,
        store: EvidenceStore,
        config: BrowserEvidenceConfig,
        console_entries: list[dict[str, str]],
        network_entries: list[dict[str, object]],
    ) -> None:
        if config.capture_console_errors:

            def on_console(message: ConsoleMessage) -> None:
                if (
                    message.type == "error"
                    and len(console_entries) < store.limits.max_console_entries
                ):
                    console_entries.append(
                        {"source": "console.error", "message": message.text[:1024]}
                    )

            def on_page_error(error: Error) -> None:
                if len(console_entries) < store.limits.max_console_entries:
                    console_entries.append(
                        {"source": "pageerror", "message": str(error)[:1024]}
                    )

            page.on("console", on_console)
            page.on("pageerror", on_page_error)

        if config.capture_network_summary:

            def on_response(response: Response) -> None:
                if len(network_entries) >= store.limits.max_network_entries:
                    return
                entry = BrowserVerifier._network_entry(
                    response.request,
                    status=response.status,
                    success=True,
                )
                if entry is not None:
                    network_entries.append(entry)

            def on_request_failed(request: Request) -> None:
                if len(network_entries) >= store.limits.max_network_entries:
                    return
                entry = BrowserVerifier._network_entry(
                    request,
                    status=None,
                    success=False,
                )
                if entry is not None:
                    network_entries.append(entry)

            page.on("response", on_response)
            page.on("requestfailed", on_request_failed)

    @staticmethod
    def _network_entry(
        request: Request,
        *,
        status: int | None,
        success: bool,
    ) -> dict[str, object] | None:
        try:
            parsed = urlsplit(request.url)
        except ValueError:
            return None
        return {
            "method": request.method[:32],
            "scheme": parsed.scheme[:16],
            "host": (parsed.hostname or "")[:255],
            "path": (parsed.path or "/")[:1024],
            "status": status,
            "resource_type": request.resource_type[:64],
            "success": success,
        }

    def _capture_evidence(
        self,
        *,
        context: BrowserContext,
        page: Page | None,
        result: BrowserExecutionResult,
        store: EvidenceStore,
        config: BrowserEvidenceConfig,
        console_entries: list[dict[str, str]],
        network_entries: list[dict[str, object]],
        trace_started: bool,
    ) -> BrowserExecutionResult:
        captured = self._capture_observation(result, store, config)

        if config.capture_screenshot and page is not None:
            try:
                screenshot = page.screenshot(type="png", animations="disabled")
                artifact = store.record_bytes(
                    kind=EvidenceKind.SCREENSHOT,
                    data=screenshot,
                    media_type="image/png",
                    producer="agentverify.browser",
                    criterion_id=result.criterion_id,
                )
            except (Error, EvidenceError):
                captured = self._add_evidence_issue(captured, "screenshot capture failed")
            else:
                captured = self._add_evidence_ref(captured, artifact.relative_path)

        if config.capture_console_errors:
            try:
                artifact = store.record_json(
                    kind=EvidenceKind.CONSOLE_ERRORS,
                    payload={"schema_version": 1, "entries": console_entries},
                    producer="agentverify.browser",
                    criterion_id=result.criterion_id,
                )
            except EvidenceError:
                captured = self._add_evidence_issue(
                    captured, "console evidence persistence failed"
                )
            else:
                captured = self._add_evidence_ref(captured, artifact.relative_path)

        if config.capture_network_summary:
            try:
                artifact = store.record_json(
                    kind=EvidenceKind.NETWORK_SUMMARY,
                    payload={"schema_version": 1, "entries": network_entries},
                    producer="agentverify.browser",
                    criterion_id=result.criterion_id,
                )
            except EvidenceError:
                captured = self._add_evidence_issue(
                    captured, "network evidence persistence failed"
                )
            else:
                captured = self._add_evidence_ref(captured, artifact.relative_path)

        if config.capture_trace:
            if not trace_started:
                captured = self._add_evidence_issue(captured, "trace capture could not start")
            else:
                try:
                    with tempfile.TemporaryDirectory(
                        prefix="agentverify-trace-",
                        dir=store.run_root,
                    ) as temporary_directory:
                        trace_path = Path(temporary_directory) / "trace.zip"
                        context.tracing.stop(path=str(trace_path))
                        artifact = store.adopt_file(
                            kind=EvidenceKind.PLAYWRIGHT_TRACE,
                            source=trace_path,
                            media_type="application/zip",
                            producer="agentverify.browser",
                            criterion_id=result.criterion_id,
                        )
                except (Error, EvidenceError, OSError):
                    captured = self._add_evidence_issue(captured, "trace capture failed")
                else:
                    captured = self._add_evidence_ref(captured, artifact.relative_path)
        return captured

    @staticmethod
    def _capture_observation(
        result: BrowserExecutionResult,
        store: EvidenceStore,
        config: BrowserEvidenceConfig,
    ) -> BrowserExecutionResult:
        if not config.capture_browser_observation:
            return result
        try:
            artifact = store.record_json(
                kind=EvidenceKind.BROWSER_OBSERVATION,
                payload={
                    "schema_version": 1,
                    "criterion_id": result.criterion_id,
                    "verdict": result.verdict.value,
                    "reason": result.reason,
                    "failed_step_index": result.failed_step_index,
                },
                producer="agentverify.browser",
                criterion_id=result.criterion_id,
            )
        except EvidenceError:
            return BrowserVerifier._add_evidence_issue(
                result, "browser observation persistence failed"
            )
        return BrowserVerifier._add_evidence_ref(result, artifact.relative_path)

    @staticmethod
    def _add_evidence_ref(
        result: BrowserExecutionResult,
        evidence_ref: str,
    ) -> BrowserExecutionResult:
        return replace(result, evidence_refs=(*result.evidence_refs, evidence_ref))

    @staticmethod
    def _add_evidence_issue(
        result: BrowserExecutionResult,
        issue: str,
    ) -> BrowserExecutionResult:
        return replace(result, evidence_issues=(*result.evidence_issues, issue))

    def _execute_steps(
        self,
        page: Page,
        criterion: BrowserAcceptanceCriterion,
    ) -> BrowserExecutionResult:
        timeout_ms = criterion.procedure.timeout_ms
        for index, step in enumerate(criterion.procedure.steps):
            step_number = index + 1
            if isinstance(step, AssertVisibleStep):
                try:
                    expect(page.locator(step.selector)).to_be_visible(timeout=timeout_ms)
                except AssertionError:
                    return BrowserExecutionResult(
                        criterion_id=criterion.id,
                        verdict=Verdict.FAIL,
                        reason=(
                            f"assert_visible failed at step {step_number} "
                            f"for selector {step.selector!r}"
                        ),
                        failed_step_index=index,
                    )
                except Error:
                    return self._unknown_step(criterion.id, "assert_visible", index, step.selector)
                continue

            try:
                if isinstance(step, NavigateStep):
                    page.goto(
                        f"{self._base_origin}{step.path}",
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                elif isinstance(step, FillStep):
                    page.locator(step.selector).fill(step.value, timeout=timeout_ms)
                elif isinstance(step, ClickStep):
                    page.locator(step.selector).click(timeout=timeout_ms)
                else:
                    assert_never(step)
            except Error:
                selector = None if isinstance(step, NavigateStep) else step.selector
                return self._unknown_step(criterion.id, step.type, index, selector)

        return BrowserExecutionResult(
            criterion_id=criterion.id,
            verdict=Verdict.PASS,
            reason=(
                f"all {len(criterion.procedure.steps)} browser steps completed "
                "and assertions passed"
            ),
        )

    @staticmethod
    def _unknown_step(
        criterion_id: str,
        step_type: str,
        index: int,
        selector: str | None,
    ) -> BrowserExecutionResult:
        selector_text = "" if selector is None else f" for selector {selector!r}"
        return BrowserExecutionResult(
            criterion_id=criterion_id,
            verdict=Verdict.UNKNOWN,
            reason=f"{step_type} could not execute at step {index + 1}{selector_text}",
            failed_step_index=index,
        )
