"""Narrow assertions for M10 release-qualification declarations."""

from __future__ import annotations

import tomllib
from pathlib import Path

from agentverify import __version__
from agentverify.cli import EXIT_FAIL, EXIT_PASS, EXIT_UNKNOWN, EXIT_USAGE
from agentverify.cli_result import OUTPUT_SCHEMA_VERSION

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_m10_versions_and_exit_codes_are_stable() -> None:
    assert __version__ == "0.1.0.dev0"
    assert OUTPUT_SCHEMA_VERSION == 1
    assert (EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_UNKNOWN) == (0, 1, 2, 3)


def test_declared_python_support_matches_release_matrix() -> None:
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = pyproject["project"]
    assert project["requires-python"] == ">=3.12,<3.15"
    classifiers = set(project["classifiers"])
    expected_python_classifiers = {
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    }
    version_classifiers = {
        classifier
        for classifier in classifiers
        if classifier.startswith("Programming Language :: Python :: 3.")
    }
    assert version_classifiers == expected_python_classifiers


def test_core_source_contains_no_ci_vendor_contract() -> None:
    forbidden = ("GITHUB_", "GITLAB_", "JENKINS_", "BUILD_BUILDID")
    for source in (REPOSITORY_ROOT / "src" / "agentverify").glob("*.py"):
        content = source.read_text(encoding="utf-8")
        assert not any(token in content for token in forbidden), source
