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
    (tmp_path / "donewitness-0.1.0-py3-none-any.whl").touch()

    with pytest.raises(ValueError, match="exactly one wheel and one sdist"):
        checker.distribution_files(tmp_path)


def test_distribution_names_match_public_version_and_pure_python_tag(
    tmp_path: Path,
) -> None:
    checker = _load_script("check_distribution.py")
    wheel = tmp_path / "donewitness-0.1.0-py3-none-any.whl"
    sdist = tmp_path / "donewitness-0.1.0.tar.gz"
    wheel.touch()
    sdist.touch()

    assert checker.distribution_files(tmp_path) == (wheel, sdist)


def test_smoke_source_guard_rejects_repository_import(tmp_path: Path) -> None:
    smoke = _load_script("smoke_distribution.py")
    repository = tmp_path / "repository"
    module = repository / "src" / "donewitness" / "__init__.py"
    module.parent.mkdir(parents=True)
    module.touch()

    with pytest.raises(RuntimeError, match="did not import from the smoke environment"):
        smoke._assert_installed_source(module, tmp_path / "venv", repository)


def test_distribution_namespace_guard_requires_new_package_and_rejects_old() -> None:
    checker = _load_script("check_distribution.py")

    checker._assert_import_namespace(
        ["donewitness/__init__.py"],
        source="wheel",
    )
    with pytest.raises(ValueError, match="does not contain"):
        checker._assert_import_namespace(
            ["nested/donewitness/__init__.py"],
            source="wheel",
        )
    with pytest.raises(ValueError, match="forbidden top-level import package"):
        checker._assert_import_namespace(
            ["donewitness/__init__.py", "agentverify/__init__.py"],
            source="wheel",
        )
    with pytest.raises(ValueError, match="forbidden top-level import package"):
        checker._assert_import_namespace(
            ["donewitness/__init__.py", "agentverify_evidence/__init__.py"],
            source="wheel",
        )


def test_console_script_mapping_requires_only_donewitness() -> None:
    checker = _load_script("check_distribution.py")

    mapping = checker._console_script_mapping(
        b"[console_scripts]\ndonewitness = donewitness.cli:main\n"
    )
    assert mapping == {"donewitness": "donewitness.cli:main"}


def test_smoke_uses_the_owned_environment_console_entrypoint(tmp_path: Path) -> None:
    smoke = _load_script("smoke_distribution.py")

    expected = tmp_path / (
        "Scripts/donewitness.exe" if smoke.os.name == "nt" else "bin/donewitness"
    )
    assert smoke._venv_donewitness(tmp_path) == expected


def test_built_metadata_python_range_allows_only_qualified_versions() -> None:
    checker = _load_script("check_distribution.py")
    smoke = _load_script("smoke_distribution.py")

    checker._assert_requires_python(">=3.12,<3.15", source="test")
    checker._assert_requires_python("<3.15,>=3.12", source="test")
    assert smoke._requires_python_is_supported_range("<3.15,>=3.12") is True
    assert smoke._requires_python_is_supported_range(">=3.12") is False
    with pytest.raises(ValueError, match="unexpected test Requires-Python"):
        checker._assert_requires_python(">=3.12,<3.16", source="test")
