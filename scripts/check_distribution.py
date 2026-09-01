"""Check AgentVerify wheel and sdist contents using only the standard library."""

from __future__ import annotations

import argparse
import configparser
import tarfile
import zipfile
from email import policy
from email.message import Message
from email.parser import BytesParser
from io import StringIO
from pathlib import Path, PurePosixPath

EXPECTED_DISTRIBUTION_NAME = "agentverify-evidence"
EXPECTED_FILENAME_STEM = "agentverify_evidence"
EXPECTED_IMPORT_PACKAGE = "agentverify_evidence"
FORBIDDEN_IMPORT_PACKAGE = "agentverify"
EXPECTED_VERSION = "0.1.0"
EXPECTED_REQUIRES_PYTHON_PARTS = frozenset({">=3.12", "<3.15"})
EXPECTED_CONSOLE_SCRIPT = "agentverify_evidence.cli:main"
EXPECTED_LICENSE_EXPRESSION = "Apache-2.0"
EXPECTED_PROJECT_URLS = {
    "Homepage": "https://github.com/Drippinblood333/AgentVerify",
    "Repository": "https://github.com/Drippinblood333/AgentVerify",
    "Issues": "https://github.com/Drippinblood333/AgentVerify/issues",
    "Changelog": "https://github.com/Drippinblood333/AgentVerify/blob/main/CHANGELOG.md",
    "Security": "https://github.com/Drippinblood333/AgentVerify/security/policy",
}
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
    expected_stem = f"{EXPECTED_FILENAME_STEM}-{EXPECTED_VERSION}"
    if wheels[0].name != f"{expected_stem}-py3-none-any.whl":
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


def _assert_import_namespace(
    names: list[str],
    *,
    source: str,
) -> None:
    paths = [PurePosixPath(name.replace("\\", "/")) for name in names]
    if not any(
        _contains_import_package(path, EXPECTED_IMPORT_PACKAGE, source=source)
        for path in paths
    ):
        raise ValueError(
            f"{source} does not contain the {EXPECTED_IMPORT_PACKAGE} import package"
        )
    if any(
        _contains_import_package(path, FORBIDDEN_IMPORT_PACKAGE, source=source)
        for path in paths
    ):
        raise ValueError(
            f"{source} exposes forbidden top-level import package {FORBIDDEN_IMPORT_PACKAGE}"
        )


def _contains_import_package(
    path: PurePosixPath,
    package: str,
    *,
    source: str,
) -> bool:
    if source == "wheel":
        return bool(path.parts) and path.parts[0] == package
    if source == "sdist":
        return any(
            path.parts[index : index + 2] == ("src", package)
            for index in range(len(path.parts) - 1)
        )
    raise ValueError(f"unsupported distribution source: {source}")


def _requires_python(metadata: bytes) -> str | None:
    message = BytesParser(policy=policy.default).parsebytes(metadata)
    return message.get("Requires-Python")


def _metadata_message(metadata: bytes) -> Message:
    return BytesParser(policy=policy.default).parsebytes(metadata)


def _project_urls(metadata: bytes) -> dict[str, str]:
    message = _metadata_message(metadata)
    parsed: dict[str, str] = {}
    for value in message.get_all("Project-URL", []):
        label, separator, url = value.partition(",")
        if not separator:
            raise ValueError(f"invalid Project-URL metadata: {value!r}")
        parsed[label.strip()] = url.strip()
    return parsed


def _assert_package_metadata(metadata: bytes, *, source: str) -> None:
    message = _metadata_message(metadata)
    expected = {
        "Name": EXPECTED_DISTRIBUTION_NAME,
        "Version": EXPECTED_VERSION,
        "License-Expression": EXPECTED_LICENSE_EXPRESSION,
    }
    for field, value in expected.items():
        if message.get(field) != value:
            raise ValueError(
                f"unexpected {source} {field}: {message.get(field)!r}"
            )
    license_files = set(message.get_all("License-File", []))
    if "LICENSE" not in license_files:
        raise ValueError(f"{source} metadata does not declare LICENSE")
    if _project_urls(metadata) != EXPECTED_PROJECT_URLS:
        raise ValueError(f"unexpected {source} Project-URL metadata")
    _assert_requires_python(message.get("Requires-Python"), source=source)


