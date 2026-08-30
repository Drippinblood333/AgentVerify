"""Narrow assertions for the proposed public v0.1 release contract."""

from __future__ import annotations

import tomllib
from pathlib import Path

from agentverify import __version__
from agentverify.cli import EXIT_FAIL, EXIT_PASS, EXIT_UNKNOWN, EXIT_USAGE
from agentverify.cli_result import OUTPUT_SCHEMA_VERSION

REPOSITORY_ROOT = Path(__file__).parents[1]


def test_v0_1_versions_and_exit_codes_are_stable() -> None:
    assert __version__ == "0.1.0"
    assert OUTPUT_SCHEMA_VERSION == 1
    assert (EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_UNKNOWN) == (0, 1, 2, 3)


def test_declared_python_support_matches_release_matrix() -> None:
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = pyproject["project"]
    assert project["name"] == "agentverify-evidence"
    assert project["requires-python"] == ">=3.12,<3.15"
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [{"name": "AgentVerify contributors"}]
    assert project["dependencies"] == ["playwright>=1.61,<2", "pydantic>=2,<3"]
    assert project["urls"] == {
        "Homepage": "https://github.com/Drippinblood333/AgentVerify",
        "Repository": "https://github.com/Drippinblood333/AgentVerify",
        "Issues": "https://github.com/Drippinblood333/AgentVerify/issues",
        "Changelog": (
            "https://github.com/Drippinblood333/AgentVerify/blob/main/CHANGELOG.md"
        ),
        "Security": "https://github.com/Drippinblood333/AgentVerify/security/policy",
    }
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
    assert "Development Status :: 3 - Alpha" in classifiers
    assert "Development Status :: 2 - Pre-Alpha" not in classifiers


def test_release_facing_install_instructions_use_the_public_distribution() -> None:
    release_files = [
        REPOSITORY_ROOT / "README.md",
        REPOSITORY_ROOT / "docs" / "CI.md",
        REPOSITORY_ROOT / "docs" / "PACKAGING.md",
        REPOSITORY_ROOT / "docs" / "RELEASE.md",
        REPOSITORY_ROOT / "docs" / "releases" / "v0.1.0.md",
    ]
    for path in release_files:
        content = path.read_text(encoding="utf-8")
        assert "pip install agentverify-evidence" in content, path
        incorrect_lines = [
            line
            for line in content.splitlines()
            if "pip install agentverify" in line
            and "pip install agentverify-evidence" not in line
        ]
        if path.name == "README.md":
            assert incorrect_lines == [
                "`pip install agentverify` installs a different project; "
                "it does not install AgentVerify."
            ]
        else:
            assert not incorrect_lines, path


def test_core_source_contains_no_ci_vendor_contract() -> None:
    forbidden = ("GITHUB_", "GITLAB_", "JENKINS_", "BUILD_BUILDID")
    for source in (REPOSITORY_ROOT / "src" / "agentverify").glob("*.py"):
        content = source.read_text(encoding="utf-8")
        assert not any(token in content for token in forbidden), source


def test_release_workflow_is_manual_build_once_and_least_privilege() -> None:
    release = (REPOSITORY_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    normal_ci = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in release
    assert "\n  push:" not in release
    assert "\n  pull_request:" not in release
    assert release.count("python -m build") == 1
    assert release.count("id-token: write") == 1
    assert release.count("contents: write") == 1
    assert "environment: pypi" in release
    assert "pypa/gh-action-pypi-publish@release/v1" in release
    assert "attestations: false" in release
    assert "PYPI_TOKEN" not in release
    assert "release-sha={tag_commit}" in release
    assert release.count("ref: ${{ needs.validate-ref.outputs.release-sha }}") == 4
    assert "id-token: write" not in normal_ci
    assert "gh-action-pypi-publish" not in normal_ci
