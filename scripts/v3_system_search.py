#!/usr/bin/env python3
'''Build and validate the generation-scoped V3 system-search product.

Ratings V4 owns the base derived generation. Search attaches an independent
manifest and lifecycle to that same generation, consumes only committed Ratings
chunks plus the pinned canonical generation, and never publishes anything.
'''
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PRODUCT_CODE = 'system_search'
PRODUCT_VERSION = 'v3-system-search-2'
GENERATION_KEY = re.compile(r'[a-z][a-z0-9_]{0,62}\Z')
SCHEMA_NAME = re.compile(r'v3_gen_[a-z][a-z0-9_]{0,30}\Z')
Progress = Callable[[dict], None]
EXPECTED_MANIFEST_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


class ExpectedManifestMismatch(ValueError):
    """The computed or registered manifest differs from operator evidence."""


@dataclass(frozen=True)
class Generation:
    identifier: str
    key: str
    state: str
    canonical_generation_id: str
    canonical_publication_sequence: int
    canonical_schema: str
    expected_systems: int


def _json(value) -> str:
    def convert(item):
        if isinstance(item, (bytes, bytearray, memoryview)):
            return bytes(item).hex()
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat()
        return str(item)

    return json.dumps(
        value, default=convert, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False,
    )


def _digest(value) -> bytes:
    return hashlib.sha256(_json(value).encode()).digest()


def code_identity(root: Path = ROOT) -> dict[str, str]:
    files = (
        'scripts/v3_system_search.py',
        'sql/v3/migrations/004_v3_search_spatial_clusters.sql',
        'sql/v3/migrations/006_v3_derived_product_lifecycle.sql',
        'sql/v3/migrations/010_v3_system_search_body_type_counts.sql',
    )
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in files
    }


def _generation(connection, generation_key: str) -> Generation:
    if not GENERATION_KEY.fullmatch(generation_key):
        raise ValueError('invalid derived generation key')
    row = connection.execute(
        '''SELECT derived_generation_id::text,generation_key,lifecycle_state,
                  canonical_generation_id::text,canonical_publication_sequence,
                  manifest,expected_systems
             FROM v3_meta.derived_generation
            WHERE generation_key=%s''',
        (generation_key,),
    ).fetchone()
    if row is None:
        raise ValueError('unknown derived generation')
    manifest = row[5]
    schema = manifest.get('canonical_schema') if isinstance(manifest, dict) else None
    if not isinstance(schema, str) or not SCHEMA_NAME.fullmatch(schema):
        raise ValueError('derived generation has invalid canonical schema')
    return Generation(
        identifier=row[0],
        key=row[1],
        state=row[2],
        canonical_generation_id=row[3],
        canonical_publication_sequence=int(row[4]),
        canonical_schema=schema,
        expected_systems=int(row[6]),
    )


def product_manifest(generation: Generation) -> dict:
    return {
        'product_code': PRODUCT_CODE,
        'product_version': PRODUCT_VERSION,
        'derived_generation_id': generation.identifier,
        'generation_key': generation.key,
        'canonical_generation_id': generation.canonical_generation_id,
        'canonical_publication_sequence': generation.canonical_publication_sequence,
        'canonical_schema': generation.canonical_schema,
        'expected_rows': generation.expected_systems,
        'code_sha256': code_identity(),
        'field_policy': {
            'position': 'canonical systems x/y/z; cube point is derived from those exact LY coordinates',
            'body_count': 'Ratings V4 loaded_body_count for the same generation/system',
            'landable_count': 'ACTIVE canonical bodies with is_landable=true',
            'station_count': 'ACTIVE canonical stations',
            'has_rings': (
                'positive observation of an ACTIVE canonical RING; asteroid BELT rows '
                'do not set this Finder flag'
            ),
            'has_biologicals': 'positive biological signal_count on an ACTIVE canonical body',
            'has_geologicals': 'positive geological signal_count on an ACTIVE canonical body',
            'has_terraformable': 'ACTIVE canonical body with terraforming state terraformable/terraformed/terraforming',
            'main_star_class': 'lowest-body_pk Ratings V4 mechanics body explicitly marked is_main_star=true',
            'source_observed_at': 'canonical systems.source_updated_at',
            'completeness': 'minimum of the seven frozen Ratings V4 completeness values',
            'confidence': 'minimum of the seven frozen Ratings V4 confidence values',
            'false_flags': (
                'false means no positive observation in the pinned canonical generation, '
                'not proof of known absence'
            ),
            'per_type_counts': (
                'counts of ACTIVE v3_derived.body_mechanics rows whose normalised '
                'body_class matches each Finder type; barycentre/belt-cluster bodies '
                'are excluded from body_mechanics and therefore uncounted'
            ),
            'star_counts': (
                'black_hole/neutron/white_dwarf resolved from body_class or '
                'spectral_class; other_star_count = star rows (spectral_class present) '
                'minus those three'
            ),
            'walkable_count': 'landable ACTIVE bodies with no/absent atmosphere (atmosphere_classification no_atmosphere or null)',
            'ring_count': 'ACTIVE canonical RING rows (asteroid BELT rows excluded)',
            'bio_signal_total': 'sum of biological signal_count over ACTIVE bodies',
            'geo_signal_total': 'sum of geological signal_count over ACTIVE bodies',
            'zero_counts': 'a 0 count means no positive observation in the pinned canonical generation, not proven absence',
        },
    }


