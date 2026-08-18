#!/usr/bin/env python3
"""Run a synthetic, private-data-free context budget benchmark."""

import json
import tempfile
from pathlib import Path

from nage.core.context import (
    ContextBuilder,
    ContextSegment,
    ContextSource,
    RequestEnvelope,
    TrustLevel,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        envelope = RequestEnvelope.create(
            "explain the build failure",
            root,
            request_id="benchmark-request",
        )
        candidates = [
            ContextSegment(
                source_type=ContextSource.FILE,
                source_reference="build.log",
                trust_level=TrustLevel.UNTRUSTED_DATA,
                priority=90,
                content=("ERROR synthetic failure\n" + "diagnostic line\n" * 1000),
                reason_selected="Synthetic benchmark fixture.",
            ),
            ContextSegment(
                source_type=ContextSource.FILE,
                source_reference="irrelevant.log",
                trust_level=TrustLevel.UNTRUSTED_DATA,
                priority=10,
                content="irrelevant line\n" * 1000,
                reason_selected="Synthetic low-priority benchmark fixture.",
            ),
        ]
        built = ContextBuilder().build(envelope, candidates)
        metrics = {
            "budget_tokens": built.manifest.budget_tokens,
            "estimated_prompt_tokens": built.manifest.estimated_prompt_tokens,
            "included_segments": sum(
                1 for entry in built.manifest.entries if entry.included
            ),
            "truncated_segments": sum(
                1 for entry in built.manifest.entries if entry.truncated
            ),
            "required_marker_recalled": any(
                "ERROR synthetic failure" in segment.content
                for segment in built.selected_segments
            ),
        }
        print(json.dumps(metrics, sort_keys=True))


if __name__ == "__main__":
    main()
