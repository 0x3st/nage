import sys
from pathlib import Path

import pytest

from nage.errors import ToolError
from nage.tools.subprocesses import run_bounded_process


def test_bounded_process_never_invokes_a_shell(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    hostile_argument = "; touch {0}".format(marker)

    result = run_bounded_process(
        [
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1])",
            hostile_argument,
        ],
        cwd=tmp_path,
        timeout_seconds=5,
        maximum_output_bytes=4096,
    )

    assert result.returncode == 0
    assert hostile_argument in result.stdout.decode()
    assert not marker.exists()


def test_bounded_process_stops_excessive_output(tmp_path: Path) -> None:
    result = run_bounded_process(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 1000000)"],
        cwd=tmp_path,
        timeout_seconds=5,
        maximum_output_bytes=1024,
    )

    assert len(result.stdout) + len(result.stderr) <= 1024
    assert result.output_truncated is True


def test_bounded_process_enforces_timeout(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="timed out"):
        run_bounded_process(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            cwd=tmp_path,
            timeout_seconds=0.05,
            maximum_output_bytes=1024,
        )