def _product(connection, generation_id: str):
    return connection.execute(
        '''SELECT product_version,lifecycle_state,manifest,manifest_sha256,
                  expected_rows,validation_receipt,validation_sha256
             FROM v3_meta.derived_product
            WHERE derived_generation_id=%s AND product_code=%s''',
        (generation_id, PRODUCT_CODE),
    ).fetchone()


def register_product(
    connection,
    generation_key: str,
    expected_manifest_sha256: str | None = None,
) -> tuple[Generation, str, bytes]:
    if (
        expected_manifest_sha256 is not None
        and EXPECTED_MANIFEST_SHA256.fullmatch(expected_manifest_sha256) is None
    ):
        raise ExpectedManifestMismatch('expected_manifest_mismatch')
    generation = _generation(connection, generation_key)
    if generation.state not in {'BUILDING', 'VALIDATING', 'READY', 'PUBLISHED'}:
        raise ValueError('generation cannot accept a Search product')
    manifest = product_manifest(generation)
    manifest_sha = _digest(manifest)
    if (
        expected_manifest_sha256 is not None
        and manifest_sha.hex() != expected_manifest_sha256
    ):
        raise ExpectedManifestMismatch('expected_manifest_mismatch')

    existing = _product(connection, generation.identifier)
    if (
        existing is not None
        and expected_manifest_sha256 is not None
        and bytes(existing[3]).hex() != expected_manifest_sha256
    ):
        raise ExpectedManifestMismatch('expected_manifest_mismatch')
    if existing is None:
        with connection.transaction():
            connection.execute(
                '''INSERT INTO v3_meta.derived_product(
                       derived_generation_id,product_code,product_version,
                       manifest,manifest_sha256,expected_rows)
                   VALUES (%s,%s,%s,%s::jsonb,%s,%s)''',
                (
                    generation.identifier, PRODUCT_CODE, PRODUCT_VERSION,
                    _json(manifest), manifest_sha, generation.expected_systems,
                ),
            )
        existing = _product(connection, generation.identifier)

    if existing is None:
        raise ValueError('Search product registration failed')
    if (
        existing[0] != PRODUCT_VERSION
        or existing[2] != manifest
        or bytes(existing[3]) != manifest_sha
        or int(existing[4]) != generation.expected_systems
    ):
        raise ValueError('existing Search product manifest differs from current code/input')
    return generation, existing[1], manifest_sha


def _source_projection_sha(
    generation: Generation,
    manifest_sha: bytes,
    ordinal: int,
    canonical_input_sha: bytes,
    ratings_content_sha: bytes,
) -> bytes:
    return _digest({
        'product': PRODUCT_CODE,
        'product_version': PRODUCT_VERSION,
        'derived_generation_id': generation.identifier,
        'canonical_generation_id': generation.canonical_generation_id,
        'canonical_publication_sequence': generation.canonical_publication_sequence,
        'chunk_ordinal': ordinal,
        'product_manifest_sha256': manifest_sha.hex(),
        'ratings_canonical_input_sha256': bytes(canonical_input_sha).hex(),
        'ratings_content_sha256': bytes(ratings_content_sha).hex(),
    })


def _rating_chunks(connection, generation_id: str, *, missing_search_only: bool = False):
    if missing_search_only:
        return connection.execute(
            '''SELECT b.chunk_ordinal,b.systems,b.canonical_input_sha256,b.content_sha256
                 FROM v3_derived.build_chunk b
            LEFT JOIN v3_derived.search_build_chunk s
                   ON s.derived_generation_id=b.derived_generation_id
                  AND s.chunk_ordinal=b.chunk_ordinal
                WHERE b.derived_generation_id=%s
                  AND s.chunk_ordinal IS NULL
                ORDER BY b.chunk_ordinal''',
            (generation_id,),
        ).fetchall()
    return connection.execute(
        '''SELECT chunk_ordinal,systems,canonical_input_sha256,content_sha256
             FROM v3_derived.build_chunk
            WHERE derived_generation_id=%s
            ORDER BY chunk_ordinal''',
        (generation_id,),
    ).fetchall()


