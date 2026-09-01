"""Narrow assertions for the proposed public v0.1 release contract."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from donewitness import __version__
from donewitness.cli import EXIT_FAIL, EXIT_PASS, EXIT_UNKNOWN, EXIT_USAGE, build_parser
from donewitness.cli_result import OUTPUT_SCHEMA_VERSION

REPOSITORY_ROOT = Path(__file__).parents[1]

RELEASE_ACTION_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "pypa/gh-action-pypi-publish": "dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}


def test_v0_1_versions_and_exit_codes_are_stable() -> None:
    assert __version__ == "0.1.0"
    assert OUTPUT_SCHEMA_VERSION == 1
    assert (EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_UNKNOWN) == (0, 1, 2, 3)


def test_declared_python_support_matches_release_matrix() -> None:
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    project = pyproject["project"]
    assert project["name"] == "donewitness"
    assert project["requires-python"] == ">=3.12,<3.15"
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == [{"name": "DoneWitness contributors"}]
    assert project["dependencies"] == ["playwright>=1.61,<2", "pydantic>=2,<3"]
    assert project["scripts"] == {"donewitness": "donewitness.cli:main"}
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "donewitness.__version__"
    }
    assert pyproject["tool"]["setuptools"]["packages"]["find"] == {
        "where": ["src"],
        "include": ["donewitness*"],
    }
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


def test_public_identity_is_single_and_old_packages_are_absent() -> None:
    pyproject = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert __version__ == "0.1.0"
    assert build_parser().prog == "donewitness"
    assert pyproject["project"]["name"] == "donewitness"
    assert pyproject["project"]["scripts"] == {
        "donewitness": "donewitness.cli:main"
    }
    assert (REPOSITORY_ROOT / "src" / "donewitness" / "__main__.py").is_file()
    assert not (REPOSITORY_ROOT / "src" / "agentverify").exists()
    assert not (REPOSITORY_ROOT / "src" / "agentverify_evidence").exists()

    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    assert "# DoneWitness" in readme
    assert "python -m pip install donewitness" in readme
    assert "donewitness --version" in readme
    assert "python -m donewitness" in readme


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
        assert "pip install donewitness" in content, path


def test_core_source_contains_no_ci_vendor_contract() -> None:
    forbidden = ("GITHUB_", "GITLAB_", "JENKINS_", "BUILD_BUILDID")
    sources = list((REPOSITORY_ROOT / "src" / "donewitness").glob("*.py"))
    assert sources
    for source in sources:
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
    assert "attestations: false" in release
    assert "PYPI_TOKEN" not in release
    assert "release-sha={tag_commit}" in release
    assert release.count("ref: ${{ needs.validate-ref.outputs.release-sha }}") == 4
    assert "id-token: write" not in normal_ci
    assert "gh-action-pypi-publish" not in normal_ci


def test_release_workflow_actions_are_immutable_and_allowlisted() -> None:
    release = (REPOSITORY_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    action_uses = re.findall(
        r"^\s*(?:-\s*)?uses:\s+([^\s#]+)", release, flags=re.MULTILINE
    )
    external_uses = [action for action in action_uses if not action.startswith("./")]

    assert external_uses
    assert all(
        re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+@[0-9a-f]{40}", action)
        for action in external_uses
    )

    observed_repositories = {action.rsplit("@", 1)[0] for action in external_uses}
    assert observed_repositories == set(RELEASE_ACTION_PINS)
    assert all(
        RELEASE_ACTION_PINS[repository] == revision
        for repository, revision in (action.rsplit("@", 1) for action in external_uses)
    )
