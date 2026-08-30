"""Check AgentVerify wheel and sdist contents using only the standard library."""

from __future__ import annotations

import argparse
import configparser
import tarfile
import zipfile
from email import policy
from email.parser import BytesParser
from io import StringIO
from pathlib import Path, PurePosixPath

EXPECTED_VERSION = "0.1.0.dev0"
EXPECTED_REQUIRES_PYTHON_PARTS = frozenset({">=3.12", "<3.15"})
EXPECTED_CONSOLE_SCRIPT = "agentverify.cli:main"
FORBIDDEN_PARTS = {
    ".agentverify",
    ".git",
    ".github",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "worktrees",
}
FORBIDDEN_NAME_FRAGMENTS = ("credential", "secret", "token")


def distribution_files(dist_dir: Path) -> tuple[Path, Path]:
    """Return the sole expected wheel and sdist from a build directory."""
    wheels = sorted(dist_dir.glob("*.whl"))
    sdists = sorted(dist_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError(
            f"expected exactly one wheel and one sdist, found {len(wheels)} wheel(s) "
            f"and {len(sdists)} sdist(s)"
        )
    expected_stem = f"agentverify-{EXPECTED_VERSION}"
    if wheels[0].name != f"agentverify-{EXPECTED_VERSION}-py3-none-any.whl":
        raise ValueError(f"unexpected wheel filename: {wheels[0].name}")
    if sdists[0].name != f"{expected_stem}.tar.gz":
        raise ValueError(f"unexpected sdist filename: {sdists[0].name}")
    return wheels[0], sdists[0]


def _assert_safe_names(names: list[str]) -> None:
    for raw_name in names:
        path = PurePosixPath(raw_name.replace("\\", "/"))
        lower_parts = {part.lower() for part in path.parts}
        if lower_parts & FORBIDDEN_PARTS:
            raise ValueError(f"forbidden distribution path: {raw_name}")
        lower_name = path.name.lower()
        if any(fragment in lower_name for fragment in FORBIDDEN_NAME_FRAGMENTS):
            raise ValueError(f"suspicious distribution filename: {raw_name}")


def _requires_python(metadata: bytes) -> str | None:
    message = BytesParser(policy=policy.default).parsebytes(metadata)
    return message.get("Requires-Python")


def _assert_requires_python(value: str | None, *, source: str) -> None:
    parts = (
        frozenset(part.strip() for part in value.split(","))
        if value is not None
        else frozenset()
    )
    if parts != EXPECTED_REQUIRES_PYTHON_PARTS:
        raise ValueError(f"unexpected {source} Requires-Python: {value!r}")


def _console_script_mapping(entry_points: bytes) -> str | None:
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_file(StringIO(entry_points.decode("utf-8")))
    if not parser.has_section("console_scripts"):
        return None
    return parser.get("console_scripts", "agentverify", fallback=None)


def check_wheel(wheel: Path) -> None:
    """Require package code, metadata, entry point, and license without local debris."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        _assert_safe_names(names)
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entry_point_names = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        required = {
            "package": any(name.startswith("agentverify/") for name in names),
            "metadata": len(metadata_names) == 1,
            "entry point": len(entry_point_names) == 1,
            "license": any(".dist-info/licenses/LICENSE" in name for name in names),
        }
        metadata = archive.read(metadata_names[0]) if len(metadata_names) == 1 else b""
        entry_points = (
            archive.read(entry_point_names[0]) if len(entry_point_names) == 1 else b""
        )
    missing = [label for label, present in required.items() if not present]
    if missing:
        raise ValueError(f"wheel is missing: {', '.join(missing)}")
    requires_python = _requires_python(metadata)
    _assert_requires_python(requires_python, source="wheel")
    console_script = _console_script_mapping(entry_points)
    if console_script != EXPECTED_CONSOLE_SCRIPT:
        raise ValueError(f"unexpected agentverify console script: {console_script!r}")


def check_sdist(sdist: Path) -> None:
    """Require the build metadata and package source needed for installation."""
    with tarfile.open(sdist, mode="r:gz") as archive:
        names = archive.getnames()
        metadata_members = [
            member
            for member in archive.getmembers()
            if PurePosixPath(member.name).parent.name.startswith("agentverify-")
            and PurePosixPath(member.name).name == "PKG-INFO"
        ]
        metadata_file = (
            archive.extractfile(metadata_members[0])
            if len(metadata_members) == 1
            else None
        )
        metadata = metadata_file.read() if metadata_file is not None else b""
    _assert_safe_names(names)
    required_suffixes = (
        "/pyproject.toml",
        "/README.md",
        "/LICENSE",
        "/src/agentverify/__init__.py",
        "/src/agentverify/__main__.py",
        "/src/agentverify/cli.py",
        "/src/agentverify/cli_result.py",
    )
    missing = [
        suffix
        for suffix in required_suffixes
        if not any(name.endswith(suffix) for name in names)
    ]
    if missing:
        raise ValueError(f"sdist is missing: {', '.join(missing)}")
    requires_python = _requires_python(metadata)
    _assert_requires_python(requires_python, source="sdist")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", type=Path)
    args = parser.parse_args()
    wheel, sdist = distribution_files(args.dist_dir.resolve())
    check_wheel(wheel)
    check_sdist(sdist)
    print(f"Wheel: {wheel.name}")
    print(f"Sdist: {sdist.name}")
    print("Requires-Python: >=3.12,<3.15 (exact semantic bounds)")
    print(f"Console script: agentverify = {EXPECTED_CONSOLE_SCRIPT}")
    print("Distribution contents: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