def _missing_chunk_systems(
    connection, generation: Generation, ordinal: int, limit: int = 20,
) -> list[int]:
    '''Bounded sample of Ratings systems the projection did not cover.

    A coverage mismatch is a fail-closed guard, and without this the operator
    cannot tell which id64s the canonical schema lacks. Read-only; the caller
    raises right after.
    '''
    try:
        from psycopg import sql
        rows = connection.execute(
            sql.SQL('''SELECT v.system_id64
                     FROM v3_derived.system_rating_vector v
                LEFT JOIN {schema}.systems s ON s.id64=v.system_id64
                    WHERE v.derived_generation_id=%(generation_id)s
                      AND v.chunk_ordinal=%(chunk_ordinal)s
                      AND s.id64 IS NULL
                    ORDER BY v.system_id64 LIMIT %(limit)s''').format(
                schema=sql.Identifier(generation.canonical_schema),
            ),
            {'generation_id': generation.identifier, 'chunk_ordinal': ordinal,
             'limit': limit},
        ).fetchall()
    except Exception:  # diagnostic only; the coverage error is what surfaces
        return []
    return [int(row[0]) for row in rows]


LOCK_TIMEOUT = '30s'
STATEMENT_TIMEOUT = '30min'


def configure_session(connection) -> None:
    '''Bound the serial builder session and force per-execution plans.

    Mirrors the parallel scheduler's lock/statement bounds so a blocked
    FOR SHARE / FOR NO KEY UPDATE cannot hang the worker silently, and keeps
    every parameterised read on its index path (psycopg names statements
    after five executions and a generic plan may discard the index path).
    Validation's position scan over the whole relation must stay under the
    statement bound; VACUUM runs from the operator action via psql.
    '''
    connection.execute(
        "SELECT set_config('plan_cache_mode','force_custom_plan',false)",
    )
    connection.execute(
        "SELECT set_config('lock_timeout',%s,false)", (LOCK_TIMEOUT,),
    )
    connection.execute(
        "SELECT set_config('statement_timeout',%s,false)", (STATEMENT_TIMEOUT,),
    )


CREDENTIAL_URI = re.compile(r'[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@', re.IGNORECASE)


def redact_credentials(text: str, dsn: str | None = None) -> str:
    '''Strip credential-bearing URIs (and the live DSN itself) from text.

    libpq/psycopg connection errors can echo the full DSN, password included.
    '''
    if dsn:
        text = text.replace(dsn, '<redacted-dsn>')
    return CREDENTIAL_URI.sub('<redacted-dsn>@', text)


def failure_receipt(exc: BaseException, dsn: str | None = None) -> dict:
    '''Bounded failure receipt: class, message and SQLSTATE, never the DSN.'''
    dsn = dsn if dsn is not None else os.environ.get('V3_SYSTEM_SEARCH_DATABASE_URL')
    detail = redact_credentials(f'{type(exc).__name__}: {exc}', dsn)
    receipt = {
        'status': 'FAILED',
        'error': type(exc).__name__,
        'error_detail': detail[:500],
    }
    sqlstate = getattr(exc, 'sqlstate', None)
    if isinstance(sqlstate, str):
        receipt['sqlstate'] = sqlstate
    return receipt


def _chunk_content_sha(connection, generation_id: str, ordinal: int) -> bytes:
    rows = connection.execute(
        '''SELECT s.system_id64,s.name,s.x_ly,s.y_ly,s.z_ly,
                  s.galaxy_region_id,s.region_name,s.main_star_class,
                  s.body_count,s.landable_count,s.station_count,s.has_rings,
                  s.has_biologicals,s.has_geologicals,s.has_terraformable,
                  s.source_observed_at,s.completeness,s.confidence,
                  s.elw_count,s.ww_count,s.ammonia_count,s.terraformable_count,
                  s.gas_giant_count,s.hmc_count,s.metal_rich_count,s.rocky_count,
                  s.rocky_ice_count,s.icy_count,s.black_hole_count,s.neutron_count,
                  s.white_dwarf_count,s.other_star_count,s.ring_count,s.walkable_count,
                  s.bio_signal_total,s.geo_signal_total
             FROM v3_derived.system_search s
             JOIN v3_derived.system_rating_vector v
               ON v.derived_generation_id=s.derived_generation_id
              AND v.system_id64=s.system_id64
            WHERE s.derived_generation_id=%s AND v.chunk_ordinal=%s
            ORDER BY s.system_id64''',
        (generation_id, ordinal),
    ).fetchall()
    return _digest(rows)


