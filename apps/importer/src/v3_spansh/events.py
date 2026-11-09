"""PostgreSQL-authoritative outbox relay and idempotent NATS consumer."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import nats
import psycopg
from nats.js.api import ConsumerConfig, RetentionPolicy, StreamConfig


async def relay_once(dsn: str, nats_url: str, subject: str = "edfinder.v3.canonical.published") -> int:
    client = await nats.connect(nats_url)
    jetstream = client.jetstream()
    try:
        await jetstream.add_stream(StreamConfig(
            name="EDFINDER_V3_CANONICAL",
            subjects=["edfinder.v3.canonical.*"],
            retention=RetentionPolicy.LIMITS,
        ))
    except Exception as exc:
        if "stream name already in use" not in str(exc).casefold():
            try:
                await jetstream.stream_info("EDFINDER_V3_CANONICAL")
            except Exception:
                raise
    published = 0
    try:
        with psycopg.connect(dsn) as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """SELECT outbox_message_id,contract_version,payload
                     FROM v3_async.outbox_message
                    WHERE published_at IS NULL AND available_at <= clock_timestamp()
                    ORDER BY occurred_at FOR UPDATE SKIP LOCKED"""
            )
            for outbox_id, contract_version, payload in cur.fetchall():
                envelope = {
                    "message_id": str(outbox_id), "contract_version": contract_version,
                    "payload": payload if isinstance(payload, dict) else json.loads(payload),
                }
                await jetstream.publish(
                    subject,
                    json.dumps(envelope, sort_keys=True).encode(),
                    headers={"Nats-Msg-Id": str(outbox_id)},
                )
                cur.execute(
                    "UPDATE v3_async.outbox_message SET published_at=clock_timestamp(),publish_attempts=publish_attempts+1,last_error=NULL WHERE outbox_message_id=%s",
                    (outbox_id,),
                )
                published += 1
    finally:
        await client.close()
    return published


def record_effect_once(dsn: str, envelope: dict[str, Any], consumer_name: str = "phase4a-cache-invalidator") -> bool:
    """Return True only for the first durable application of an event effect."""
    effect_id = f"canonical-cache:{envelope['payload']['generation_id']}:{envelope['payload']['publication_sequence']}"
    digest = hashlib.sha256(json.dumps(envelope, sort_keys=True).encode()).digest()
    with psycopg.connect(dsn) as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """INSERT INTO v3_async.effect_receipt(
                   consumer_name,effect_id,outbox_message_id,effect_sha256,completed_at,result_detail)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (consumer_name,effect_id) DO NOTHING""",
            (consumer_name, effect_id, envelope["message_id"], digest,
             datetime.now(timezone.utc), json.dumps({"cache_invalidated": True})),
        )
        return cur.rowcount == 1


async def consume_two_deliveries(dsn: str, nats_url: str, timeout: float = 10.0) -> dict[str, int]:
    """Lab helper proving an explicit redelivery produces one durable effect."""
    client = await nats.connect(nats_url)
    jetstream = client.jetstream()
    subscription = await jetstream.pull_subscribe(
        "edfinder.v3.canonical.published",
        durable="phase4a-cache-invalidator",
        config=ConsumerConfig(ack_wait=1.0, max_deliver=5),
    )
    first = (await subscription.fetch(1, timeout=timeout))[0]
    envelope = json.loads(first.data)
    applied = int(record_effect_once(dsn, envelope))
    await first.nak(delay=0)
    await asyncio.sleep(1.1)
    redelivered = (await subscription.fetch(1, timeout=timeout))[0]
    applied += int(record_effect_once(dsn, json.loads(redelivered.data)))
    await redelivered.ack()
    await subscription.unsubscribe()
    await client.close()
    return {"deliveries_observed": 2, "effects_applied": applied}
