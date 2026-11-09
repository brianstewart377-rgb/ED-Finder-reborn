"""Durable V3 Spansh streaming/COPY/publication pipeline."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID, NAMESPACE_URL, uuid5

import ijson
import psycopg
from psycopg import sql

from .adapter import CanonicalBatch, SpanshAdapter, VOCABULARY, station_market_id
from .coverage import SourceCoverageInventory
from .contracts import (
    COPY_COLUMNS,
    EVENT_CONTRACT_VERSION,
    IMPORTER_VERSION,
    NORMALIZER_VERSION,
    SELECTOR_VERSION,
)

REQUIRED_FEATURES = frozenset({
    "star", "planet", "barycentre", "composition", "rings", "signals",
    "stations", "station_services", "station_economies",
})
MACMILLAN_MARKET_ID = 128130808
_MACMILLAN_MARKET_PATTERN = re.compile(
    rb'(?:"(?:id|marketId|marketID)"\s*:\s*"?128130808"?\b|"Macmillan Depot")'
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _peak_rss_bytes() -> int | None:
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows development host
        return None
    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # Linux reports KiB; macOS reports bytes.
    return peak if sys.platform == "darwin" else peak * 1024


def _cpu_times() -> tuple[float, float] | None:
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows development host
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return float(usage.ru_utime), float(usage.ru_stime)


class _TimedReadStream:
    """Account for gzip read/decompression time separately from ijson work."""

    def __init__(self, stream: Any):
        self.stream = stream
        self.read_seconds = 0.0
        self.bytes_out = 0

    def read(self, size: int = -1) -> bytes:
        started = time.monotonic()
        data = self.stream.read(size)
        self.read_seconds += time.monotonic() - started
        self.bytes_out += len(data)
        return data


def _uuid(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://ed-finder.app/v3/phase-4a/{label}")


@dataclass(frozen=True)
class ArtifactRegistration:
    path: Path
    sha256: str
    size_bytes: int
    retrieved_at: datetime
    effective_at: datetime | None
    media_type: str = "application/octet-stream"
    compression_code: str = "gzip"
    endpoint_identity: str = "https://downloads.spansh.co.uk/galaxy.json.gz"

    @classmethod
    def load(cls, path: Path, metadata_path: Path) -> "ArtifactRegistration":
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        sha256 = metadata.get("sha256") or metadata.get("content_sha256")
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise ValueError("artifact metadata lacks a 64-character sha256")
        actual_size = path.stat().st_size
        declared_size = int(metadata.get("size_bytes", metadata.get("compressed_size_bytes", actual_size)))
        if actual_size != declared_size:
            raise ValueError(f"artifact size mismatch: {actual_size} != {declared_size}")
        retrieved_raw = metadata.get("retrieved_at") or metadata.get("retrieved_at_utc")
        if not retrieved_raw:
            raise ValueError("artifact metadata lacks retrieved_at/retrieved_at_utc")
        retrieved = datetime.fromisoformat(str(retrieved_raw).replace("Z", "+00:00"))
        effective_raw = metadata.get("effective_at") or metadata.get("http_last_modified")
        effective = None
        if effective_raw:
            try:
                effective = datetime.fromisoformat(str(effective_raw).replace("Z", "+00:00"))
            except ValueError:
                try:
                    effective = parsedate_to_datetime(str(effective_raw))
                except (TypeError, ValueError):
                    effective = None
        return cls(
            path=path,
            sha256=sha256.lower(),
            size_bytes=actual_size,
            retrieved_at=retrieved,
            effective_at=effective,
            media_type=str(metadata.get("media_type", "application/octet-stream")),
            compression_code=str(metadata.get("compression", "gzip")),
            endpoint_identity=str(metadata.get("final_url") or metadata.get("source_url") or cls.endpoint_identity),
        )


@dataclass(frozen=True)
class ImportConfig:
    generation_key: str
    target_systems: int = 10_000
    max_systems: int = 50_000
    chunk_systems: int = 500
    require_macmillan: bool = False
    execution_phase: str = "4A"

    def __post_init__(self) -> None:
        if not (1 <= self.target_systems <= self.max_systems <= 5_000_000):
            raise ValueError("selector bounds must satisfy 1 <= target <= max <= 5000000")
        if not (1 <= self.chunk_systems <= self.target_systems):
            raise ValueError("chunk_systems must be between 1 and target_systems")
        if self.require_macmillan and self.target_systems >= self.max_systems:
            raise ValueError("require_macmillan needs at least one selector slot above target_systems")
        if not self.generation_key or len(self.generation_key) > 31:
            raise ValueError("generation_key must fit the frozen baseline contract")
        if self.execution_phase not in {"4A", "4B"}:
            raise ValueError("execution_phase must be 4A or 4B")

    def payload(self) -> dict[str, Any]:
        return {
            "generation_key": self.generation_key,
            "target_systems": self.target_systems,
            "max_systems": self.max_systems,
            "chunk_systems": self.chunk_systems,
            "require_macmillan": self.require_macmillan,
            "selector_version": SELECTOR_VERSION,
            "execution_phase": self.execution_phase,
        }

    @property
    def phase_slug(self) -> str:
        return self.execution_phase.casefold()


@dataclass
class ImportResult:
    generation_id: UUID
    source_run_id: UUID
    job_id: UUID
    relation_schema: str
    systems_selected: int
    chunks_succeeded: int
    rows_written: int
    features: list[str]
    macmillan_found: bool
    source_bytes_consumed: int
    peak_rss_bytes: int | None
    copy_seconds: float
    elapsed_seconds: float
    logical_sha256: str
    chunks_written_this_attempt: int
    chunks_skipped_on_resume: int
    copy_payload_bytes: int
    phase_seconds: dict[str, float]
    rates: dict[str, float | int | None]
    process_metrics: dict[str, float | int | None]
    coverage: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            key: str(value) if isinstance(value, UUID) else value
            for key, value in vars(self).items()
        }


class SpanshV3Pipeline:
    def __init__(self, dsn: str, artifact: ArtifactRegistration, config: ImportConfig):
        self.dsn = dsn
        self.artifact = artifact
        self.config = config
        identity = f"{artifact.sha256}:{_canonical_json(config.payload()).decode()}"
        self.source_run_id = _uuid(f"source-run:{identity}")
        self.artifact_id = _uuid(f"artifact:{artifact.sha256}")
        self.generation_id = _uuid(f"generation:{identity}")
        self.job_id = _uuid(f"job:{identity}")
        self.relation_schema = f"v3_gen_{config.generation_key}"
        self.source_id = 0
        self.rights_policy_id = 0
        self.attempt_number = 0
        self.fencing_token = 0
        self.copy_seconds = 0.0
        self.copy_payload_bytes = 0
        self.copy_rows_this_attempt = 0
        self.chunks_written_this_attempt = 0
        self.chunks_skipped_on_resume = 0
        self.gzip_read_seconds = 0.0
        self.json_parse_seconds = 0.0
        self.normalization_seconds = 0.0
        self.coverage_seconds = 0.0
        self.pre_finalize_validation_seconds = 0.0
        self.finalize_seconds = 0.0
        self.relation_count_seconds = 0.0
        self.rows_written = 0

    def _code_hash(self) -> bytes:
        here = Path(__file__).resolve().parent
        return _sha(b"".join(path.read_bytes() for path in sorted(here.glob("*.py"))))

    def prepare(self, conn: psycopg.Connection[Any]) -> None:
        code_hash = self._code_hash()
        config_hash = _sha(_canonical_json(self.config.payload()))
        normalizer_hash = _sha((Path(__file__).resolve().parent / "adapter.py").read_bytes())
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """INSERT INTO v3_source.source(source_id,source_code,display_name,endpoint_identity,authority_class)
                   VALUES (40,'spansh','Spansh galaxy dump',%s,'SECONDARY_AGGREGATED')
                   ON CONFLICT (source_code) DO UPDATE SET endpoint_identity=EXCLUDED.endpoint_identity
                   RETURNING source_id""",
                (self.artifact.endpoint_identity,),
            )
            self.source_id = int(cur.fetchone()[0])
            cur.execute(
                """INSERT INTO v3_source.source_rights_policy(
                       source_id,policy_version,rights_class,retention_class,
                       distribution_allowed,policy_document_uri,effective_at)
                   VALUES (%s,%s,'CANONICAL_ELIGIBLE','IMMUTABLE_LAB',false,
                           'https://www.spansh.co.uk/',%s)
                   ON CONFLICT (source_id,policy_version) DO UPDATE SET effective_at=EXCLUDED.effective_at
                   RETURNING rights_policy_id""",
                (self.source_id, f"phase-{self.config.phase_slug}-lab-1", self.artifact.retrieved_at),
            )
            self.rights_policy_id = int(cur.fetchone()[0])
            cur.execute(
                """INSERT INTO v3_source.source_artifact(
                       artifact_id,source_id,rights_policy_id,artifact_kind,endpoint_identity,
                       content_sha256,size_bytes,media_type,compression_code,retrieved_at,effective_at,
                       retention_class,storage_locator)
                   VALUES (%s,%s,%s,'GALAXY_SNAPSHOT',%s,%s,%s,%s,%s,%s,%s,'IMMUTABLE_LAB',%s)
                   ON CONFLICT (source_id,content_sha256) DO UPDATE SET storage_locator=EXCLUDED.storage_locator
                   RETURNING artifact_id""",
                (
                    self.artifact_id, self.source_id, self.rights_policy_id,
                    self.artifact.endpoint_identity, bytes.fromhex(self.artifact.sha256),
                    self.artifact.size_bytes, self.artifact.media_type,
                    self.artifact.compression_code, self.artifact.retrieved_at,
                    self.artifact.effective_at, str(self.artifact.path),
                ),
            )
            self.artifact_id = UUID(str(cur.fetchone()[0]))
            cur.execute(
                """INSERT INTO v3_source.source_run(
                       source_run_id,source_id,rights_policy_id,artifact_id,acquisition_kind,
                       trust_zone,run_state,idempotency_key,coverage_domain,scope_contract,
                       is_complete_snapshot,started_at,effective_at,importer_version,
                       importer_code_sha256,importer_config_sha256,normalizer_version,normalizer_sha256)
                   VALUES (%s,%s,%s,%s,'BULK_SNAPSHOT','CANONICAL','RUNNING',%s,'REPRESENTATIVE_SLICE',
                           %s,false,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source_id,idempotency_key) DO UPDATE SET
                       run_state=CASE WHEN v3_source.source_run.run_state='SUCCEEDED' THEN 'SUCCEEDED' ELSE 'RUNNING' END,
                       completed_at=CASE WHEN v3_source.source_run.run_state='SUCCEEDED' THEN v3_source.source_run.completed_at ELSE NULL END
                   RETURNING source_run_id""",
                (
                    self.source_run_id, self.source_id, self.rights_policy_id, self.artifact_id,
                    f"{self.config.phase_slug}:{self.artifact.sha256}:{hashlib.sha256(config_hash).hexdigest()}",
                    json.dumps(self.config.payload()), _utcnow(), self.artifact.effective_at,
                    IMPORTER_VERSION, code_hash, config_hash, NORMALIZER_VERSION, normalizer_hash,
                ),
            )
            self.source_run_id = UUID(str(cur.fetchone()[0]))
            cur.execute(
                """INSERT INTO v3_meta.canonical_generation(
                       generation_id,generation_key,relation_schema,manifest_sha256,build_source_run_id)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (generation_key) DO NOTHING""",
                (self.generation_id, self.config.generation_key, self.relation_schema, config_hash, self.source_run_id),
            )
            cur.execute(
                """INSERT INTO v3_meta.canonical_generation_input(
                       generation_id,input_ordinal,source_id,source_run_id,artifact_id,input_role)
                   VALUES (%s,0,%s,%s,%s,'SPANS_H_REPRESENTATIVE_SLICE')
                   ON CONFLICT (generation_id,source_run_id) DO NOTHING""",
                (self.generation_id, self.source_id, self.source_run_id, self.artifact_id),
            )
            cur.execute("SELECT to_regnamespace(%s)", (self.relation_schema,))
            if cur.fetchone()[0] is None:
                cur.execute("SELECT v3_meta.create_canonical_generation_relations(%s)", (self.generation_id,))
            self._seed_vocab(cur)
            cur.execute(
                """INSERT INTO v3_async.job(job_id,job_type,idempotency_key,job_state,payload,max_attempts)
                   VALUES (%s,'V3_SPANSH_IMPORT',%s,'RUNNING',%s,20)
                   ON CONFLICT (job_type,idempotency_key) DO UPDATE SET
                       job_state=CASE WHEN v3_async.job.job_state='SUCCEEDED' THEN 'SUCCEEDED' ELSE 'RUNNING' END,
                       completed_at=CASE WHEN v3_async.job.job_state='SUCCEEDED' THEN v3_async.job.completed_at ELSE NULL END
                   RETURNING job_id""",
                (self.job_id, str(self.source_run_id), json.dumps(self.config.payload())),
            )
            self.job_id = UUID(str(cur.fetchone()[0]))
            cur.execute(
                """UPDATE v3_async.job_attempt SET attempt_state='ABANDONED',finished_at=clock_timestamp(),
                           error_code='LEASE_REPLACED'
                     WHERE job_id=%s AND attempt_state='RUNNING'""",
                (self.job_id,),
            )
            cur.execute("SELECT COALESCE(max(attempt_number),0)+1 FROM v3_async.job_attempt WHERE job_id=%s", (self.job_id,))
            self.attempt_number = int(cur.fetchone()[0])
            cur.execute("SELECT COALESCE(max(fencing_token),0)+1 FROM v3_async.job_attempt WHERE job_id=%s", (self.job_id,))
            self.fencing_token = int(cur.fetchone()[0])
            now = _utcnow()
            cur.execute(
                """INSERT INTO v3_async.job_attempt(job_id,attempt_number,fencing_token,attempt_state,started_at)
                   VALUES (%s,%s,%s,'RUNNING',%s)""",
                (self.job_id, self.attempt_number, self.fencing_token, now),
            )
            cur.execute(
                """INSERT INTO v3_async.job_lease(job_id,lease_owner,fencing_token,acquired_at,expires_at)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (job_id) DO UPDATE SET lease_owner=EXCLUDED.lease_owner,
                       fencing_token=EXCLUDED.fencing_token,acquired_at=EXCLUDED.acquired_at,
                       expires_at=EXCLUDED.expires_at,renewed_at=NULL""",
                (self.job_id, f"{os.uname().nodename if hasattr(os, 'uname') else 'worker'}:{os.getpid()}",
                 self.fencing_token, now, now + timedelta(minutes=20)),
            )

    def _seed_vocab(self, cur: psycopg.Cursor[Any]) -> None:
        for domain, values in VOCABULARY.items():
            table = {
                "body_type": "body_type", "terraforming_state": "terraforming_state",
                "atmosphere_classification": "atmosphere_classification",
                "volcanism_type": "volcanism_type",
                "ring_type": "ring_type", "reserve_type": "reserve_type",
                "signal_type": "signal_type", "genus": "genus",
                "station_type": "station_type",
                "station_service": "station_service", "economy": "economy",
            }.get(domain)
            if table is None:
                continue
            id_column = f"{table}_id"
            query = sql.SQL(
                "INSERT INTO v3_vocab.{}({},{},display_name) VALUES (%s,%s,%s) "
                "ON CONFLICT ({}) DO UPDATE SET display_name=EXCLUDED.display_name"
            ).format(sql.Identifier(table), sql.Identifier(id_column), sql.Identifier("public_code"), sql.Identifier(id_column))
            for raw, (identifier, display) in values.items():
                public_code = "_".join(raw.replace("$", "").replace(";", "").replace("-", " ").split())[:63]
                cur.execute(query, (identifier, public_code, display))
        for identifier, public_code, entity_kind in (
            (1, "spansh_body_id64", "BODY"),
            (2, "spansh_station_market_id", "STATION"),
            (3, "spansh_ring_id64", "RING"),
        ):
            cur.execute(
                """INSERT INTO v3_vocab.identity_namespace(
                       identity_namespace_id,public_code,entity_kind)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (identity_namespace_id) DO UPDATE SET
                       public_code=EXCLUDED.public_code,entity_kind=EXCLUDED.entity_kind""",
                (identifier, public_code, entity_kind),
            )

    def _renew_lease(self, conn: psycopg.Connection[Any]) -> None:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """UPDATE v3_async.job_lease SET renewed_at=clock_timestamp(),
                           expires_at=clock_timestamp()+interval '20 minutes'
                     WHERE job_id=%s AND fencing_token=%s""",
                (self.job_id, self.fencing_token),
            )
            if cur.rowcount != 1:
                raise RuntimeError("lost V3 importer lease/fence")

    def _chunk_succeeded(self, conn: psycopg.Connection[Any], chunk_key: str) -> bool:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT chunk_state='SUCCEEDED' FROM v3_async.job_chunk WHERE job_id=%s AND chunk_key=%s",
                (self.job_id, chunk_key),
            )
            row = cur.fetchone()
            return bool(row and row[0])

    def _write_chunk(
        self,
        conn: psycopg.Connection[Any],
        chunk_ordinal: int,
        systems: list[dict[str, Any]],
        batch: CanonicalBatch,
        input_offset: int,
    ) -> tuple[int, str]:
        identities = [int(system["id64"]) for system in systems]
        identity_hash = _sha(_canonical_json({"ordinal": chunk_ordinal, "systems": identities}))
        chunk_key = f"systems-{chunk_ordinal:06d}"
        logical_payload = batch.logical_sha256_payload()
        output_hash = _sha(logical_payload)
        if self._chunk_succeeded(conn, chunk_key):
            self.chunks_skipped_on_resume += 1
            return 0, output_hash.hex()
        started = _utcnow()
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """INSERT INTO v3_source.source_checkpoint(
                       source_run_id,chunk_key,chunk_ordinal,deterministic_identity_sha256,
                       checkpoint_state,input_offset,started_at,detail)
                   VALUES (%s,%s,%s,%s,'RUNNING',%s,%s,%s)
                   ON CONFLICT (source_run_id,chunk_key) DO UPDATE SET
                       checkpoint_state='RUNNING',input_offset=EXCLUDED.input_offset,
                       started_at=EXCLUDED.started_at,completed_at=NULL""",
                (
                    self.source_run_id, chunk_key, chunk_ordinal, identity_hash,
                    input_offset, started,
                    json.dumps({
                        "selector_version": SELECTOR_VERSION,
                        "system_id64_first": identities[0], "system_id64_last": identities[-1],
                        "raw_economy_evidence": batch.raw_economy_evidence[:200],
                    }),
                ),
            )
            cur.execute(
                """INSERT INTO v3_async.job_chunk(
                       job_id,chunk_key,chunk_ordinal,deterministic_identity_sha256,chunk_state,
                       attempt_number,fencing_token,started_at)
                   VALUES (%s,%s,%s,%s,'RUNNING',%s,%s,%s)
                   ON CONFLICT (job_id,chunk_key) DO UPDATE SET chunk_state='RUNNING',
                       attempt_number=EXCLUDED.attempt_number,fencing_token=EXCLUDED.fencing_token,
                       started_at=EXCLUDED.started_at,completed_at=NULL""",
                (self.job_id, chunk_key, chunk_ordinal, identity_hash, self.attempt_number, self.fencing_token, started),
            )
            copy_started = time.monotonic()
            for table, rows in batch.rows.items():
                if not rows:
                    continue
                columns = COPY_COLUMNS[table]
                statement = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(
                    sql.Identifier(self.relation_schema), sql.Identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                )
                with cur.copy(statement) as copy:
                    for row in rows:
                        if len(row) != len(columns):
                            raise AssertionError(f"{table} row has {len(row)} values; expected {len(columns)}")
                        copy.write_row(row)
            self.copy_seconds += time.monotonic() - copy_started
            for domain, raw_token in sorted(batch.unmapped):
                cur.execute(
                    """INSERT INTO v3_source.unmapped_vocabulary(
                           source_run_id,vocabulary_domain,raw_token,normalized_candidate,
                           first_observed_at,detail)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (source_run_id,vocabulary_domain,raw_token) DO NOTHING""",
                    (self.source_run_id, domain, raw_token, "_".join(raw_token.casefold().split()),
                     started, json.dumps({"source": "spansh", "chunk_key": chunk_key})),
                )
            completed = _utcnow()
            cur.execute(
                """UPDATE v3_source.source_checkpoint SET checkpoint_state='SUCCEEDED',
                           rows_read=%s,rows_accepted=%s,rows_rejected=0,completed_at=%s
                     WHERE source_run_id=%s AND chunk_key=%s""",
                (len(systems), batch.rows_written, completed, self.source_run_id, chunk_key),
            )
            cur.execute(
                """UPDATE v3_async.job_chunk SET chunk_state='SUCCEEDED',output_sha256=%s,
                           rows_read=%s,rows_written=%s,rows_rejected=0,completed_at=%s
                     WHERE job_id=%s AND chunk_key=%s""",
                (output_hash, len(systems), batch.rows_written, completed, self.job_id, chunk_key),
            )
        self.rows_written += batch.rows_written
        self.copy_rows_this_attempt += batch.rows_written
        self.copy_payload_bytes += len(logical_payload)
        self.chunks_written_this_attempt += 1
        return batch.rows_written, output_hash.hex()

    @staticmethod
    def _is_macmillan(record: dict[str, Any]) -> bool:
        candidates = list(record.get("stations") or [])
        for body in record.get("bodies") or []:
            if isinstance(body, dict):
                candidates.extend(body.get("stations") or [])
        for station in candidates:
            if not isinstance(station, dict):
                continue
            try:
                market_id = station_market_id(station)
            except ValueError:
                continue
            if market_id == MACMILLAN_MARKET_ID and str(station.get("name", "")).casefold() == "macmillan depot":
                return True
        return False

    def _seek_macmillan_record(
        self, conn: psycopg.Connection[Any]
    ) -> tuple[dict[str, Any], int]:
        """Find the pinned real-source case without decoding unrelated systems.

        The immutable Spansh artifact stores one compact system object per
        physical line inside its top-level JSON array.  ``readline`` therefore
        bounds memory to one source system and lets zlib perform the long seek
        without the Python object-allocation cost of adapting every unrelated
        record.  The matched line is still decoded and validated normally.
        """
        with self.artifact.path.open("rb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="rb") as uncompressed:
                for line_number, line in enumerate(uncompressed, start=1):
                    if line_number % 250_000 == 0:
                        self._renew_lease(conn)
                    if not _MACMILLAN_MARKET_PATTERN.search(line):
                        continue
                    payload = line.strip()
                    if payload.endswith(b","):
                        payload = payload[:-1]
                    record = json.loads(payload)
                    if isinstance(record, dict) and self._is_macmillan(record):
                        return record, raw.tell()
        raise RuntimeError("Macmillan Depot MarketID 128130808 absent from streamed artifact")

    def run(
        self, *, crash_before_chunk: int | None = None,
        pause_before_chunk: int | None = None, pause_seconds: int = 300,
    ) -> ImportResult:
        started = time.monotonic()
        cpu_started = _cpu_times()
        selected = 0
        chunk_ordinal = 0
        features: set[str] = set()
        macmillan_found = False
        logical_hashes: list[str] = []
        chunk_records: list[dict[str, Any]] = []
        chunk_batch = CanonicalBatch()
        raw_position = 0
        pin_seek_needed = False
        coverage = SourceCoverageInventory()
        # Autocommit is essential here: every ``conn.transaction()`` below is
        # then a real top-level chunk/control-plane transaction.  Without it,
        # the read in ``_chunk_succeeded`` opens an implicit outer transaction
        # and the chunk scopes become savepoints that all roll back together
        # when a later injected crash escapes the connection context.
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            self.prepare(conn)
            adapter = SpanshAdapter(
                source_id=self.source_id,
                source_run_id=self.source_run_id,
                observed_at=self.artifact.retrieved_at,
            )

            def flush() -> None:
                nonlocal chunk_records, chunk_batch, chunk_ordinal
                if not chunk_records:
                    return
                if crash_before_chunk is not None and chunk_ordinal == crash_before_chunk:
                    raise RuntimeError(
                        f"{self.config.execution_phase}_INJECTED_CRASH_BEFORE_CHUNK_{chunk_ordinal}"
                    )
                if pause_before_chunk is not None and chunk_ordinal == pause_before_chunk:
                    print(
                        f"{self.config.execution_phase}_PAUSED_BEFORE_CHUNK_{chunk_ordinal}",
                        file=sys.stderr, flush=True,
                    )
                    time.sleep(pause_seconds)
                _, output_hash = self._write_chunk(conn, chunk_ordinal, chunk_records, chunk_batch, raw_position)
                logical_hashes.append(output_hash)
                chunk_ordinal += 1
                chunk_records = []
                chunk_batch = CanonicalBatch()

            with self.artifact.path.open("rb") as raw:
                with gzip.GzipFile(fileobj=raw, mode="rb") as uncompressed:
                    timed_stream = _TimedReadStream(uncompressed)
                    records = iter(ijson.items(timed_stream, "item"))
                    scanned = 0
                    while True:
                        parse_started = time.monotonic()
                        read_before = timed_stream.read_seconds
                        try:
                            record = next(records)
                        except StopIteration:
                            break
                        parse_elapsed = time.monotonic() - parse_started
                        read_elapsed = timed_stream.read_seconds - read_before
                        self.json_parse_seconds += max(0.0, parse_elapsed - read_elapsed)
                        scanned += 1
                        if not isinstance(record, dict):
                            continue
                        is_special = self._is_macmillan(record)
                        select = selected < self.config.target_systems
                        regular_ceiling = self.config.max_systems - int(self.config.require_macmillan and not macmillan_found)
                        if not select and selected < regular_ceiling and not REQUIRED_FEATURES.issubset(features):
                            select = True
                        if is_special and not macmillan_found:
                            select = True
                        if select:
                            normalization_started = time.monotonic()
                            adapted = adapter.adapt_system(record)
                            self.normalization_seconds += time.monotonic() - normalization_started
                            coverage_started = time.monotonic()
                            coverage.observe(record, adapted)
                            self.coverage_seconds += time.monotonic() - coverage_started
                            chunk_batch.merge(adapted)
                            chunk_records.append(record)
                            features.update(adapted.feature_flags)
                            selected += 1
                            macmillan_found = macmillan_found or is_special
                            if len(chunk_records) >= self.config.chunk_systems:
                                raw_position = raw.tell()
                                flush()
                        if scanned % 25_000 == 0:
                            self._renew_lease(conn)
                        selector_complete = (
                            selected >= self.config.target_systems
                            and REQUIRED_FEATURES.issubset(features)
                            and (macmillan_found or not self.config.require_macmillan)
                        )
                        if selector_complete:
                            break
                        if (
                            selected >= self.config.target_systems
                            and REQUIRED_FEATURES.issubset(features)
                            and self.config.require_macmillan
                            and not macmillan_found
                        ):
                            # The representative prefix is complete.  Switch to
                            # a bounded raw-record seek for the single pinned
                            # semantic case instead of allocating Python objects
                            # for every intervening system.
                            pin_seek_needed = True
                            break
                        if selected >= self.config.max_systems and not self.config.require_macmillan:
                            break
                    self.gzip_read_seconds += timed_stream.read_seconds
                    raw_position = raw.tell()
                    flush()

            if pin_seek_needed:
                pinned_record, pinned_bytes = self._seek_macmillan_record(conn)
                normalization_started = time.monotonic()
                adapted = adapter.adapt_system(pinned_record)
                self.normalization_seconds += time.monotonic() - normalization_started
                coverage_started = time.monotonic()
                coverage.observe(pinned_record, adapted)
                self.coverage_seconds += time.monotonic() - coverage_started
                chunk_batch.merge(adapted)
                chunk_records.append(pinned_record)
                features.update(adapted.feature_flags)
                selected += 1
                macmillan_found = True
                raw_position += pinned_bytes
                flush()

            if not REQUIRED_FEATURES.issubset(features):
                missing = sorted(REQUIRED_FEATURES - features)
                raise RuntimeError(f"representative selector reached its ceiling without: {missing}")
            if self.config.require_macmillan and not macmillan_found:
                raise RuntimeError("Macmillan Depot MarketID 128130808 absent from streamed artifact")
            result_hash = hashlib.sha256("".join(logical_hashes).encode()).hexdigest()
            validation_started = time.monotonic()
            receipt = self._validate(conn, selected, result_hash, features, macmillan_found)
            self.pre_finalize_validation_seconds += time.monotonic() - validation_started
            finalize_started = time.monotonic()
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("SELECT v3_meta.finalize_canonical_generation(%s,%s)", (self.generation_id, json.dumps(receipt)))
                completed = _utcnow()
                cur.execute(
                    "UPDATE v3_source.source_run SET run_state='SUCCEEDED',completed_at=%s WHERE source_run_id=%s",
                    (completed, self.source_run_id),
                )
                cur.execute(
                    """UPDATE v3_async.job_attempt SET attempt_state='SUCCEEDED',finished_at=%s
                         WHERE job_id=%s AND attempt_number=%s AND fencing_token=%s""",
                    (completed, self.job_id, self.attempt_number, self.fencing_token),
                )
                cur.execute("UPDATE v3_async.job SET job_state='SUCCEEDED',completed_at=%s WHERE job_id=%s", (completed, self.job_id))
                cur.execute("DELETE FROM v3_async.job_lease WHERE job_id=%s AND fencing_token=%s", (self.job_id, self.fencing_token))
            self.finalize_seconds += time.monotonic() - finalize_started
            relation_count_started = time.monotonic()
            self.rows_written = self._relation_row_count(conn)
            self.relation_count_seconds += time.monotonic() - relation_count_started
        elapsed = time.monotonic() - started
        cpu_finished = _cpu_times()
        cpu_user = None
        cpu_system = None
        if cpu_started and cpu_finished:
            cpu_user = cpu_finished[0] - cpu_started[0]
            cpu_system = cpu_finished[1] - cpu_started[1]
        phase_seconds = {
            "gzip_read_decompress": round(self.gzip_read_seconds, 6),
            "json_parse_estimate": round(self.json_parse_seconds, 6),
            "canonical_normalization": round(self.normalization_seconds, 6),
            "coverage_inventory": round(self.coverage_seconds, 6),
            "heap_copy": round(self.copy_seconds, 6),
            "pre_finalize_validation": round(self.pre_finalize_validation_seconds, 6),
            "indexes_fk_validation_analyze": round(self.finalize_seconds, 6),
            "relation_count_receipt": round(self.relation_count_seconds, 6),
            "elapsed_total": round(elapsed, 6),
        }
        rates: dict[str, float | int | None] = {
            "systems_per_second": round(selected / elapsed, 3) if elapsed else None,
            "bodies_per_second": round(
                coverage.counts["bodies"] / elapsed, 3
            ) if elapsed else None,
            "normalized_records_per_second": round(
                self.rows_written / self.normalization_seconds, 3
            ) if self.normalization_seconds else None,
            "copy_records_per_second": round(
                self.copy_rows_this_attempt / self.copy_seconds, 3
            ) if self.copy_seconds else None,
            "copy_mib_per_second": round(
                self.copy_payload_bytes / 1048576 / self.copy_seconds, 3
            ) if self.copy_seconds else None,
            "gzip_compressed_mib_per_second": round(
                raw_position / 1048576 / self.gzip_read_seconds, 3
            ) if self.gzip_read_seconds else None,
            "json_records_per_second": round(
                selected / self.json_parse_seconds, 3
            ) if self.json_parse_seconds else None,
            "normalization_systems_per_second": round(
                selected / self.normalization_seconds, 3
            ) if self.normalization_seconds else None,
        }
        return ImportResult(
            generation_id=self.generation_id, source_run_id=self.source_run_id, job_id=self.job_id,
            relation_schema=self.relation_schema, systems_selected=selected,
            chunks_succeeded=chunk_ordinal, rows_written=self.rows_written,
            features=sorted(features), macmillan_found=macmillan_found,
            source_bytes_consumed=raw_position, peak_rss_bytes=_peak_rss_bytes(),
            copy_seconds=round(self.copy_seconds, 6),
            elapsed_seconds=round(elapsed, 6), logical_sha256=result_hash,
            chunks_written_this_attempt=self.chunks_written_this_attempt,
            chunks_skipped_on_resume=self.chunks_skipped_on_resume,
            copy_payload_bytes=self.copy_payload_bytes,
            phase_seconds=phase_seconds, rates=rates,
            process_metrics={
                "cpu_user_seconds": round(cpu_user, 6) if cpu_user is not None else None,
                "cpu_system_seconds": round(cpu_system, 6) if cpu_system is not None else None,
                "parser_workers": 1,
                "copy_writers": 1,
                "max_queue_depth": 1,
                "backpressure_model": "SYNCHRONOUS_CHUNK_HANDOFF",
            },
            coverage=coverage.as_dict(),
        )

    def _relation_row_count(self, conn: psycopg.Connection[Any]) -> int:
        total = 0
        with conn.cursor() as cur:
            for table in COPY_COLUMNS:
                cur.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier(self.relation_schema), sql.Identifier(table)
                ))
                total += int(cur.fetchone()[0])
        return total

    def _validate(
        self, conn: psycopg.Connection[Any], selected: int, logical_hash: str,
        features: set[str], macmillan_found: bool,
    ) -> dict[str, Any]:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT count(*),sum(loaded_body_count) FROM {}.systems").format(sql.Identifier(self.relation_schema)))
            system_count, loaded_bodies = cur.fetchone()
            cur.execute(sql.SQL("SELECT count(*) FROM {}.bodies").format(sql.Identifier(self.relation_schema)))
            body_count = int(cur.fetchone()[0])
            cur.execute(sql.SQL(
                "SELECT count(*) FROM {}.bodies b LEFT JOIN {}.systems s ON s.id64=b.system_id64 WHERE s.id64 IS NULL"
            ).format(sql.Identifier(self.relation_schema), sql.Identifier(self.relation_schema)))
            orphan_bodies = int(cur.fetchone()[0])
            cur.execute(sql.SQL(
                "SELECT count(*) FROM {}.station_economy_current e JOIN {}.stations s USING(station_pk) "
                "WHERE s.market_id=%s AND s.name='Macmillan Depot' AND e.economy_weight IS NULL AND e.is_primary"
            ).format(sql.Identifier(self.relation_schema), sql.Identifier(self.relation_schema)), (MACMILLAN_MARKET_ID,))
            macmillan_rows = int(cur.fetchone()[0])
        checks = {
            "selected_system_count_matches": int(system_count) == selected,
            "system_loaded_body_sum_matches": int(loaded_bodies or 0) == body_count,
            "orphan_bodies_zero": orphan_bodies == 0,
            "required_features_present": REQUIRED_FEATURES.issubset(features),
            "macmillan_nullable_primary_tourism": (
                not self.config.require_macmillan
                or (macmillan_found and macmillan_rows == 1)
            ),
        }
        if not all(checks.values()):
            raise RuntimeError(f"candidate validation failed: {checks}")
        return {
            "phase": self.config.execution_phase, "artifact_sha256": self.artifact.sha256,
            "selector": self.config.payload(), "logical_sha256": logical_hash,
            "checks": checks, "systems": int(system_count), "bodies": body_count,
            "features": sorted(features), "validated_at": _utcnow().isoformat(),
        }

    def publish(self, reason: str, actor: str = "phase-4a-integration-lab") -> int:
        """Atomically move the metadata pointer and enqueue its durable event."""
        outbox_id = _uuid(f"outbox:{self.generation_id}:{reason}")
        with psycopg.connect(self.dsn) as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT v3_meta.publish_canonical_generation(%s,%s,%s)", (self.generation_id, reason, actor))
            sequence = int(cur.fetchone()[0])
            payload = {
                "generation_id": str(self.generation_id),
                "generation_key": self.config.generation_key,
                "publication_sequence": sequence,
                "artifact_sha256": self.artifact.sha256,
            }
            cur.execute(
                """INSERT INTO v3_async.outbox_message(
                       outbox_message_id,producer_scope,idempotency_key,aggregate_type,
                       aggregate_id,event_type,contract_version,payload,occurred_at)
                   VALUES (%s,'v3-canonical-publication',%s,'canonical_generation',%s,
                           'canonical_generation.published',%s,%s,%s)
                   ON CONFLICT (producer_scope,idempotency_key) DO NOTHING""",
                (outbox_id, f"publication:{sequence}", str(self.generation_id),
                 EVENT_CONTRACT_VERSION, json.dumps(payload), _utcnow()),
            )
        return sequence