def projection_query_sql() -> str:
    """Read projection shared by the writer and the read-only operator profiler.

    Only {schema} is an SQL-format identifier; values are named DB parameters.
    LATERAL lookups correlate directly with the final target row: putting these
    in target-scanning CTEs creates quadratic joins back to that same target.
    LIMIT 1 preserves the lowest-body_pk main-star rule.
    """
    return '''
WITH target AS MATERIALIZED (
    SELECT v.system_id64,v.loaded_body_count,v.completeness,v.confidence
      FROM v3_derived.system_rating_vector v
     WHERE v.derived_generation_id=%(generation_id)s AND v.chunk_ordinal=%(chunk_ordinal)s
),
body_summary AS (
    SELECT b.system_id64,
           (count(*) FILTER (
               WHERE b.lifecycle_state='ACTIVE' AND b.is_landable IS TRUE
           ))::integer AS landable_count,
           (count(*) FILTER (
               WHERE b.lifecycle_state='ACTIVE' AND b.is_landable IS TRUE
                 AND (b.atmosphere_classification_id IS NULL OR atmo.public_code='no_atmosphere')
           ))::integer AS walkable_count,
           bool_or(
               b.lifecycle_state='ACTIVE'
               AND ts.public_code IN ('terraformable','terraformed','terraforming')
           ) AS has_terraformable
      FROM {schema}.bodies b
      JOIN target t ON t.system_id64=b.system_id64
 LEFT JOIN v3_vocab.terraforming_state ts
        ON ts.terraforming_state_id=b.terraforming_state_id
 LEFT JOIN v3_vocab.atmosphere_classification atmo
        ON atmo.atmosphere_classification_id=b.atmosphere_classification_id
  GROUP BY b.system_id64
),
type_summary AS (
    SELECT bm.system_id64,
           (count(*) FILTER (WHERE nk IN ('earth like world','earthlike world','elw')))::integer AS elw_count,
           (count(*) FILTER (WHERE nk IN ('water world','ww')))::integer AS ww_count,
           (count(*) FILTER (WHERE nk IN ('ammonia world','ammonia')))::integer AS ammonia_count,
           (count(*) FILTER (WHERE bm.terraformable IS TRUE))::integer AS terraformable_count,
           (count(*) FILTER (WHERE nk LIKE '%%gas giant%%'))::integer AS gas_giant_count,
           (count(*) FILTER (WHERE nk IN ('high metal content world','high metal content body','high metal content','hmc')))::integer AS hmc_count,
           (count(*) FILTER (WHERE nk IN ('metal rich body','metal rich')))::integer AS metal_rich_count,
           (count(*) FILTER (WHERE nk IN ('rocky body','rocky')))::integer AS rocky_count,
           (count(*) FILTER (WHERE nk IN ('rocky ice body','rocky ice world','rocky ice')))::integer AS rocky_ice_count,
           (count(*) FILTER (WHERE nk IN ('icy body','icy')))::integer AS icy_count,
           (count(*) FILTER (WHERE nk LIKE '%%black hole%%' OR bm.spectral_class IN ('H','SupermassiveBlackHole')))::integer AS black_hole_count,
           (count(*) FILTER (WHERE nk='neutron star' OR bm.spectral_class='N'))::integer AS neutron_count,
           (count(*) FILTER (WHERE nk LIKE '%%white dwarf%%' OR bm.spectral_class LIKE 'D%%'))::integer AS white_dwarf_count,
           (count(*) FILTER (
               WHERE bm.spectral_class IS NOT NULL
                 AND NOT (COALESCE(nk LIKE '%%black hole%%', false) OR bm.spectral_class IN ('H','SupermassiveBlackHole'))
                 AND NOT (COALESCE(nk='neutron star', false) OR bm.spectral_class='N')
                 AND NOT (COALESCE(nk LIKE '%%white dwarf%%', false) OR bm.spectral_class LIKE 'D%%')
           ))::integer AS other_star_count
      FROM (
          SELECT bm.system_id64, bm.terraformable, bm.spectral_class,
                 lower(btrim(regexp_replace(regexp_replace(bm.body_class,'[-]',' ','g'),'\\s+',' ','g'))) AS nk
            FROM v3_derived.body_mechanics bm
           WHERE bm.derived_generation_id=%(generation_id)s
      ) bm
      JOIN target t ON t.system_id64=bm.system_id64
  GROUP BY bm.system_id64
),
ring_summary AS (
    SELECT r.system_id64,
           (count(*) FILTER (WHERE r.lifecycle_state='ACTIVE' AND r.kind='RING'))::integer AS ring_count
      FROM {schema}.rings r
      JOIN target t ON t.system_id64=r.system_id64
  GROUP BY r.system_id64
),
station_summary AS (
    SELECT st.system_id64,
           (count(*) FILTER (WHERE st.lifecycle_state='ACTIVE'))::integer
               AS station_count
      FROM {schema}.stations st
      JOIN target t ON t.system_id64=st.system_id64
  GROUP BY st.system_id64
)
SELECT %(generation_id)s,t.system_id64,s.name,s.x_ly,s.y_ly,s.z_ly,
       cube(ARRAY[s.x_ly,s.y_ly,s.z_ly]),
       s.galaxy_region_id,gr.display_name,ms.main_star_class,
       t.loaded_body_count,COALESCE(bs.landable_count,0),
       COALESCE(ss.station_count,0),COALESCE(rs.ring_count,0)>0,
       COALESCE(sig.bio_signal_total,0)>0,
       COALESCE(sig.geo_signal_total,0)>0,
       COALESCE(bs.has_terraformable,false),
       s.source_updated_at,
       (SELECT min(value)::double precision/10000 FROM unnest(t.completeness) AS value),
       (SELECT min(value)::double precision/10000 FROM unnest(t.confidence) AS value),
       COALESCE(ty.elw_count,0),COALESCE(ty.ww_count,0),COALESCE(ty.ammonia_count,0),
       COALESCE(ty.terraformable_count,0),COALESCE(ty.gas_giant_count,0),
       COALESCE(ty.hmc_count,0),COALESCE(ty.metal_rich_count,0),COALESCE(ty.rocky_count,0),
       COALESCE(ty.rocky_ice_count,0),COALESCE(ty.icy_count,0),
       COALESCE(ty.black_hole_count,0),COALESCE(ty.neutron_count,0),
       COALESCE(ty.white_dwarf_count,0),COALESCE(ty.other_star_count,0),
       COALESCE(rs.ring_count,0),COALESCE(bs.walkable_count,0),
       COALESCE(sig.bio_signal_total,0),COALESCE(sig.geo_signal_total,0)
  FROM target t
  JOIN {schema}.systems s ON s.id64=t.system_id64
 LEFT JOIN v3_vocab.galaxy_region gr ON gr.galaxy_region_id=s.galaxy_region_id
 LEFT JOIN body_summary bs USING(system_id64)
 LEFT JOIN type_summary ty USING(system_id64)
 LEFT JOIN ring_summary rs USING(system_id64)
 LEFT JOIN station_summary ss USING(system_id64)
 LEFT JOIN LATERAL (
    SELECT sum(bs.signal_count) FILTER (WHERE st.public_code='saa_signaltype_biological')::integer AS bio_signal_total,
           sum(bs.signal_count) FILTER (WHERE st.public_code='saa_signaltype_geological')::integer AS geo_signal_total
      FROM {schema}.bodies b
      JOIN {schema}.body_signal_current bs ON bs.body_pk=b.body_pk
      JOIN v3_vocab.signal_type st ON st.signal_type_id=bs.signal_type_id
     WHERE b.system_id64=t.system_id64 AND b.lifecycle_state='ACTIVE'
 ) sig ON true
 LEFT JOIN LATERAL (
    SELECT bm.body_class AS main_star_class
      FROM v3_derived.body_mechanics bm
     WHERE bm.derived_generation_id=%(generation_id)s
       AND bm.system_id64=t.system_id64 AND bm.is_main_star IS TRUE
  ORDER BY bm.body_pk
     LIMIT 1
 ) ms ON true
ORDER BY t.system_id64
    '''.strip()


