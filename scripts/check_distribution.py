"""Check AgentVerify wheel and sdist contents using only the standard library."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

EXPECTED_VERSION = "0.1.0.dev0"
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


def check_wheel(wheel: Path) -> None:
    """Require package code, metadata, entry point, and license without local debris."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        _assert_safe_names(names)
        required = {
            "package": any(name.startswith("agentverify/") for name in names),
            "metadata": any(name.endswith(".dist-info/METADATA") for name in names),
            "entry point": any(
                name.endswith(".dist-info/entry_points.txt") for name in names
            ),
            "license": any(".dist-info/licenses/LICENSE" in name for name in names),
        }
    missing = [label for label, present in required.items() if not present]
    if missing:
        raise ValueError(f"wheel is missing: {', '.join(missing)}")


def check_sdist(sdist: Path) -> None:
    """Require the build metadata and package source needed for installation."""
    with tarfile.open(sdist, mode="r:gz") as archive:
        names = archive.getnames()
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", type=Path)
    args = parser.parse_args()
    wheel, sdist = distribution_files(args.dist_dir.resolve())
    check_wheel(wheel)
    check_sdist(sdist)
    print(f"Wheel: {wheel.name}")
    print(f"Sdist: {sdist.name}")
    print("Distribution contents: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
