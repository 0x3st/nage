import shutil
import subprocess
from pathlib import Path

import pytest

from nage.errors import PolicyDeniedError
from nage.paths import AppPaths
from nage.storage import ArtifactStore, Database
from nage.tools import ToolContext
from nage.tools.git import build_git_tools

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git unavailable")


def specs():
    return {spec.name: spec for spec in build_git_tools()}


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        [shutil.which("git"), "-C", str(root)] + list(arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Nage Test")
    git(tmp_path, "config", "user.email", "nage@example.test")
    (tmp_path / "visible.txt").write_text("initial\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=initial\n", encoding="utf-8")
    git(tmp_path, "add", "visible.txt", ".env")
    git(tmp_path, "commit", "-q", "-m", "initial commit")
    return tmp_path


def artifact_store(tmp_path: Path) -> ArtifactStore:
    paths = AppPaths(tmp_path / ".test-data").ensure()
    database = Database(paths.database_file)
    database.initialize()
    return ArtifactStore(database, paths)


def test_git_status_omits_likely_sensitive_paths(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "visible.txt").write_text("visible change\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=changed-secret\n", encoding="utf-8")
    spec = specs()["git.status"]

    result = spec.execute(spec.validate_arguments({}), ToolContext("request-1", root))

    assert any(item["path"] == "visible.txt" for item in result.data["changes"])
    assert all(item["path"] != ".env" for item in result.data["changes"])
    assert result.data["sensitive_paths_omitted"] == 1


def test_git_diff_filters_sensitive_file_content(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "visible.txt").write_text("VISIBLE_CHANGE\n", encoding="utf-8")
    (root / ".env").write_text("SECRET_CHANGE\n", encoding="utf-8")
    store = artifact_store(root)
    spec = specs()["git.diff"]

    result = spec.execute(
        spec.validate_arguments({}),
        ToolContext("request-1", root, artifact_writer=store),
    )

    assert "VISIBLE_CHANGE" in result.data["diff"]
    assert "SECRET_CHANGE" not in result.data["diff"]
    assert ".env" not in result.data["files"]
    assert result.warnings


def test_git_diff_refuses_explicit_secret_path(tmp_path: Path) -> None:
    root = repository(tmp_path)
    spec = specs()["git.diff"]

    with pytest.raises(PolicyDeniedError, match="secret"):
        spec.execute(
            spec.validate_arguments({"path": ".env"}),
            ToolContext("request-1", root),
        )


def test_git_log_returns_bounded_structured_commits(tmp_path: Path) -> None:
    root = repository(tmp_path)
    spec = specs()["git.log"]

    result = spec.execute(
        spec.validate_arguments({"limit": 5}), ToolContext("request-1", root)
    )

    assert len(result.data["commits"]) == 1
    assert result.data["commits"][0]["subject"] == "initial commit"
    assert len(result.data["commits"][0]["commit"]) == 40