def _insert_chunk(
    connection,
    generation: Generation,
    manifest_sha: bytes,
    chunk,
) -> bool:
    from psycopg import sql

    ordinal, systems, canonical_input_sha, ratings_content_sha = chunk
    ordinal = int(ordinal)
    systems = int(systems)
    if ordinal < 0 or not 1 <= systems <= 1000:
        raise ValueError('Search chunk is outside the bounded Ratings contract')
    source_sha = _source_projection_sha(
        generation, manifest_sha, ordinal,
        bytes(canonical_input_sha), bytes(ratings_content_sha),
    )

    with connection.transaction():
        # Match the insert guard's base -> product lock order. Shared locks
        # permit disjoint chunks while excluding lifecycle/publication changes.
        base = connection.execute(
            '''SELECT lifecycle_state FROM v3_meta.derived_generation
                WHERE derived_generation_id=%s FOR SHARE''',
            (generation.identifier,),
        ).fetchone()
        if base is None or base[0] not in {
            'BUILDING', 'VALIDATING', 'READY', 'PUBLISHED',
        }:
            raise ValueError('generation cannot accept Search rows')
        product = connection.execute(
            '''SELECT lifecycle_state,manifest_sha256
                 FROM v3_meta.derived_product
                WHERE derived_generation_id=%s AND product_code=%s
                FOR SHARE''',
            (generation.identifier, PRODUCT_CODE),
        ).fetchone()
        if product is None or product[0] != 'BUILDING':
            raise ValueError('Search product is not BUILDING')
        if bytes(product[1]) != manifest_sha:
            raise ValueError('Search product manifest changed')

        # A committed Ratings receipt is the stable per-chunk mutex. Both the
        # serial and parallel paths use it before checking Search's receipt.
        # NO KEY UPDATE also permits the receipt's deferred FK KEY SHARE lock.
        source = connection.execute(
            '''SELECT systems,canonical_input_sha256,content_sha256
                 FROM v3_derived.build_chunk
                WHERE derived_generation_id=%s AND chunk_ordinal=%s
                FOR NO KEY UPDATE''',
            (generation.identifier, ordinal),
        ).fetchone()
        if (source is None or int(source[0]) != systems
                or bytes(source[1]) != bytes(canonical_input_sha)
                or bytes(source[2]) != bytes(ratings_content_sha)):
            raise ValueError('Search Ratings chunk source changed')

        receipt = connection.execute(
            '''SELECT projection_version,source_projection_sha256,content_sha256,systems
                 FROM v3_derived.search_build_chunk
                WHERE derived_generation_id=%s AND chunk_ordinal=%s''',
            (generation.identifier, ordinal),
        ).fetchone()
        if receipt is not None:
            if (
                receipt[0] != PRODUCT_VERSION
                or bytes(receipt[1]) != source_sha
                or int(receipt[3]) != systems
            ):
                raise ValueError('resumed Search chunk source/coverage changed')
            return False

        preexisting = connection.execute(
            '''SELECT count(*)
                 FROM v3_derived.system_search s
                 JOIN v3_derived.system_rating_vector v
                   ON v.derived_generation_id=s.derived_generation_id
                  AND v.system_id64=s.system_id64
                WHERE s.derived_generation_id=%s AND v.chunk_ordinal=%s''',
            (generation.identifier, ordinal),
        ).fetchone()[0]
        if preexisting:
            raise ValueError('unreceipted Search rows already exist for chunk')

        # Bulk-write safety exception: only derived Search rows/receipts are
        # inserted. No canonical systems/bodies/clusters/ratings are updated.
        # Keep origin triggers and FKs active: they enforce insert-only data,
        # generation lifecycle, and the atomic receipt contract.
        query = sql.SQL(
            '''INSERT INTO v3_derived.system_search(
                derived_generation_id,system_id64,name,x_ly,y_ly,z_ly,position_ly,
                galaxy_region_id,region_name,main_star_class,body_count,landable_count,
                station_count,has_rings,has_biologicals,has_geologicals,
                has_terraformable,source_observed_at,completeness,confidence,
                elw_count,ww_count,ammonia_count,terraformable_count,gas_giant_count,
                hmc_count,metal_rich_count,rocky_count,rocky_ice_count,icy_count,
                black_hole_count,neutron_count,white_dwarf_count,other_star_count,
                ring_count,walkable_count,bio_signal_total,geo_signal_total
            )
            '''
        ) + sql.SQL(projection_query_sql()).format(
            schema=sql.Identifier(generation.canonical_schema),
        )
        # The bounded indexed projection is too short to amortize compilation.
        # READ ONLY profiles prove this; keep the setting transaction-local.
        connection.execute('SET LOCAL jit=off')
        cursor = connection.execute(
            query,
            {'generation_id': generation.identifier, 'chunk_ordinal': ordinal},
        )
        if cursor.rowcount != systems:
            missing = _missing_chunk_systems(
                connection, generation, ordinal,
            )
            print(json.dumps({
                'status': 'DIAGNOSTIC', 'product_code': PRODUCT_CODE,
                'chunk_ordinal': ordinal, 'expected_systems': systems,
                'inserted_rows': cursor.rowcount,
                'missing_sample': missing,
            }, sort_keys=True), file=sys.stderr, flush=True)
            raise ValueError('Search chunk did not cover every Ratings system')

        content_sha = _chunk_content_sha(connection, generation.identifier, ordinal)
        connection.execute(
            '''INSERT INTO v3_derived.search_build_chunk(
                   derived_generation_id,chunk_ordinal,product_code,projection_version,
                   source_projection_sha256,content_sha256,systems)
               VALUES (%s,%s,%s,%s,%s,%s,%s)''',
            (
                generation.identifier, ordinal, PRODUCT_CODE, PRODUCT_VERSION,
                source_sha, content_sha, systems,
            ),
        )
    return True


