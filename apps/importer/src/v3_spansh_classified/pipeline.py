"""Bounded disposable-DB rehearsal for the versioned Spansh adapter.

This is deliberately not a production or galaxy-generation operator command.
Reuse the adapter in a separately governed candidate builder for a full import.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import gzip
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID

import ijson
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from v3_spansh.adapter import CanonicalBatch, VOCABULARY
from v3_spansh.contracts import COPY_COLUMNS
from v3_spansh.pipeline import ArtifactRegistration, ImportConfig, SpanshV3Pipeline

from . import VERSION
from .adapter import ClassifiedSpanshAdapter
from .classification import BODY_TYPE_VOCABULARY

MAX_SLICE_BYTES = 64 * 1024 * 1024
MAX_SLICE_SYSTEMS = 10_000


def code_hash() -> bytes:
    here = Path(__file__).resolve().parent
    paths = sorted((here.parent / "v3_spansh").glob("*.py")) + sorted(here.glob("*.py"))
    return hashlib.sha256(b"".join(path.read_bytes() for path in paths)).digest()


@dataclass(frozen=True)
class ClassifiedImportConfig(ImportConfig):
    def __post_init__(self) -> None:
        super().__post_init__()
        if self.max_systems > MAX_SLICE_SYSTEMS or self.require_macmillan:
            raise ValueError("classification rehearsal requires a bounded slice of at most 10000 systems")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", self.generation_key):
            raise ValueError("invalid local generation key")

    def payload(self) -> dict[str, Any]:
        return {**super().payload(), "classification_version": VERSION,
                "classification_code_sha256": code_hash().hex(),
                "selector_version": "complete-local-slice-1"}


def slice_records(artifact: ArtifactRegistration, max_systems: int):
    """Verify the complete small compressed artifact before any database write."""
    if not 1 <= max_systems <= MAX_SLICE_SYSTEMS:
        raise ValueError("invalid slice system limit")
    if artifact.compression_code != "gzip":
        raise ValueError("slice must use gzip compression")
    with artifact.path.open("rb") as raw:
        size = raw.seek(0, 2)
        raw.seek(0)
        if size != artifact.size_bytes or not 0 < size <= MAX_SLICE_BYTES:
            raise ValueError("slice size mismatch or exceeds 64 MiB rehearsal limit")
        if hashlib.file_digest(raw, "sha256").hexdigest() != artifact.sha256:
            raise ValueError("slice SHA256 mismatch")
        raw.seek(0)
        with gzip.GzipFile(fileobj=raw, mode="rb") as decoded:
            first = decoded.read(1)
            while first and first in b" \r\n\t":
                first = decoded.read(1)
            if first != b"[":
                raise ValueError("Spansh slice must be a JSON array")
            decoded.seek(0)
            count = 0
            for count, record in enumerate(ijson.items(decoded, "item", use_float=True), 1):
                if count > max_systems:
                    raise ValueError("slice exceeds max-systems; extract a smaller complete slice")
                if not isinstance(record, dict) or not isinstance(record.get("bodies", []), list):
                    raise ValueError("invalid Spansh system record")
                if len(record.get("bodies", [])) > 10000:
                    raise ValueError("system exceeds rehearsal body limit")
                yield record
            if not count:
                raise ValueError("empty Spansh slice")


def dry_run(artifact: ArtifactRegistration, *, max_systems: int = 1000) -> dict[str, Any]:
    adapter = ClassifiedSpanshAdapter(source_id=40, source_run_id=UUID(int=0),
                                     observed_at=artifact.retrieved_at)
    spectral: Counter = Counter()
    body_types: Counter = Counter()
    unmapped: Counter = Counter()
    systems = bodies = main_stars = missing_main = 0
    ids: set[int] = set()
    columns = COPY_COLUMNS["bodies"]
    for record in slice_records(artifact, max_systems):
        identifier = int(record["id64"])
        if identifier in ids:
            raise ValueError("duplicate Spansh system identity")
        ids.add(identifier)
        batch = adapter.adapt_system(record)
        systems += 1
        selected = 0
        for values in batch.rows["bodies"]:
            row = dict(zip(columns, values, strict=True))
            bodies += 1
            body_types[str(row["body_type_id"])] += 1
            if row["spectral_class"]:
                spectral[row["spectral_class"]] += 1
            selected += int(row["is_main_star"])
        main_stars += selected
        missing_main += int(selected == 0)
        unmapped.update(batch.unmapped)
    return {"version": VERSION, "dry_run": True, "published": False,
            "source": "Spansh", "source_url": artifact.endpoint_identity,
            "artifact_sha256": artifact.sha256, "systems": systems, "bodies": bodies,
            "main_stars": main_stars, "systems_without_main_star": missing_main,
            "spectral_classes": dict(sorted(spectral.items())),
            "body_type_counts": {code: body_types[str(entry[0])]
                                 for code, entry in BODY_TYPE_VOCABULARY.items()},
            "unclassified_bodies": body_types["None"],
            "unmapped": [{"domain": domain, "token": token, "systems": count}
                         for (domain, token), count in sorted(unmapped.items())]}


class ClassifiedSpanshPipeline(SpanshV3Pipeline):
    """Fresh local candidate only; no resume, publication, or existing-row UPDATE."""

    def __init__(self, dsn: str, artifact: ArtifactRegistration, config: ClassifiedImportConfig):
        settings = conninfo_to_dict(dsn)
        if (settings.get("host") not in {"127.0.0.1", "localhost", "::1"}
                or settings.get("port") in {None, "5432"}
                or not settings.get("dbname", "").startswith("spansh_classified_test")
                or settings.get("service") or settings.get("hostaddr")):
            raise ValueError("rehearsal needs loopback, a non-5432 port, and a spansh_classified_test database")
        if not isinstance(config, ClassifiedImportConfig):
            raise ValueError("versioned classification configuration is required")
        # Explicit hostaddr prevents PGHOSTADDR/PGSERVICE in the caller's
        # environment from rerouting an apparently local DSN to another host.
        local_dsn = make_conninfo(dsn, hostaddr="::1" if settings["host"] == "::1" else "127.0.0.1")
        super().__init__(local_dsn, artifact, config)
        self.body_type_ids: dict[str, int] = {}

    def _code_hash(self) -> bytes:
        return code_hash()

    def _seed_vocab(self, cur: psycopg.Cursor[Any]) -> None:
        # Resolve body types by public_code, never relabel an existing ID. The
        # lock also serializes ID allocation when a partial vocabulary exists.
        cur.execute("LOCK TABLE v3_vocab.body_type IN SHARE ROW EXCLUSIVE MODE")
        cur.execute("SELECT public_code,body_type_id FROM v3_vocab.body_type")
        body_ids = dict(cur.fetchall())
        used = set(body_ids.values())
        for code, (preferred, display) in BODY_TYPE_VOCABULARY.items():
            if code in body_ids:
                continue
            identifier = preferred if preferred not in used else max(used, default=0) + 1
            cur.execute("INSERT INTO v3_vocab.body_type(body_type_id,public_code,display_name) VALUES (%s,%s,%s)",
                        (identifier, code, display))
            used.add(identifier)
        # Reuse the frozen vocabulary data and spelling, but not its fixed
        # body_type ID assumptions. These are vocabulary inserts, not updates
        # of canonical systems/bodies/ratings; replica mode is not applicable.
        for domain, values in VOCABULARY.items():
            if domain == "body_type":
                continue
            query = sql.SQL("INSERT INTO v3_vocab.{} ({},public_code,display_name) VALUES (%s,%s,%s) "
                            "ON CONFLICT ({}) DO NOTHING").format(
                sql.Identifier(domain), sql.Identifier(f"{domain}_id"), sql.Identifier(f"{domain}_id"))
            for raw, (identifier, display) in values.items():
                public_code = "_".join(raw.replace("$", "").replace(";", "").replace("-", " ").split())[:63]
                cur.execute(query, (identifier, public_code, display))
        for identifier, code, kind in ((1, "spansh_body_id64", "BODY"),
                                       (2, "spansh_station_market_id", "STATION"),
                                       (3, "spansh_ring_id64", "RING")):
            cur.execute("INSERT INTO v3_vocab.identity_namespace(identity_namespace_id,public_code,entity_kind) "
                        "VALUES (%s,%s,%s) ON CONFLICT (identity_namespace_id) DO NOTHING",
                        (identifier, code, kind))
        cur.execute("SELECT public_code,body_type_id FROM v3_vocab.body_type WHERE public_code=ANY(%s)",
                    (list(BODY_TYPE_VOCABULARY),))
        self.body_type_ids = dict(cur.fetchall())

    def prepare(self, conn: psycopg.Connection[Any]) -> None:
        # The caller wraps registration in one transaction so a rejected seed
        # or metadata write cannot leave a partially registered candidate.
        if conn.execute("SELECT 1 FROM v3_meta.canonical_generation WHERE generation_key=%s",
                        (self.config.generation_key,)).fetchone():
            raise ValueError("rehearsal requires a fresh generation key; resume is not supported")
        super().prepare(conn)
        conn.execute("""UPDATE v3_source.source_run
                        SET importer_version=%s,normalizer_version=%s,normalizer_sha256=%s
                        WHERE source_run_id=%s""",
                     (VERSION, VERSION, code_hash(), self.source_run_id))

    def run(self) -> dict[str, Any]:
        summary = dry_run(self.artifact, max_systems=self.config.max_systems)
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            with conn.transaction():
                self.prepare(conn)
            adapter = ClassifiedSpanshAdapter(source_id=self.source_id, source_run_id=self.source_run_id,
                                             observed_at=self.artifact.retrieved_at,
                                             body_type_ids=self.body_type_ids)
            batch = CanonicalBatch()
            records = []
            hashes = []
            for record in slice_records(self.artifact, self.config.max_systems):
                batch.merge(adapter.adapt_system(record))
                records.append(record)
                if len(records) == self.config.chunk_systems:
                    hashes.append(self._copy(conn, records, batch, len(hashes)))
                    records, batch = [], CanonicalBatch()
            if records:
                hashes.append(self._copy(conn, records, batch, len(hashes)))
            receipt = {**summary, "dry_run": False, "logical_sha256": hashlib.sha256(
                "".join(hashes).encode()).hexdigest(), "generation_id": str(self.generation_id),
                "relation_schema": self.relation_schema, "source_run_id": str(self.source_run_id),
                "code_sha256": code_hash().hex()}
            with conn.transaction():
                # Baseline finalization creates/validates indexes and all FKs.
                # It makes this local candidate READY without publishing it.
                conn.execute("SELECT v3_meta.finalize_canonical_generation(%s,%s)",
                             (self.generation_id, json.dumps(receipt)))
                conn.execute("UPDATE v3_source.source_run SET run_state='SUCCEEDED',completed_at=now() "
                             "WHERE source_run_id=%s", (self.source_run_id,))
                conn.execute("UPDATE v3_async.job SET job_state='SUCCEEDED',completed_at=now() WHERE job_id=%s",
                             (self.job_id,))
                conn.execute("UPDATE v3_async.job_attempt SET attempt_state='SUCCEEDED',finished_at=now() "
                             "WHERE job_id=%s AND attempt_number=%s", (self.job_id, self.attempt_number))
                conn.execute("DELETE FROM v3_async.job_lease WHERE job_id=%s", (self.job_id,))
        return receipt

    def _copy(self, conn, records, batch, ordinal):
        # Bulk-write safety exception: COPY inserts NEW canonical rows with
        # primary/foreign-key identities. Trigger enforcement must remain on;
        # bulk_update_replica_mode is unsafe here. No canonical UPDATE occurs.
        return super()._write_chunk(conn, ordinal, records, batch, 0)[1]

    def publish(self, *args, **kwargs):
        raise ValueError("classification rehearsal cannot publish")
