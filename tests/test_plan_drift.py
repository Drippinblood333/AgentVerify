"""Canonical post-snapshot plan-drift detection tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentverify.plan import load_plan, plan_digest
from agentverify.run import detect_plan_drift

PLAN = {
    "schema_version": 2,
    "task": "Verify greeting",
    "criteria": [
        {
            "id": "AC-001",
            "description": "Greeting appears",
            "procedure": {
                "type": "browser",
                "steps": [
                    {"type": "navigate", "path": "/"},
                    {"type": "assert_visible", "selector": "#message"},
                ],
            },
        }
    ],
}


def write_plan(path: Path, payload: object = PLAN, *, indent: int | None = None) -> None:
    path.write_text(json.dumps(payload, indent=indent), encoding="utf-8")


def test_unchanged_and_canonical_equivalent_plan_do_not_warn(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    write_plan(path)
    expected = plan_digest(load_plan(path))

    assert detect_plan_drift(path, expected_digest=expected) is None
    reordered = {
        "criteria": PLAN["criteria"],
        "task": PLAN["task"],
        "schema_version": 2,
    }
    write_plan(path, reordered, indent=4)
    assert detect_plan_drift(path, expected_digest=expected) is None


def test_semantic_plan_change_warns_without_replacing_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    write_plan(path)
    expected = plan_digest(load_plan(path))
    changed = json.loads(json.dumps(PLAN))
    changed["criteria"][0]["description"] = "Different criterion"
    write_plan(path, changed)

    warning = detect_plan_drift(path, expected_digest=expected)
    assert warning is not None
    assert "changed after verification snapshot" in warning
    assert expected in warning


@pytest.mark.parametrize("replacement", [None, "{"])
def test_deleted_or_invalid_plan_warns(tmp_path: Path, replacement: str | None) -> None:
    path = tmp_path / "plan.json"
    write_plan(path)
    expected = plan_digest(load_plan(path))
    if replacement is None:
        path.unlink()
    else:
        path.write_text(replacement, encoding="utf-8")

    warning = detect_plan_drift(path, expected_digest=expected)
    assert warning is not None
    assert "no longer validates" in warning
    assert expected in warning
