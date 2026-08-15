"""Deterministic Chromium execution for Verification Plan v2 browser procedures."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import assert_never
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Browser, Error, Page, expect, sync_playwright

from agentverify.browser_plan import (
    AssertVisibleStep,
    BrowserAcceptanceCriterion,
    BrowserVerificationPlan,
    ClickStep,
    FillStep,
    NavigateStep,
)
from agentverify.domain import Verdict


class BaseURLValidationError(ValueError):
    """The supplied application URL is not a loopback HTTP(S) origin."""


@dataclass(frozen=True, slots=True)
class BrowserExecutionResult:
    """Internal M4 outcome; it is not durable evidence or ProofReceipt data."""

    criterion_id: str
    verdict: Verdict
    reason: str
    failed_step_index: int | None = None


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
        manager = sync_playwright()
        try:
            playwright = manager.start()
        except Error:
            return self._all_unknown(plan, "Chromium infrastructure could not start")

        try:
            try:
                browser = playwright.chromium.launch(headless=True)
            except Error:
                return self._all_unknown(plan, "Chromium could not launch")

            try:
                return tuple(
                    self._execute_criterion(browser, criterion) for criterion in plan.criteria
                )
            finally:
                browser.close()
        finally:
            playwright.stop()

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
    ) -> BrowserExecutionResult:
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 720})
        except Error:
            return BrowserExecutionResult(
                criterion_id=criterion.id,
                verdict=Verdict.UNKNOWN,
                reason="A fresh browser context could not be created",
            )

        try:
            try:
                page = context.new_page()
                page.set_default_timeout(criterion.procedure.timeout_ms)
                page.set_default_navigation_timeout(criterion.procedure.timeout_ms)
            except Error:
                return BrowserExecutionResult(
                    criterion_id=criterion.id,
                    verdict=Verdict.UNKNOWN,
                    reason="A browser page could not be created",
                )
            return self._execute_steps(page, criterion)
        finally:
            context.close()

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
