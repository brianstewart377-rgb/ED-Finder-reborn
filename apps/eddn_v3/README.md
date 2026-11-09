# V3 EDDN station-sighting worker (`apps/eddn_v3`)

**Status: code only. Nothing here is deployed, scheduled or wired into any
compose file, release workflow or production deploy authority.**

## What it does

A small long-running worker that listens to the public EDDN relay
(`tcp://eddn.edcd.io:9500`, zlib-compressed JSON) and keeps a change history of
station state:

1. Accepts only `$schemaRef == https://eddn.edcd.io/schemas/journal/1`
   (this rejects `/test`) with `event == "Docked"`.
2. Drops fleet carriers (`StationType` FleetCarrier / Drake-Class Carrier) and
   Legacy-galaxy game versions.
3. Validates and bounds everything: decompressed size, positive `MarketID` and
   `SystemAddress`, at most 8 economies with proportions in 0..1, at most 128
   services, UTF-8-encodable text at most 128 characters, landing pads 0..255,
   and event/gateway timestamps inside a window around receipt time (never
   more than an hour ahead; also enforced by CHECK constraints).
4. Rejects frames missing any hashed state field (`StationType`,
   `StationEconomies`, `StationServices`, `LandingPads`): unknown is not empty.
   Hashes the research state (system, station name, type, economies, services,
   landing pads; the system is included because megaships move) and writes
   a row to `v3_observe.station_sighting` **only when that hash differs
   from the current state for the same `(source, market_id)`**.
   The same state seen twice gives one row. Rows are kept indefinitely.
   A record older than the latest known source time for that station is
   counted as stale and skipped. Unchanged sightings still advance that time
   (an in-process, bounded high-water mark), so a delayed frame cannot
   overwrite a state that was re-observed later. After a restart the mark
   starts empty and protection rests on the stored rows and the event-age
   window.
5. Writes in bounded batches (default every 15 s or 500 rows), one transaction
   per batch, with parameterised SQL, as the restricted `v3_station_observer`
   role (`SET ROLE` on the existing application credential).

It writes `v3_observe` only. It never touches canonical (`v3_gen_*`), derived
(`v3_derived`, `v3_spatial`), application (`v3_app`) or V2 relations. It is
**not** the V2 listener (`apps/eddn`), **not** the API-lifespan simulation
ingest (`apps/api/src/ingest/eddn_client.py`), and **not** journal-import
automation or journal-import canonical promotion.

EDDN carries no colonisation events. Colonisation shows up only indirectly, as
station types (construction depots) and as economy/service changes over time.

## Privacy

- The EDDN header is reduced to an allowlist the moment a frame is decoded:
  `softwareName`, `softwareVersion`, `gatewayTimestamp`, `gameversion`,
  `gamebuild`. **`uploaderID` is never read, stored or logged.** There is no
  column that could hold it, and the tests enforce that the field is never
  accessed.
- Only allowlisted message fields are stored. The raw message is never kept.
- Logs carry counts, exception type names and SQLSTATEs only, never message
  content.
- The data is public-source evidence recorded as `INTERNAL_USE` (not
  redistributed) in the `v3_source` rights ledger. Journal uploads from users
  are personal data and must never be written here.

## Shared writer (for the Spansh loader)

`edfinder_observe.sightings` is source-agnostic: `build_sighting(...)`
validates one station, `record_sightings(conn, batch)` / `record_sighting(conn,
row)` apply the change-only rule, and `configure_observer_session(conn)` drops
to the restricted role. The Spansh one-shot loader is meant to reuse them with
`source='spansh'` (it may also fill `body_id` / `body_name`, which EDDN `Docked`
does not carry).

## Configuration (environment)

| Variable | Default | Bounds |
|---|---|---|
| `EDDN_V3_DATABASE_URL` | required | `postgresql://…`; never printed |
| `EDDN_V3_RELAY_URL` | `tcp://eddn.edcd.io:9500` | `tcp://host:port` |
| `EDDN_V3_FLUSH_INTERVAL_SECONDS` | 15 | 1–300 |
| `EDDN_V3_FLUSH_MAX_ROWS` | 500 | 1–5000 |
| `EDDN_V3_BUFFER_MAX_ROWS` | 5000 | 100–50000, ≥ flush rows; oldest dropped and counted when full |
| `EDDN_V3_MAX_CONSECUTIVE_FLUSH_FAILURES` | 5 | 1–1000; then exit 3 |
| `EDDN_V3_MAX_MESSAGE_BYTES` | 262144 | 1024–1048576; also ZeroMQ `MAXMSGSIZE` (an oversized frame drops the connection; the idle reconnect restores it) |
| `EDDN_V3_RECV_IDLE_RECONNECT_SECONDS` | 120 | 10–3600 |
| `EDDN_V3_RECONNECT_BACKOFF_MAX_SECONDS` | 60 | 1–600 (exponential from 1 s) |
| `EDDN_V3_MAX_EVENT_AGE_SECONDS` | 86400 | 60–604800 |
| `EDDN_V3_MAX_CLOCK_SKEW_SECONDS` | 300 | 0–3600 |
| `EDDN_V3_METRICS_HOST` / `_PORT` | `127.0.0.1` / 9465 | IPv4 literal / 1024–65535 |
| `EDDN_V3_WATERMARK_MAX_STATIONS` | 200000 | 1000–2000000 (LRU high-water marks) |
| `EDDN_V3_HEALTH_STALE_SECONDS` | 300 | 30–86400 |
| `EDDN_V3_LOG_LEVEL` | `INFO` | DEBUG, INFO, WARNING, ERROR |

