"""Command-line entry point for the V3 Spansh vertical slice."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .pipeline import ArtifactRegistration, ImportConfig, SpanshV3Pipeline


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), required=os.environ.get("DATABASE_URL") is None)
    result.add_argument("--artifact", type=Path, required=True)
    result.add_argument("--artifact-metadata", type=Path, required=True)
    result.add_argument("--generation-key", required=True)
    result.add_argument("--target-systems", type=int, default=10_000)
    result.add_argument("--max-systems", type=int, default=50_000)
    result.add_argument("--chunk-systems", type=int, default=500)
    result.add_argument("--crash-before-chunk", type=int)
    result.add_argument("--pause-before-chunk", type=int)
    result.add_argument("--pause-seconds", type=int, default=300)
    result.add_argument("--execution-phase", choices=("4A", "4B"), default="4A")
    result.add_argument(
        "--require-macmillan",
        action="store_true",
        help="Optional pinned-source seek; not required for representative slices because snapshots may omit the station.",
    )
    result.add_argument("--publish", action="store_true")
    result.add_argument("--publish-only", action="store_true")
    result.add_argument("--publication-reason", default="Phase 4A validated vertical slice")
    result.add_argument("--publication-actor", default="phase-4a-integration-lab")
    return result


def main() -> int:
    args = parser().parse_args()
    artifact = ArtifactRegistration.load(args.artifact, args.artifact_metadata)
    config = ImportConfig(
        generation_key=args.generation_key, target_systems=args.target_systems,
        max_systems=args.max_systems, chunk_systems=args.chunk_systems,
        require_macmillan=args.require_macmillan,
        execution_phase=args.execution_phase,
    )
    pipeline = SpanshV3Pipeline(args.dsn, artifact, config)
    if args.publish_only:
        print(json.dumps({
            "generation_id": str(pipeline.generation_id),
            "publication_sequence": pipeline.publish(args.publication_reason, args.publication_actor),
        }, sort_keys=True, indent=2))
        return 0
    outcome = pipeline.run(
        crash_before_chunk=args.crash_before_chunk,
        pause_before_chunk=args.pause_before_chunk,
        pause_seconds=args.pause_seconds,
    )
    result = outcome.as_dict()
    if args.publish:
        publication_started = time.monotonic()
        result["publication_sequence"] = pipeline.publish(
            args.publication_reason, args.publication_actor
        )
        result["phase_seconds"]["publication"] = round(
            time.monotonic() - publication_started, 6
        )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