def build_available(
    connection,
    generation: Generation,
    manifest_sha: bytes,
    *,
    max_chunks: int | None = None,
    progress: Progress | None = None,
) -> dict:
    if max_chunks is not None and (type(max_chunks) is not int or max_chunks <= 0):
        raise ValueError('max_chunks must be a positive integer')
    chunks_seen = chunks_written = 0
    for chunk in _rating_chunks(connection, generation.identifier, missing_search_only=True):
        written = _insert_chunk(connection, generation, manifest_sha, chunk)
        chunks_seen += 1
        chunks_written += int(written)
        if written and progress is not None:
            progress({
                'derived_generation_id': generation.identifier,
                'product_code': PRODUCT_CODE,
                'chunk_ordinal': int(chunk[0]),
                'systems': int(chunk[1]),
                'written': True,
            })
        if max_chunks is not None and chunks_written >= max_chunks:
            break
    return {'chunks_seen': chunks_seen, 'chunks_written': chunks_written}


def validate_product(
    connection,
    generation: Generation,
    manifest_sha: bytes,
) -> dict:
    generation = _generation(connection, generation.key)
    product = _product(connection, generation.identifier)
    if product is None:
        raise ValueError('Search product is not registered')
    if product[1] == 'READY':
        receipt = product[5]
        if not isinstance(receipt, dict) or receipt.get('status') != 'VERIFIED':
            raise ValueError('READY Search product has no VERIFIED receipt')
        return receipt
    if product[1] != 'BUILDING':
        raise ValueError('Search product cannot be validated from current state')

    vector_count = int(connection.execute(
        'SELECT count(*) FROM v3_derived.system_rating_vector WHERE derived_generation_id=%s',
        (generation.identifier,),
    ).fetchone()[0])
    search_count = int(connection.execute(
        'SELECT count(*) FROM v3_derived.system_search WHERE derived_generation_id=%s',
        (generation.identifier,),
    ).fetchone()[0])
    rating_chunks = _rating_chunks(connection, generation.identifier)
    search_chunks = connection.execute(
        '''SELECT chunk_ordinal,projection_version,source_projection_sha256,
                  content_sha256,systems
             FROM v3_derived.search_build_chunk
            WHERE derived_generation_id=%s
            ORDER BY chunk_ordinal''',
        (generation.identifier,),
    ).fetchall()

    complete = (
        generation.state in {'VALIDATING', 'READY', 'PUBLISHED'}
        and vector_count == generation.expected_systems
        and search_count == generation.expected_systems
        and len(rating_chunks) == len(search_chunks)
        and sum(int(row[1]) for row in rating_chunks) == generation.expected_systems
        and all(int(row[0]) == index for index, row in enumerate(rating_chunks))
    )
    if not complete:
        return {
            'status': 'INCOMPLETE',
            'derived_generation_id': generation.identifier,
            'product_code': PRODUCT_CODE,
            'base_lifecycle_state': generation.state,
            'expected_systems': generation.expected_systems,
            'rating_systems': vector_count,
            'search_systems': search_count,
            'rating_chunks': len(rating_chunks),
            'search_chunks': len(search_chunks),
        }

    validation_digest = hashlib.sha256()
    validation_digest.update(manifest_sha)
    for rating, search in zip(rating_chunks, search_chunks, strict=True):
        ordinal, systems, canonical_input_sha, ratings_content_sha = rating
        if int(search[0]) != int(ordinal):
            raise ValueError('Search chunk receipt ordinal gap')
        if search[1] != PRODUCT_VERSION or int(search[4]) != int(systems):
            raise ValueError('Search chunk receipt version/coverage mismatch')
        expected_source = _source_projection_sha(
            generation, manifest_sha, int(ordinal),
            bytes(canonical_input_sha), bytes(ratings_content_sha),
        )
        if bytes(search[2]) != expected_source:
            raise ValueError('Search chunk source seal mismatch')
        validation_digest.update(_json({
            'chunk_ordinal': int(ordinal),
            'source_projection_sha256': bytes(search[2]).hex(),
            'content_sha256': bytes(search[3]).hex(),
            'systems': int(search[4]),
        }).encode())

    invalid_positions = int(connection.execute(
        '''SELECT count(*)
             FROM v3_derived.system_search
            WHERE derived_generation_id=%s
              AND cube_distance(position_ly,cube(ARRAY[x_ly,y_ly,z_ly]))<>0''',
        (generation.identifier,),
    ).fetchone()[0])
    if invalid_positions:
        raise ValueError('Search cube position differs from numeric LY truth')

    receipt = {
        'status': 'VERIFIED',
        'derived_generation_id': generation.identifier,
        'product_code': PRODUCT_CODE,
        'product_version': PRODUCT_VERSION,
        'expected_systems': generation.expected_systems,
        'systems': search_count,
        'chunks': len(search_chunks),
        'coverage_complete': True,
        'source_seals_verified': True,
        'position_truth_verified': True,
        'base_lifecycle_state': generation.state,
        'product_manifest_sha256': manifest_sha.hex(),
    }
    validation_digest.update(_json(receipt).encode())
    validation_sha = validation_digest.digest()
    with connection.transaction():
        updated = connection.execute(
            '''UPDATE v3_meta.derived_product
                  SET lifecycle_state='READY',validation_receipt=%s::jsonb,
                      validation_sha256=%s,validated_at=now()
                WHERE derived_generation_id=%s AND product_code=%s
                  AND lifecycle_state='BUILDING' ''',
            (_json(receipt), validation_sha, generation.identifier, PRODUCT_CODE),
        ).rowcount
        if updated != 1:
            raise ValueError('Search product validation state changed')
    return receipt


