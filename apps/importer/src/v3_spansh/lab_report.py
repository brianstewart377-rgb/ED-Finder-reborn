"""Emit the deterministic Phase 4A integration-lab database receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any

import psycopg
from psycopg import sql

from .contracts import COPY_COLUMNS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--schema", default="v3_gen_phase4a_b")
    parser.add_argument("--negative-generation-key", default="phase4a_bad")
    args = parser.parse_args()
    receipt: dict[str, Any] = {
        "schema": args.schema,
        "tables": {},
        "table_content_aggregates": {},
    }
    digest = hashlib.sha256()
    with psycopg.connect(args.dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT generation.generation_id,generation.generation_key,current.publication_sequence
                 FROM v3_meta.current_canonical_generation current
                 JOIN v3_meta.canonical_generation generation USING(generation_id)"""
        )
        current = cur.fetchone()
        receipt["current"] = (
            {
                "generation_id": str(current[0]), "generation_key": current[1],
                "publication_sequence": int(current[2]),
            }
            if current else None
        )
        for table in COPY_COLUMNS:
            cur.execute(
                sql.SQL(
                    """SELECT count(*),
                              COALESCE(bit_xor(row_hash),0),
                              COALESCE(sum(row_hash::numeric),0)::text,
                              COALESCE(min(row_hash),0),
                              COALESCE(max(row_hash),0)
                         FROM (
                              SELECT hashtextextended(to_jsonb(row_value)::text,0) AS row_hash
                                FROM {}.{} row_value
                         ) hashed_rows"""
                ).format(sql.Identifier(args.schema), sql.Identifier(table))
            )
            count, xor_hash, sum_hash, min_hash, max_hash = cur.fetchone()
            aggregate = {
                "count": int(count),
                "xor_hash64": str(xor_hash),
                "sum_hash64": sum_hash,
                "min_hash64": str(min_hash),
                "max_hash64": str(max_hash),
            }
            receipt["tables"][table] = int(count)
            receipt["table_content_aggregates"][table] = aggregate
            digest.update(table.encode())
            digest.update(json.dumps(aggregate, sort_keys=True, separators=(",", ":")).encode())
            digest.update(b"\n")
        cur.execute(
            """SELECT run.source_run_id::text,run.run_state,run.scope_contract,
                      run.importer_version,encode(run.importer_code_sha256,'hex'),
                      encode(run.importer_config_sha256,'hex'),run.normalizer_version,
                      encode(run.normalizer_sha256,'hex'),
                      artifact.artifact_id::text,artifact.endpoint_identity,
                      encode(artifact.content_sha256,'hex'),artifact.size_bytes,
                      artifact.media_type,artifact.compression_code,artifact.retrieved_at,
                      artifact.effective_at,artifact.retention_class,
                      policy.policy_version,policy.rights_class,policy.retention_class
                 FROM v3_source.source_run run
                 JOIN v3_source.source_artifact artifact
                   ON artifact.artifact_id=run.artifact_id
                  AND artifact.source_id=run.source_id
                 JOIN v3_source.source_rights_policy policy
                   ON policy.rights_policy_id=run.rights_policy_id
                  AND policy.source_id=run.source_id
                ORDER BY run.started_at
                LIMIT 1"""
        )
        identity = cur.fetchone()
        receipt["artifact_run_identity"] = {
            "source_run_id": identity[0],
            "run_state": identity[1],
            "selector_config": identity[2],
            "importer_version": identity[3],
            "importer_code_sha256": identity[4],
            "importer_config_sha256": identity[5],
            "normalizer_version": identity[6],
            "normalizer_sha256": identity[7],
            "artifact_id": identity[8],
            "source_url": identity[9],
            "artifact_sha256": identity[10],
            "compressed_size_bytes": int(identity[11]),
            "media_type": identity[12],
            "compression": identity[13],
            "retrieved_at": identity[14].isoformat(),
            "effective_at": identity[15].isoformat() if identity[15] else None,
            "artifact_retention_class": identity[16],
            "rights_policy_version": identity[17],
            "rights_class": identity[18],
            "policy_retention_class": identity[19],
        }
        cur.execute(
            """SELECT detail->>'system_id64_first'
                 FROM v3_source.source_checkpoint
                WHERE checkpoint_state='SUCCEEDED'
                ORDER BY chunk_ordinal
                LIMIT 1"""
        )
        first_selected = cur.fetchone()[0]
        cur.execute(
            """SELECT detail->>'system_id64_last'
                 FROM v3_source.source_checkpoint
                WHERE checkpoint_state='SUCCEEDED'
                ORDER BY chunk_ordinal DESC
                LIMIT 1"""
        )
        last_selected = cur.fetchone()[0]
        system_identity_digest = hashlib.sha256()
        with conn.cursor(name="phase4b_system_identity_stream") as identity_cur:
            identity_cur.itersize = 10_000
            identity_cur.execute(
                sql.SQL("SELECT id64::text FROM {}.systems ORDER BY id64").format(
                    sql.Identifier(args.schema)
                )
            )
            for system_id64, in identity_cur:
                system_identity_digest.update(system_id64.encode())
                system_identity_digest.update(b"\n")
        cur.execute(
            sql.SQL("SELECT kind,count(*) FROM {}.rings GROUP BY kind ORDER BY kind").format(
                sql.Identifier(args.schema)
            )
        )
        ring_kinds = {kind.lower(): int(count) for kind, count in cur.fetchall()}
        cur.execute(
            sql.SQL(
                "SELECT count(*) FILTER (WHERE body_pk IS NOT NULL),"
                "count(*) FILTER (WHERE body_pk IS NULL) FROM {}.station_placement"
            ).format(sql.Identifier(args.schema))
        )
        nested_stations, top_level_stations = cur.fetchone()
        cur.execute(
            sql.SQL(
                "SELECT resolution_state,count(*) FROM {}.body_parent_evidence "
                "GROUP BY resolution_state ORDER BY resolution_state"
            ).format(sql.Identifier(args.schema))
        )
        parent_resolution = {state.lower(): int(count) for state, count in cur.fetchall()}
        cur.execute(
            sql.SQL(
                "SELECT count(*) FILTER (WHERE economy_weight IS NULL),"
                "count(*) FILTER (WHERE economy_weight=0) FROM {}.station_economy_current"
            ).format(sql.Identifier(args.schema))
        )
        null_economy_weights, zero_economy_weights = cur.fetchone()
        cur.execute(
            sql.SQL(
                "SELECT count(*) FILTER (WHERE is_primary),"
                "count(*) FILTER (WHERE is_secondary) FROM {}.station_economy_current"
            ).format(sql.Identifier(args.schema))
        )
        primary_economies, secondary_economies = cur.fetchone()
        cur.execute(
            """SELECT vocabulary_domain,raw_token
                 FROM v3_source.unmapped_vocabulary
                ORDER BY vocabulary_domain,raw_token"""
        )
        unmapped_tokens = [
            {"domain": domain, "raw_token": raw_token}
            for domain, raw_token in cur.fetchall()
        ]
        receipt["real_slice_manifest"] = {
            "parent_artifact_sha256": identity[10],
            "selector_version": identity[2]["selector_version"],
            "selector_config": identity[2],
            "first_selected_source_identity": first_selected,
            "last_selected_source_identity": last_selected,
            "systems_selected": receipt["tables"]["systems"],
            "entity_counts": {
                **receipt["tables"],
                "rings_only": ring_kinds.get("ring", 0),
                "belts": ring_kinds.get("belt", 0),
                "nested_stations": int(nested_stations),
                "top_level_stations": int(top_level_stations),
            },
            "selected_system_ids_sha256": system_identity_digest.hexdigest(),
        }
        receipt["validation"] = {
            "parent_resolution": parent_resolution,
            "economy_weights_null": int(null_economy_weights),
            "economy_weights_explicit_zero": int(zero_economy_weights),
            "primary_economy_designations": int(primary_economies),
            "secondary_economy_designations": int(secondary_economies),
            "unmapped_vocabulary": unmapped_tokens,
        }
        cur.execute(
            """SELECT validation_receipt
                 FROM v3_meta.canonical_generation
                WHERE generation_key=%s""",
            (args.schema.removeprefix("v3_gen_"),),
        )
        receipt["validation"]["frozen_finalizer_receipt"] = cur.fetchone()[0]
        cur.execute(
            """SELECT count(*) FILTER (WHERE convalidated),
                      count(*) FILTER (WHERE NOT convalidated)
                 FROM pg_constraint
                WHERE connamespace=%s::regnamespace AND contype='f'""",
            (args.schema,),
        )
        valid_fks, invalid_fks = cur.fetchone()
        receipt["validation"]["foreign_keys"] = {
            "validated": int(valid_fks), "not_valid": int(invalid_fks),
        }
        cur.execute(
            """SELECT count(*)
                 FROM v3_meta.canonical_generation_input generation_input
                 JOIN v3_meta.canonical_generation generation USING(generation_id)
                WHERE generation.relation_schema=%s""",
            (args.schema,),
        )
        receipt["validation"]["generation_input_manifest_rows"] = int(cur.fetchone()[0])
        cur.execute(
            """SELECT count(*) FILTER (WHERE checkpoint_state='SUCCEEDED'),
                      count(*) FILTER (WHERE checkpoint_state<>'SUCCEEDED')
                 FROM v3_source.source_checkpoint"""
        )
        succeeded, incomplete = cur.fetchone()
        receipt["checkpoints"] = {"succeeded": int(succeeded), "incomplete": int(incomplete)}
        cur.execute("SELECT count(*) FROM v3_async.job_attempt")
        receipt["job_attempts"] = int(cur.fetchone()[0])
        cur.execute(
            """SELECT count(*) FROM v3_async.effect_receipt
                WHERE consumer_name='phase4a-cache-invalidator'"""
        )
        receipt["effect_receipts"] = int(cur.fetchone()[0])
        cur.execute(
            sql.SQL("""SELECT economy_weight,is_primary,is_secondary
                          FROM {}.station_economy_current economy
                          JOIN {}.stations station USING(station_pk)
                         WHERE station.market_id=128130808 AND station.name='Macmillan Depot'""").format(
                sql.Identifier(args.schema), sql.Identifier(args.schema)
            )
        )
        economy = cur.fetchone()
        receipt["macmillan_depot"] = (
            {
                "selected_in_snapshot_slice": True,
                "market_id": "128130808", "economy_weight": economy[0],
                "is_primary": economy[1], "is_secondary": economy[2],
            }
            if economy
            else {
                "selected_in_snapshot_slice": False,
                "acceptance_authority": "sql/v3/tests/001_v3_baseline.sql",
                "semantic_case": "raw Spansh 100.0 evidence; canonical economy_weight NULL",
            }
        )
        cur.execute("SELECT pg_database_size(current_database())")
        receipt["database_size_bytes"] = int(cur.fetchone()[0])
        cur.execute(
            """SELECT temp_files,temp_bytes,blk_read_time,blk_write_time,
                      tup_inserted,tup_updated,tup_deleted
                 FROM pg_stat_database
                WHERE datname=current_database()"""
        )
        temp_files, temp_bytes, block_read_ms, block_write_ms, inserted, updated, deleted = cur.fetchone()
        cur.execute(
            """SELECT COALESCE(sum(n_dead_tup),0)::bigint
                 FROM pg_stat_user_tables
                WHERE schemaname=%s""",
            (args.schema,),
        )
        receipt["database_activity"] = {
            "temp_files": int(temp_files),
            "temp_bytes": int(temp_bytes),
            "block_read_milliseconds": float(block_read_ms),
            "block_write_milliseconds": float(block_write_ms),
            "tuples_inserted": int(inserted),
            "tuples_updated": int(updated),
            "tuples_deleted": int(deleted),
            "estimated_dead_tuples": int(cur.fetchone()[0]),
        }
        cur.execute("SELECT count(*) FROM v3_source.unmapped_vocabulary")
        receipt["unmapped_vocabulary_rows"] = int(cur.fetchone()[0])
        cur.execute(
            "SELECT lifecycle_state,failure_reason FROM v3_meta.canonical_generation WHERE generation_key=%s",
            (args.negative_generation_key,),
        )
        negative = cur.fetchone()
        receipt["negative_candidate"] = (
            {"state": negative[0], "failure_reason": negative[1]} if negative else None
        )
    receipt["canonical_logical_sha256"] = digest.hexdigest()
    receipt["real_slice_manifest"]["canonical_content_sha256"] = digest.hexdigest()
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
