import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence

from ..errors import ToolError


@dataclass(frozen=True)
class BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    output_truncated: bool


def run_bounded_process(
    arguments: Sequence[str],
    cwd: Path,
    timeout_seconds: float,
    maximum_output_bytes: int,
    environment: Optional[Mapping[str, str]] = None,
) -> BoundedProcessResult:
    argv = tuple(arguments)
    if not argv or any(
        not isinstance(value, str) or not value or "\x00" in value for value in argv
    ):
        raise ToolError("Subprocess argument vector is invalid.")
    if timeout_seconds <= 0 or maximum_output_bytes <= 0:
        raise ValueError("Subprocess limits must be positive.")

    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=None if environment is None else dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
    except (OSError, ValueError) as exc:
        raise ToolError("Unable to start bounded subprocess.") from exc

    stdout = bytearray()
    stderr = bytearray()
    total_saved = [0]
    output_truncated = threading.Event()
    lock = threading.Lock()

    def read_stream(stream, destination: bytearray) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                with lock:
                    remaining = maximum_output_bytes - total_saved[0]
                    if remaining > 0:
                        kept = chunk[:remaining]
                        destination.extend(kept)
                        total_saved[0] += len(kept)
                    if len(chunk) > max(remaining, 0):
                        output_truncated.set()
        finally:
            stream.close()

    if process.stdout is None or process.stderr is None:
        process.kill()
        raise ToolError("Subprocess output pipes are unavailable.")
    readers = (
        threading.Thread(
            target=read_stream, args=(process.stdout, stdout), daemon=True
        ),
        threading.Thread(
            target=read_stream, args=(process.stderr, stderr), daemon=True
        ),
    )
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while process.poll() is None:
        if output_truncated.is_set():
            process.kill()
            break
        if time.monotonic() >= deadline:
            timed_out = True
            process.kill()
            break
        time.sleep(0.01)
    try:
        returncode = process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        returncode = process.wait()
    for reader in readers:
        reader.join(timeout=1.0)
    if timed_out:
        raise ToolError("Bounded subprocess timed out.")
    return BoundedProcessResult(
        returncode=returncode,
        stdout=bytes(stdout),
        stderr=bytes(stderr),
        output_truncated=output_truncated.is_set(),
    )