def run(
    connection,
    generation_key: str,
    *,
    follow: bool = False,
    poll_seconds: float = 5.0,
    max_chunks: int | None = None,
    progress: Progress | None = None,
    expected_manifest_sha256: str | None = None,
) -> dict:
    if not isinstance(poll_seconds, (int, float)) or poll_seconds <= 0:
        raise ValueError('poll_seconds must be positive')
    generation, state, manifest_sha = register_product(
        connection, generation_key, expected_manifest_sha256,
    )
    total_seen = total_written = 0

    if state == 'READY':
        receipt = validate_product(connection, generation, manifest_sha)
        return {
            'status': 'VERIFIED',
            'derived_generation_id': generation.identifier,
            'product_code': PRODUCT_CODE,
            'chunks_seen': 0,
            'chunks_written': 0,
            'validation_receipt': receipt,
        }
    if state != 'BUILDING':
        raise ValueError('Search product cannot resume from current state')

    while True:
        result = build_available(
            connection, generation, manifest_sha,
            max_chunks=max_chunks, progress=progress,
        )
        total_seen += result['chunks_seen']
        total_written += result['chunks_written']
        generation = _generation(connection, generation_key)

        receipt = validate_product(connection, generation, manifest_sha)
        if receipt.get('status') == 'VERIFIED':
            return {
                'status': 'VERIFIED',
                'derived_generation_id': generation.identifier,
                'product_code': PRODUCT_CODE,
                'chunks_seen': total_seen,
                'chunks_written': total_written,
                'validation_receipt': receipt,
            }
        if not follow or max_chunks is not None:
            return {
                'status': 'INCOMPLETE',
                'derived_generation_id': generation.identifier,
                'product_code': PRODUCT_CODE,
                'chunks_seen': total_seen,
                'chunks_written': total_written,
                'validation_receipt': receipt,
            }
        time.sleep(float(poll_seconds))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--generation-key', required=True)
    parser.add_argument('--follow', action='store_true')
    parser.add_argument('--poll-seconds', type=float, default=5.0)
    parser.add_argument('--max-chunks', type=int)
    parser.add_argument(
        '--expected-manifest-sha256',
        type=lambda value: value
        if EXPECTED_MANIFEST_SHA256.fullmatch(value)
        else parser.error('--expected-manifest-sha256 must be 64 lowercase hex'),
    )
    parser.add_argument('--print-manifest-sha256', action='store_true')
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    dsn = os.environ.get('V3_SYSTEM_SEARCH_DATABASE_URL')
    if not dsn:
        print(json.dumps({'status': 'FAILED', 'error': 'database_authority_missing'}), file=sys.stderr)
        return 64
    try:
        import psycopg
        with psycopg.connect(dsn, autocommit=True) as connection:
            configure_session(connection)
            if args.print_manifest_sha256:
                generation = _generation(connection, args.generation_key)
                manifest_sha = _digest(product_manifest(generation)).hex()
                if (
                    args.expected_manifest_sha256 is not None
                    and manifest_sha != args.expected_manifest_sha256
                ):
                    raise ExpectedManifestMismatch('expected_manifest_mismatch')
                print(manifest_sha)
                return 0
            result = run(
                connection, args.generation_key,
                follow=args.follow,
                poll_seconds=args.poll_seconds,
                max_chunks=args.max_chunks,
                progress=lambda item: print(json.dumps(
                    {'status': 'PROGRESS', **item}, sort_keys=True
                ), flush=True),
                expected_manifest_sha256=args.expected_manifest_sha256,
            )
    except ExpectedManifestMismatch:
        print(
            json.dumps({'status': 'FAILED', 'error': 'expected_manifest_mismatch'}),
            file=sys.stderr,
        )
        return 65
    except Exception as exc:
        print(json.dumps(failure_receipt(exc, dsn)), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