## Liveness and metrics

`GET /metrics` (Prometheus text) and `GET /healthz` on the metrics port. The
container HEALTHCHECK runs `python -m edfinder_observe.healthcheck`, which
probes the configured bind address (`0.0.0.0` is probed on loopback).
Following the #864 pattern, the last-successful-flush timestamp moves only on a
successful flush; failures increment `flush_failures_total` and
`consecutive_flush_failures`; `/healthz` returns 503 while a failure is
outstanding or the relay has been silent past the stale window. After N
consecutive failed flushes the process exits with code 3 so the supervisor
restarts it. A failed batch is put back at the front of the buffer and retried
on the next flush (also while the relay is quiet), so a broken write path
reaches the breaker; if the requeue overflows the buffer the oldest rows are
dropped and counted. Also exported: last message received, last flush attempt,
messages filtered/rejected by reason, buffer drops, reconnects, rows
inserted/unchanged/stale.

Exit codes: 0 after SIGTERM/SIGINT with a successful final flush, 2 bad
configuration, 3 flush breaker, 4 the final flush on shutdown failed (buffered
sightings lost).

## Known limitations

- **The "seen" high-water mark is in memory only.** Unchanged sightings write
  no row, so the mark that stops a delayed older frame overwriting a newer,
  re-observed state lives in the worker process and starts empty after a
  restart. A wrong current state therefore needs all of: a restart, then a
  late frame with a *different* state that is older than an unrecorded
  re-observation but still inside the event-age window
  (`EDDN_V3_MAX_EVENT_AGE_SECONDS`, default one day). It is accepted and
  bounded for the first version, and it self-heals at the station's next
  docking, which records the true state again. A durable per-station
  high-water mark will be designed with the post-go-live wiring; it trades
  against the change-only, no-update-churn storage rule, so it needs its own
  decision.
- **`build_sighting` does not exclude fleet carriers itself.** The EDDN
  normaliser drops them before building a sighting; the future Spansh loader
  must do the same (for example with `eddn_docked.is_fleet_carrier`) before
  calling the shared writer. To be enforced when that loader is written.
- **The container health probe honours proxy variables.** If the container is
  given `HTTP_PROXY`/`http_proxy`, the deploy wiring must also set `NO_PROXY`
  to cover the metrics address (or not pass a proxy at all); the worker itself
  needs no HTTP proxy, since EDDN is ZeroMQ over TCP.

## How it will be deployed later (each step needs the owner's go)

None of these is done by this code:

1. Register `sql/v3/proposals/017_v3_observe_station_sightings.sql` as V3
   migration 017 (manifest, schema transition, schema identity) after the
   Finder go-live, then plan and apply it through the governed migration
   workflow. Before registering, make the `v3_source` id seed and
   identity-sequence re-seed safe against concurrent writers (lock both
   sequences, or allocate the ids from the repaired sequences), because the
   live journal-import path inserts into the same source ledger.
2. Add the worker to the V3 production deploy authority (compose services,
   deployer, target authority, release runbook). The stack decision prefers one
   backend image with several entrypoints, so that step may run this package
   from the backend image instead of building this Dockerfile.
3. Make the worker refuse to start while the governed derived purge is open,
   using the shared read-only purge-state query, and keep it stopped during a
   purge.
4. First run, then a read-only review of rows per day, size, drops and
   failures.

## Development

```bash
# from the repository root, with the CPython 3.14 test venv
python -m pytest tests/test_eddn_v3_worker.py tests/test_eddn_v3_station_sightings_pg18.py -q
```

The PostgreSQL tests apply `sql/v3/migrations/001_v3_baseline.sql` and the
017 proposal into a fresh random database on the local disposable server named
by `RATINGS_V4_VALIDATION_DATABASE_URL`, and drop it afterwards.
