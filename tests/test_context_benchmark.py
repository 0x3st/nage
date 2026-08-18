import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_synthetic_context_benchmark_stays_bounded_and_recalls_marker() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "context_benchmark.py")],
        check=True,
        capture_output=True,
        text=True,
    )
    metrics = json.loads(result.stdout)

    assert metrics["estimated_prompt_tokens"] <= metrics["budget_tokens"]
    assert metrics["required_marker_recalled"] is True
    assert metrics["included_segments"] >= 1
