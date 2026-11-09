"""Exercise Phase 4A JetStream relay and duplicate-delivery deduplication."""

from __future__ import annotations

import argparse
import asyncio
import json

from .events import consume_two_deliveries, relay_once


async def run(dsn: str, nats_url: str) -> dict[str, int]:
    relayed = await relay_once(dsn, nats_url)
    dedup = await consume_two_deliveries(dsn, nats_url)
    return {"outbox_messages_relayed": relayed, **dedup}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--nats-url", required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.dsn, args.nats_url)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
