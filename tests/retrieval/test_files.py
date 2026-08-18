from pathlib import Path

import pytest

from nage.errors import PolicyDeniedError, ToolError
from nage.retrieval import FileRetriever, detect_repository_root, is_binary_data


def test_detect_repository_root_from_nested_directory(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)

    assert detect_repository_root(nested) == tmp_path.resolve()


def test_file_iteration_respects_default_and_ignore_files(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / ".nageignore").write_text("private/\n", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "data.txt").write_text("private", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.js").write_text("ignored", encoding="utf-8")

    paths = {
        path.relative_to(tmp_path).as_posix()
        for path in FileRetriever(tmp_path).iter_files()
    }

    assert "visible.txt" in paths
    assert "ignored.txt" not in paths
    assert "private/data.txt" not in paths
    assert "node_modules/package.js" not in paths


def test_file_iteration_respects_nested_gitignore(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (nested / "ignored.log").write_text("ignored", encoding="utf-8")
    (nested / "visible.txt").write_text("visible", encoding="utf-8")

    paths = {
        path.relative_to(tmp_path).as_posix()
        for path in FileRetriever(tmp_path).iter_files()
    }

    assert "nested/ignored.log" not in paths
    assert "nested/visible.txt" in paths


def test_file_iteration_excludes_likely_secrets_but_keeps_env_example(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / ".env.example").write_text("TOKEN=", encoding="utf-8")
    (tmp_path / "private.pem").write_text("secret", encoding="utf-8")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "config").write_text("secret", encoding="utf-8")

    paths = {
        path.relative_to(tmp_path).as_posix()
        for path in FileRetriever(tmp_path).iter_files()
    }

    assert ".env" not in paths
    assert "private.pem" not in paths
    assert ".ssh/config" not in paths
    assert ".env.example" in paths


def test_search_text_is_bounded_and_returns_line_numbers(tmp_path: Path) -> None:
    (tmp_path / "first.txt").write_text("one\nneedle here\nthree\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("another needle\n", encoding="utf-8")

    matches = FileRetriever(tmp_path).search_text("needle", limit=1)

    assert len(matches) == 1
    assert matches[0].relative_path == "first.txt"
    assert matches[0].line_number == 2


def test_find_by_name_supports_literal_and_glob_queries(tmp_path: Path) -> None:
    (tmp_path / "alpha.py").write_text("", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("", encoding="utf-8")
    retriever = FileRetriever(tmp_path)

    assert retriever.find_by_name("alpha")[0].relative_path == "alpha.py"
    assert retriever.find_by_name("*.txt")[0].relative_path == "beta.txt"


def test_read_range_returns_hash_and_truncation_metadata(tmp_path: Path) -> None:
    (tmp_path / "sample.txt").write_text(
        "line 1\nline 2\nline 3\nline 4\n", encoding="utf-8"
    )

    excerpt = FileRetriever(tmp_path).read_range("sample.txt", 2, 3)

    assert excerpt.content == "line 2\nline 3"
    assert excerpt.start_line == 2
    assert excerpt.end_line == 3
    assert excerpt.total_lines == 4
    assert excerpt.truncated is True
    assert len(excerpt.sha256) == 64


def test_read_range_rejects_secret_binary_and_oversized_ranges(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"hello\x00world")
    retriever = FileRetriever(tmp_path, max_range_lines=2)

    with pytest.raises(PolicyDeniedError, match="secret"):
        retriever.read_range(".env", 1, 1)
    with pytest.raises(PolicyDeniedError, match="binary"):
        retriever.read_range("binary.bin", 1, 1)
    with pytest.raises(ToolError, match="line limit"):
        retriever.read_range("binary.bin", 1, 3)


def test_retrieval_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-context.txt"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(PolicyDeniedError, match="outside"):
        FileRetriever(tmp_path).read_range("../outside-context.txt", 1, 1)


def test_iteration_does_not_follow_directory_symlinks(tmp_path: Path) -> None:
    outside = tmp_path.parent / "external-source"
    outside.mkdir(exist_ok=True)
    (outside / "outside.txt").write_text("outside", encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable")

    assert tuple(FileRetriever(tmp_path).iter_files()) == ()


def test_iteration_enforces_entry_scan_ceiling(tmp_path: Path) -> None:
    for index in range(5):
        (tmp_path / "file-{0}.txt".format(index)).write_text(
            "content", encoding="utf-8"
        )
    retriever = FileRetriever(tmp_path, max_scan_entries=2)

    assert len(tuple(retriever.iter_files())) == 2
    assert retriever.last_scan_truncated is True


def test_binary_detection_is_fail_closed_for_invalid_utf8() -> None:
    assert is_binary_data(b"plain text") is False
    assert is_binary_data(b"text\x00data") is True
    assert is_binary_data(b"\xff\xfe") is True
