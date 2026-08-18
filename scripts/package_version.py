#!/usr/bin/env python3
import argparse
import re
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python 3.8-3.10 compatibility
    tomllib = None


def project_version() -> str:
    root = Path(__file__).resolve().parents[1]
    path = root / "pyproject.toml"
    if tomllib is not None:
        with path.open("rb") as source:
            value = tomllib.load(source)["project"]["version"]
    else:
        text = path.read_text(encoding="utf-8")
        project = text.split("[project]", 1)[1].split("\n[", 1)[0]
        match = re.search(r'^version\s*=\s*"([^"]+)"', project, re.MULTILINE)
        value = "" if match is None else match.group(1)
    if not isinstance(value, str) or not value:
        raise RuntimeError("project.version is missing")
    return value


def numeric_version(value: str) -> str:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    if match is None:
        raise RuntimeError("project.version must begin with major.minor.patch")
    return ".".join(match.groups())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--format",
        choices=("pep440", "deb", "msi", "macos"),
        default="pep440",
    )
    arguments = parser.parse_args()
    version = project_version()
    if arguments.format == "msi":
        version = numeric_version(version)
    print(version)


if __name__ == "__main__":
    main()
