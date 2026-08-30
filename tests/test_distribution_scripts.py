"""Cheap unit coverage for the distribution release-test helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]


def _load_script(name: str) -> ModuleType:
    path = REPOSITORY_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_distribution_files_requires_exact_artifact_pair(tmp_path: Path) -> None:
    checker = _load_script("check_distribution.py")
    (tmp_path / "agentverify-0.1.0.dev0-py3-none-any.whl").touch()

    with pytest.raises(ValueError, match="exactly one wheel and one sdist"):
        checker.distribution_files(tmp_path)


def test_distribution_names_match_m10_version_and_pure_python_tag(
    tmp_path: Path,
) -> None:
    checker = _load_script("check_distribution.py")
    wheel = tmp_path / "agentverify-0.1.0.dev0-py3-none-any.whl"
    sdist = tmp_path / "agentverify-0.1.0.dev0.tar.gz"
    wheel.touch()
    sdist.touch()

    assert checker.distribution_files(tmp_path) == (wheel, sdist)


def test_smoke_source_guard_rejects_repository_import(tmp_path: Path) -> None:
    smoke = _load_script("smoke_distribution.py")
    repository = tmp_path / "repository"
    module = repository / "src" / "agentverify" / "__init__.py"
    module.parent.mkdir(parents=True)
    module.touch()

    with pytest.raises(RuntimeError, match="did not import from the smoke environment"):
        smoke._assert_installed_source(module, tmp_path / "venv", repository)
