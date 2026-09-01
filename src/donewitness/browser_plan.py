"""Pure models for the narrow Verification Plan v2 browser procedure."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from donewitness.domain import NonBlankText


class NavigateStep(BaseModel):
    """Navigate within the supplied local application origin."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["navigate"]
    path: StrictStr

    @field_validator("path")
    @classmethod
    def require_local_application_path(cls, path: str) -> str:
        """Reject absolute, protocol-relative, blank, and backslash paths."""
        contains_unsafe_character = "\\" in path or any(
            character.isspace() or ord(character) < 32 for character in path
        )
        if not path.startswith("/") or path.startswith("//") or contains_unsafe_character:
            raise ValueError(
                "navigate path must begin with exactly one '/' and stay within the local origin"
            )
        return path


class FillStep(BaseModel):
    """Fill a CSS-selected element with an exact string value."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["fill"]
    selector: StrictStr
    value: StrictStr

    @field_validator("selector")
    @classmethod
    def require_nonblank_selector(cls, selector: str) -> str:
        if not selector.strip():
            raise ValueError("selector must contain non-whitespace text")
        return selector


class ClickStep(BaseModel):
    """Click a CSS-selected element."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["click"]
    selector: StrictStr

    @field_validator("selector")
    @classmethod
    def require_nonblank_selector(cls, selector: str) -> str:
        if not selector.strip():
            raise ValueError("selector must contain non-whitespace text")
        return selector


class AssertVisibleStep(BaseModel):
    """Assert that a CSS-selected element becomes visible."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["assert_visible"]
    selector: StrictStr

    @field_validator("selector")
    @classmethod
    def require_nonblank_selector(cls, selector: str) -> str:
        if not selector.strip():
            raise ValueError("selector must contain non-whitespace text")
        return selector


type BrowserStep = Annotated[
    NavigateStep | FillStep | ClickStep | AssertVisibleStep,
    Field(discriminator="type"),
]


class BrowserProcedure(BaseModel):
    """One bounded, explicit browser procedure for an acceptance criterion."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    type: Literal["browser"]
    timeout_ms: Annotated[int, Field(ge=100, le=30_000)] = 5_000
    steps: Annotated[tuple[BrowserStep, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def require_navigated_assertion(self) -> BrowserProcedure:
        if not isinstance(self.steps[0], NavigateStep):
            raise ValueError("the first browser step must be navigate")
        if not any(isinstance(step, AssertVisibleStep) for step in self.steps):
            raise ValueError("browser procedure must contain at least one assert_visible step")
        return self


class BrowserAcceptanceCriterion(BaseModel):
    """One v2 criterion with a frozen browser procedure."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: NonBlankText
    description: NonBlankText
    procedure: BrowserProcedure


class BrowserVerificationPlan(BaseModel):
    """Verification Plan v2 with explicit deterministic browser procedures."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[2]
    task: NonBlankText
    criteria: Annotated[tuple[BrowserAcceptanceCriterion, ...], Field(min_length=1)]

    @field_validator("criteria")
    @classmethod
    def require_unique_criterion_ids(
        cls,
        criteria: tuple[BrowserAcceptanceCriterion, ...],
    ) -> tuple[BrowserAcceptanceCriterion, ...]:
        seen: set[str] = set()
        for criterion in criteria:
            if criterion.id in seen:
                raise ValueError(f"criterion id must be unique: {criterion.id}")
            seen.add(criterion.id)
        return criteria
