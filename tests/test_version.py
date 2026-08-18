import subprocess
import sys
from pathlib import Path

from nage.version import package_version

ROOT = Path(__file__).parents[1]


def test_runtime_version_matches_canonical_project_metadata() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "package_version.py")],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == package_version() == "1.2.1"


def test_installer_numeric_version_is_derived_from_project_version() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "package_version.py"),
            "--format",
            "msi",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "1.2.1"