def _assert_license_contents(contents: bytes, *, source: str) -> None:
    text = contents.decode("utf-8")
    if "Apache License" not in text or "Version 2.0, January 2004" not in text:
        raise ValueError(f"{source} LICENSE is not the expected Apache-2.0 text")


def _assert_pure_wheel(metadata: bytes) -> None:
    message = _metadata_message(metadata)
    if message.get("Root-Is-Purelib") != "true":
        raise ValueError("wheel does not declare Root-Is-Purelib: true")
    if message.get_all("Tag", []) != ["py3-none-any"]:
        raise ValueError(f"unexpected wheel compatibility tag: {message.get_all('Tag', [])!r}")


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
        _assert_import_namespace(
            names,
            source="wheel",
        )
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        entry_point_names = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        wheel_metadata_names = [
            name for name in names if name.endswith(".dist-info/WHEEL")
        ]
        license_names = [
            name for name in names if name.endswith(".dist-info/licenses/LICENSE")
        ]
        required = {
            "metadata": len(metadata_names) == 1,
            "entry point": len(entry_point_names) == 1,
            "wheel metadata": len(wheel_metadata_names) == 1,
            "license": len(license_names) == 1,
        }
        metadata = archive.read(metadata_names[0]) if len(metadata_names) == 1 else b""
        entry_points = (
            archive.read(entry_point_names[0]) if len(entry_point_names) == 1 else b""
        )
        wheel_metadata = (
            archive.read(wheel_metadata_names[0])
            if len(wheel_metadata_names) == 1
            else b""
        )
        license_contents = (
            archive.read(license_names[0]) if len(license_names) == 1 else b""
        )
    missing = [label for label, present in required.items() if not present]
    if missing:
        raise ValueError(f"wheel is missing: {', '.join(missing)}")
    _assert_package_metadata(metadata, source="wheel")
    _assert_pure_wheel(wheel_metadata)
    _assert_license_contents(license_contents, source="wheel")
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
            if PurePosixPath(member.name).parent.name.startswith(
                f"{EXPECTED_FILENAME_STEM}-"
            )
            and PurePosixPath(member.name).name == "PKG-INFO"
        ]
        metadata_file = (
            archive.extractfile(metadata_members[0])
            if len(metadata_members) == 1
            else None
        )
        metadata = metadata_file.read() if metadata_file is not None else b""
        license_members = [
            member
            for member in archive.getmembers()
            if PurePosixPath(member.name).name == "LICENSE"
            and PurePosixPath(member.name).parent.name
            == f"{EXPECTED_FILENAME_STEM}-{EXPECTED_VERSION}"
        ]
        license_file = (
            archive.extractfile(license_members[0])
            if len(license_members) == 1
            else None
        )
        license_contents = license_file.read() if license_file is not None else b""
    _assert_safe_names(names)
    _assert_import_namespace(
        names,
        source="sdist",
    )
    required_suffixes = (
        "/pyproject.toml",
        "/README.md",
        "/LICENSE",
        "/src/agentverify_evidence/__init__.py",
        "/src/agentverify_evidence/__main__.py",
        "/src/agentverify_evidence/cli.py",
        "/src/agentverify_evidence/cli_result.py",
    )
    missing = [
        suffix
        for suffix in required_suffixes
        if not any(name.endswith(suffix) for name in names)
    ]
    if missing:
        raise ValueError(f"sdist is missing: {', '.join(missing)}")
    _assert_package_metadata(metadata, source="sdist")
    if len(license_members) != 1:
        raise ValueError("sdist must contain exactly one root LICENSE")
    _assert_license_contents(license_contents, source="sdist")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", type=Path)
    args = parser.parse_args()
    wheel, sdist = distribution_files(args.dist_dir.resolve())
    check_wheel(wheel)
    check_sdist(sdist)
    print(f"Wheel: {wheel.name}")
    print(f"Sdist: {sdist.name}")
    print(f"Name: {EXPECTED_DISTRIBUTION_NAME}")
    print(f"Version: {EXPECTED_VERSION}")
    print(f"License-Expression: {EXPECTED_LICENSE_EXPRESSION}")
    print("Requires-Python: >=3.12,<3.15 (exact semantic bounds)")
    print(f"Console script: agentverify = {EXPECTED_CONSOLE_SCRIPT}")
    print(f"Python import package: {EXPECTED_IMPORT_PACKAGE}")
    print(f"Forbidden import package absent: {FORBIDDEN_IMPORT_PACKAGE}")
    print("Distribution contents: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
