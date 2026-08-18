from pathlib import Path

import pytest

from nage.core.path_policy import PathBoundary
from nage.errors import PolicyDeniedError, ToolError


def test_resolves_relative_path_inside_root(tmp_path: Path) -> None:
    target = tmp_path / "folder" / "file.txt"
    target.parent.mkdir()
    target.write_text("content", encoding="utf-8")

    resolved = PathBoundary(tmp_path).resolve_for_read("folder/file.txt")

    assert resolved.absolute == target.resolve()
    assert resolved.relative == Path("folder/file.txt")


def test_rejects_parent_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(PolicyDeniedError, match="outside"):
        PathBoundary(tmp_path).resolve_for_read("../outside.txt")


def test_rejects_absolute_path_outside_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(PolicyDeniedError, match="outside"):
        PathBoundary(tmp_path).resolve_for_read(outside)


def test_allows_new_write_target_inside_root(tmp_path: Path) -> None:
    resolved = PathBoundary(tmp_path).resolve_for_write("new/file.txt")

    assert resolved.absolute == (tmp_path / "new" / "file.txt").resolve()


def test_rejects_missing_read_target(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="does not exist"):
        PathBoundary(tmp_path).resolve_for_read("missing.txt")


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-directory"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "outside-link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(PolicyDeniedError, match="outside"):
        PathBoundary(tmp_path).resolve_for_read(
            "outside-link/file.txt", must_exist=False
        )


def test_write_refuses_injected_protected_application_path(tmp_path: Path) -> None:
    protected = tmp_path / "custom-data"
    protected.mkdir()
    boundary = PathBoundary(tmp_path, protected_paths=(protected,))

    with pytest.raises(PolicyDeniedError, match="protected"):
        boundary.resolve_for_write("custom-data/config.json")


def test_write_refuses_target_symlink_even_inside_root(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("content", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(PolicyDeniedError, match="symbolic link"):
        PathBoundary(tmp_path).resolve_for_write("link.txt", must_exist=True)
