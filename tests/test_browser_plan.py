"""Schema and digest tests for Verification Plan v2 browser procedures."""

import copy
import json
from pathlib import Path

import pytest

from donewitness.browser_plan import BrowserVerificationPlan, FillStep
from donewitness.plan import PlanValidationError, load_plan, plan_digest

VALID_V2_PLAN: dict[str, object] = {
    "schema_version": 2,
    "task": "Verify greeting flow",
    "criteria": [
        {
            "id": "AC-001",
            "description": "A greeting becomes visible after entering a name",
            "procedure": {
                "type": "browser",
                "timeout_ms": 5000,
                "steps": [
                    {"type": "navigate", "path": "/"},
                    {"type": "fill", "selector": "#name", "value": "Ada"},
                    {"type": "click", "selector": "#greet"},
                    {"type": "assert_visible", "selector": "#message"},
                ],
            },
        }
    ],
}


def write_json(path: Path, data: object, *, indent: int | None = None) -> None:
    path.write_text(json.dumps(data, indent=indent), encoding="utf-8")


def test_load_plan_accepts_valid_v2_browser_plan(tmp_path: Path) -> None:
    path = tmp_path / "browser-plan.json"
    write_json(path, VALID_V2_PLAN)

    plan = load_plan(path)

    assert isinstance(plan, BrowserVerificationPlan)
    assert plan.schema_version == 2
    assert plan.criteria[0].procedure.steps[1] == FillStep(
        type="fill", selector="#name", value="Ada"
    )
    assert plan.criteria[0].procedure.model_config["frozen"] is True


def test_v2_formatting_equivalent_json_has_stable_digest(tmp_path: Path) -> None:
    compact = tmp_path / "compact.json"
    formatted = tmp_path / "formatted.json"
    write_json(compact, VALID_V2_PLAN)
    write_json(formatted, VALID_V2_PLAN, indent=4)

    compact_digest = plan_digest(load_plan(compact))

    assert compact_digest == plan_digest(load_plan(formatted))
    assert compact_digest == (
        "sha256:ce1d3f9f06844fd729228ad80a6868570ee1915244068668e0b342554ae6e2cc"
    )


def test_v2_digest_covers_the_entire_procedure(tmp_path: Path) -> None:
    original_path = tmp_path / "original.json"
    changed_path = tmp_path / "changed.json"
    changed = copy.deepcopy(VALID_V2_PLAN)
    criteria = changed["criteria"]
    assert isinstance(criteria, list)
    procedure = criteria[0]["procedure"]
    assert isinstance(procedure, dict)
    steps = procedure["steps"]
    assert isinstance(steps, list)
    steps[1]["value"] = "Grace"
    write_json(original_path, VALID_V2_PLAN)
    write_json(changed_path, changed)

    assert plan_digest(load_plan(original_path)) != plan_digest(load_plan(changed_path))


def test_load_plan_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "future.json"
    write_json(path, {"schema_version": 3, "task": "Task", "criteria": []})

    with pytest.raises(PlanValidationError, match="unsupported schema version: 3"):
        load_plan(path)


def test_load_plan_rejects_unsupported_browser_step(tmp_path: Path) -> None:
    path = tmp_path / "unsupported-step.json"
    data = copy.deepcopy(VALID_V2_PLAN)
    criteria = data["criteria"]
    assert isinstance(criteria, list)
    criteria[0]["procedure"]["steps"][1] = {"type": "execute_javascript"}
    write_json(path, data)

    with pytest.raises(PlanValidationError, match="execute_javascript"):
        load_plan(path)


def test_load_plan_rejects_unsupported_procedure_type(tmp_path: Path) -> None:
    path = tmp_path / "unsupported-procedure.json"
    data = copy.deepcopy(VALID_V2_PLAN)
    criteria = data["criteria"]
    assert isinstance(criteria, list)
    criteria[0]["procedure"]["type"] = "http"
    write_json(path, data)

    with pytest.raises(PlanValidationError, match="browser"):
        load_plan(path)


def test_load_plan_rejects_unknown_procedure_fields(tmp_path: Path) -> None:
    path = tmp_path / "unknown-field.json"
    data = copy.deepcopy(VALID_V2_PLAN)
    criteria = data["criteria"]
    assert isinstance(criteria, list)
    criteria[0]["procedure"]["browser"] = "chromium"
    write_json(path, data)

    with pytest.raises(PlanValidationError, match="criteria.0.procedure.browser"):
        load_plan(path)


@pytest.mark.parametrize("timeout_ms", [99, 30_001])
def test_load_plan_rejects_timeout_outside_bounded_range(
    tmp_path: Path,
    timeout_ms: int,
) -> None:
    path = tmp_path / "unbounded-timeout.json"
    data = copy.deepcopy(VALID_V2_PLAN)
    criteria = data["criteria"]
    assert isinstance(criteria, list)
    criteria[0]["procedure"]["timeout_ms"] = timeout_ms
    write_json(path, data)

    with pytest.raises(PlanValidationError, match="criteria.0.procedure.timeout_ms"):
        load_plan(path)


def test_load_plan_rejects_browser_procedure_without_assertion(tmp_path: Path) -> None:
    path = tmp_path / "no-assertion.json"
    data = copy.deepcopy(VALID_V2_PLAN)
    criteria = data["criteria"]
    assert isinstance(criteria, list)
    criteria[0]["procedure"]["steps"] = [
        {"type": "navigate", "path": "/"},
        {"type": "click", "selector": "#greet"},
    ]
    write_json(path, data)

    with pytest.raises(PlanValidationError, match="must contain at least one assert_visible"):
        load_plan(path)


def test_load_plan_rejects_procedure_whose_first_step_is_not_navigate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "wrong-first-step.json"
    data = copy.deepcopy(VALID_V2_PLAN)
    criteria = data["criteria"]
    assert isinstance(criteria, list)
    criteria[0]["procedure"]["steps"] = [
        {"type": "click", "selector": "#greet"},
        {"type": "assert_visible", "selector": "#message"},
    ]
    write_json(path, data)

    with pytest.raises(PlanValidationError, match="first browser step must be navigate"):
        load_plan(path)


@pytest.mark.parametrize(
    "path_value",
    ["https://example.com", "http://example.com", "//example.com/path", "/\\example.com"],
)
def test_load_plan_rejects_navigate_path_that_can_escape_local_origin(
    tmp_path: Path,
    path_value: str,
) -> None:
    path = tmp_path / "external-navigation.json"
    data = copy.deepcopy(VALID_V2_PLAN)
    criteria = data["criteria"]
    assert isinstance(criteria, list)
    criteria[0]["procedure"]["steps"][0]["path"] = path_value
    write_json(path, data)

    with pytest.raises(PlanValidationError, match="navigate path must begin with exactly one"):
        load_plan(path)


def test_fill_accepts_an_empty_strict_string_value() -> None:
    step = FillStep(type="fill", selector="#name", value="")

    assert step.value == ""
