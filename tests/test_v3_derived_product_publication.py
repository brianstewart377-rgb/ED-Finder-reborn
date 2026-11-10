"""Migration 015's product-publication and pointer contracts on disposable PG18."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import pytest

from tests.ratings_v4_pg_fixture import canonical_database


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "sql/v3/migrations"
PRE_015 = (
    "003_ratings_v4_derived.sql",
    "004_v3_search_spatial_clusters.sql",
    "006_v3_derived_product_lifecycle.sql",
    "010_v3_system_search_body_type_counts.sql",
    "011_v3_system_archetype.sql",
)
PRE_010 = PRE_015[:3]
POST_PRODUCT_PRE_015 = PRE_015[3:]
FINDER_PRODUCTS = ("system_search", "system_archetype")
HASH = b"p" * 32
ARCHETYPES = (
    "paradise",
    "mining_hub",
    "manufacturing_hub",
    "megacomplex",
    "research_hub",
    "stronghold",
    "population_capital",
    "flexible",
)


@pytest.fixture
def database():
    with canonical_database() as fixture:
        connection = fixture[0]
        for name in (*PRE_015, "015_v3_derived_product_publication.sql"):
            connection.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
        yield fixture


def _canonical(connection):
    return connection.execute(
        "SELECT generation_id, publication_sequence "
        "FROM v3_meta.current_canonical_generation WHERE singleton"
    ).fetchone()


def _insert_generation(connection, state="BUILDING"):
    canonical, sequence = _canonical(connection)
    generation = uuid4()
    terminal = state in {"READY", "PUBLISHED", "RETIRED"}
    failed = state == "FAILED"
    manifest = json.dumps({"generation": str(generation)}, sort_keys=True)
    connection.execute(
        """
        INSERT INTO v3_meta.derived_generation (
            derived_generation_id, canonical_generation_id,
            canonical_publication_sequence, generation_key, mechanics_version,
            scorer_version, adapter_version, lifecycle_state, manifest,
            manifest_sha256, expected_systems, expected_bodies, source_receipt,
            validation_receipt, content_sha256, validated_at, published_at,
            failed_at, failure
        ) VALUES (
            %s, %s, %s, %s, 'mechanics-test', 'scorer-test', 'adapter-test',
            %s, %s::jsonb, %s, 1, 0, %s::jsonb, %s::jsonb, %s,
            CASE WHEN %s THEN transaction_timestamp() END,
            CASE WHEN %s THEN transaction_timestamp() END,
            CASE WHEN %s THEN transaction_timestamp() END,
            CASE WHEN %s THEN 'test failure' END
        )
        """,
        (
            generation,
            canonical,
            sequence,
            "product_publication_" + uuid4().hex,
            state,
            manifest,
            HASH,
            json.dumps({"status": "SEALED"}) if terminal else None,
            json.dumps({"status": "VERIFIED"}) if terminal else None,
            HASH if terminal else None,
            terminal,
            state in {"PUBLISHED", "RETIRED"},
            failed,
            failed,
        ),
    )
    return generation


def _make_ready(connection, generation):
    connection.execute(
        """
        UPDATE v3_meta.derived_generation
           SET lifecycle_state='VALIDATING', source_receipt=%s::jsonb,
               content_sha256=%s
         WHERE derived_generation_id=%s
        """,
        (json.dumps({"status": "SEALED"}), HASH, generation),
    )
    connection.execute(
        """
        UPDATE v3_meta.derived_generation
           SET lifecycle_state='READY', validation_receipt=%s::jsonb,
               validated_at=transaction_timestamp()
         WHERE derived_generation_id=%s
        """,
        (json.dumps({"status": "VERIFIED"}), generation),
    )


def _seed_rating(connection, generation, system_id=42):
    connection.execute(
        """
        INSERT INTO v3_derived.build_chunk (
            derived_generation_id, chunk_ordinal, source_projection_sha256,
            canonical_input_sha256, content_sha256, systems, canonical_bodies,
            physical_bodies, eligible_opportunities
        ) VALUES (%s, 0, %s, %s, %s, 1, 0, 0, 0)
        """,
        (generation, HASH, HASH, HASH),
    )
    connection.execute(
        """
        INSERT INTO v3_derived.system_rating_vector (
            derived_generation_id, system_id64, chunk_ordinal,
            loaded_body_count, physical_body_count, subtype_projection_sha256,
            potential, quality, quality_min, quality_max, completeness, confidence
        ) VALUES (
            %s, %s, 0, 0, 0, %s,
            ARRAY[0,0,0,0,0,0,0]::smallint[],
            ARRAY[0,0,0,0,0,0,0]::smallint[],
            ARRAY[0,0,0,0,0,0,0]::smallint[],
            ARRAY[0,0,0,0,0,0,0]::smallint[],
            ARRAY[10000,10000,10000,10000,10000,10000,10000]::smallint[],
            ARRAY[10000,10000,10000,10000,10000,10000,10000]::smallint[]
        )
        """,
        (generation, system_id, HASH),
    )


def _ready_generation(connection, *, with_rating=False):
    generation = _insert_generation(connection)
    if with_rating:
        _seed_rating(connection, generation)
    _make_ready(connection, generation)
    return generation


def _publish_generation(connection, target, current=None, sequence=0, products=()):
    canonical, canonical_sequence = _canonical(connection)
    return connection.execute(
        "SELECT v3_meta.publish_derived_generation(%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            target,
            current,
            sequence,
            canonical,
            canonical_sequence,
            "publication-test",
            "migration 015 test",
            list(products),
        ),
    ).fetchone()[0]


def _current_generation(connection, *, with_rating=False):
    generation = _ready_generation(connection, with_rating=with_rating)
    assert _publish_generation(connection, generation) == 1
    return generation


def _register_product(connection, generation, code, *, version=None):
    version = version or f"{code}-test-1"
    manifest = {
        "product_code": code,
        "product_version": version,
        "archetype_model_sha256": "a" * 64,
    }
    connection.execute(
        """
        INSERT INTO v3_meta.derived_product (
            derived_generation_id, product_code, product_version, manifest,
            manifest_sha256, expected_rows
        ) VALUES (%s,%s,%s,%s::jsonb,%s,1)
        """,
        (generation, code, version, json.dumps(manifest, sort_keys=True), HASH),
    )


def _ready_product(connection, generation, code):
    _register_product(connection, generation, code)
    connection.execute(
        """
        UPDATE v3_meta.derived_product
           SET lifecycle_state='READY', validation_receipt=%s::jsonb,
               validation_sha256=%s, validated_at=transaction_timestamp()
         WHERE derived_generation_id=%s AND product_code=%s
        """,
        (json.dumps({"status": "VERIFIED"}), HASH, generation, code),
    )


def _fail_product(connection, generation, code):
    _register_product(connection, generation, code)
    connection.execute(
        """
        UPDATE v3_meta.derived_product
           SET lifecycle_state='FAILED', failed_at=transaction_timestamp(),
               failure='deliberate test failure'
         WHERE derived_generation_id=%s AND product_code=%s
        """,
        (generation, code),
    )


def _insert_search_row(connection, generation, system_id=42):
    connection.execute(
        """
        INSERT INTO v3_derived.system_search (
            derived_generation_id, system_id64, name, x_ly, y_ly, z_ly,
            position_ly, body_count, landable_count, station_count, has_rings,
            has_biologicals, has_geologicals, has_terraformable,
            completeness, confidence
        ) VALUES (%s,%s,'Publication Test',0,0,0,cube(ARRAY[0,0,0]),0,0,0,
                  false,false,false,false,1,1)
        """,
        (generation, system_id),
    )


def _insert_archetype_row(connection, generation, system_id=42):
    values = {
        "derived_generation_id": generation,
        "system_id64": system_id,
        "archetype_version": "v3-archetype-test",
        "primary_archetype": "paradise",
        "secondary_archetype": "mining_hub",
        "best_colony_potential": 75,
        "best_tier": "A",
        "archetype_confidence_ppm": 900_000,
        "weighted_potential": 67.5,
        "completeness_ppm": 1_000_000,
    }
    for key in ARCHETYPES:
        values.update(
            {
                f"{key}_score": 75,
                f"{key}_tier": "A",
                f"{key}_confidence_ppm": 900_000,
                f"{key}_weighted_e4": 675_000,
            }
        )
    columns = tuple(values)
    connection.execute(
        sql.SQL("INSERT INTO v3_derived.system_archetype ({}) VALUES ({})").format(
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(sql.Placeholder(column) for column in columns),
        ),
        values,
    )


def _assert_pointer(connection, generation, sequence):
    assert connection.execute(
        "SELECT derived_generation_id, publication_sequence "
        "FROM v3_meta.current_derived_generation WHERE singleton"
    ).fetchone() == (generation, sequence)


# 7.5.1 -- registration states and born-unpublished rule.
def test_registration_accepts_build_states_and_current_published_only(database):
    connection = database[0]
    bases = []
    for state in ("BUILDING", "VALIDATING", "READY"):
        generation = _insert_generation(connection, state)
        _register_product(connection, generation, "product_" + state.lower())
        bases.append((state, generation))

    current = _current_generation(connection)
    _register_product(connection, current, "published_base_product")
    bases.append(("PUBLISHED", current))

    for state in ("RETIRED", "FAILED"):
        generation = _insert_generation(connection, state)
        bases.append((state, generation))
        with pytest.raises(psycopg.Error, match="cannot change while generation"):
            _register_product(connection, generation, "rejected_" + state.lower())

    for state, generation in bases:
        with pytest.raises(psycopg.Error):
            connection.execute(
                """
                INSERT INTO v3_meta.derived_product (
                    derived_generation_id,product_code,product_version,manifest,
                    manifest_sha256,expected_rows,published_at
                ) VALUES (%s,%s,'v1','{}'::jsonb,%s,1,transaction_timestamp())
                """,
                (generation, "born_published_" + state.lower(), HASH),
            )


# 7.5.2 -- data and receipts remain insert-only and BUILDING-only.
def test_product_rows_and_receipts_are_insert_only_on_published_base(database):
    connection = database[0]
    generation = _current_generation(connection, with_rating=True)
    _register_product(connection, generation, "system_search")
    _insert_search_row(connection, generation)
    connection.execute(
        """
        INSERT INTO v3_derived.search_build_chunk (
            derived_generation_id,chunk_ordinal,projection_version,
            source_projection_sha256,content_sha256,systems
        ) VALUES (%s,0,'search-test',%s,%s,1)
        """,
        (generation, HASH, HASH),
    )
    for statement in (
        "UPDATE v3_derived.system_search SET name='changed' WHERE derived_generation_id=%s",
        "DELETE FROM v3_derived.search_build_chunk WHERE derived_generation_id=%s",
    ):
        with pytest.raises(psycopg.Error, match="insert-only"):
            connection.execute(statement, (generation,))

    with pytest.raises(psycopg.Error, match="manifest identity is immutable"):
        connection.execute(
            "UPDATE v3_meta.derived_product SET manifest='{}'::jsonb "
            "WHERE derived_generation_id=%s AND product_code='system_search'",
            (generation,),
        )

    connection.execute(
        """
        UPDATE v3_meta.derived_product
           SET lifecycle_state='READY', validation_receipt=%s::jsonb,
               validation_sha256=%s, validated_at=transaction_timestamp()
         WHERE derived_generation_id=%s AND product_code='system_search'
        """,
        (json.dumps({"status": "VERIFIED"}), HASH, generation),
    )
    with pytest.raises(psycopg.Error, match="not BUILDING"):
        _insert_search_row(connection, generation, 43)
    with pytest.raises(psycopg.Error, match="not BUILDING"):
        connection.execute(
            """
            INSERT INTO v3_derived.search_build_chunk (
                derived_generation_id,chunk_ordinal,projection_version,
                source_projection_sha256,content_sha256,systems
            ) VALUES (%s,1,'search-test',%s,%s,1)
            """,
            (generation, HASH, HASH),
        )


# 7.5.3 -- product terminal transitions on PUBLISHED.
def test_ready_and_failed_transitions_work_on_published_base(database):
    connection = database[0]
    generation = _current_generation(connection)
    _ready_product(connection, generation, "ready_product")
    _fail_product(connection, generation, "failed_product")
    with pytest.raises(
        psycopg.Error, match="invalid derived product lifecycle transition"
    ):
        connection.execute(
            """
            UPDATE v3_meta.derived_product
               SET lifecycle_state='FAILED', failed_at=transaction_timestamp(), failure='late'
             WHERE derived_generation_id=%s AND product_code='ready_product'
            """,
            (generation,),
        )


# 7.5.4 -- READY is still invisible without product publication.
def test_ready_unpublished_products_are_absent_from_all_application_views(database):
    connection = database[0]
    generation = _current_generation(connection, with_rating=True)
    _register_product(connection, generation, "system_search")
    _register_product(connection, generation, "system_archetype")
    _insert_search_row(connection, generation)
    _insert_archetype_row(connection, generation)
    for code in FINDER_PRODUCTS:
        connection.execute(
            """
            UPDATE v3_meta.derived_product
               SET lifecycle_state='READY', validation_receipt=%s::jsonb,
                   validation_sha256=%s, validated_at=transaction_timestamp()
             WHERE derived_generation_id=%s AND product_code=%s
            """,
            (json.dumps({"status": "VERIFIED"}), HASH, generation, code),
        )
    assert (
        connection.execute("SELECT count(*) FROM v3_app.system_search").fetchone()[0]
        == 0
    )
    assert (
        connection.execute("SELECT count(*) FROM v3_app.system_archetype").fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM v3_app.system_archetype_summary"
        ).fetchone()[0]
        == 0
    )


# 7.5.5 -- convenience publisher rejection matrix and exact audit success.
def test_publish_derived_product_rejects_bad_inputs_and_audits_success(database):
    connection = database[0]
    current = _current_generation(connection)
    other = _insert_generation(connection, "READY")
    _ready_product(connection, other, "system_search")
    _register_product(connection, current, "building")
    _fail_product(connection, current, "failed")
    _ready_product(connection, current, "system_search")
    connection.execute(
        "ALTER TABLE v3_meta.derived_product DISABLE TRIGGER immutable_derived_product"
    )
    try:
        connection.execute(
            """
            INSERT INTO v3_meta.derived_product (
                derived_generation_id,product_code,product_version,lifecycle_state,
                manifest,manifest_sha256,expected_rows,validation_receipt,
                validation_sha256,validated_at
            ) VALUES (%s,'missing_receipt','v1','READY','{}'::jsonb,%s,1,
                      '{}'::jsonb,%s,transaction_timestamp())
            """,
            (current, HASH, HASH),
        )
    finally:
        connection.execute(
            "ALTER TABLE v3_meta.derived_product ENABLE TRIGGER immutable_derived_product"
        )

    cases = (
        (other, "system_search", "actor", "current PUBLISHED"),
        (current, "missing", "actor", "unknown derived product"),
        (current, "building", "actor", "not READY/VERIFIED"),
        (current, "failed", "actor", "not READY/VERIFIED"),
        (current, "missing_receipt", "actor", "not READY/VERIFIED"),
        (current, "system_search", "   ", "needs an actor"),
    )
    for generation, code, actor, message in cases:
        with pytest.raises(psycopg.Error, match=message):
            connection.execute(
                "SELECT v3_meta.publish_derived_product(%s,%s,%s)",
                (generation, code, actor),
            )

    with connection.transaction():
        transaction_time = connection.execute(
            "SELECT transaction_timestamp()"
        ).fetchone()[0]
        published = connection.execute(
            "SELECT v3_meta.publish_derived_product(%s,'system_search','actor')",
            (current,),
        ).fetchone()[0]
        assert published == transaction_time
    product = connection.execute(
        """
        SELECT p.published_at,a.published_at,a.product_version,a.manifest_sha256,a.actor
          FROM v3_meta.derived_product p
          JOIN v3_meta.derived_product_publication_audit a
            USING (derived_generation_id,product_code)
         WHERE p.derived_generation_id=%s AND p.product_code='system_search'
        """,
        (current,),
    ).fetchone()
    assert product == (published, published, "system_search-test-1", HASH, "actor")
    with pytest.raises(psycopg.Error, match="already published"):
        connection.execute(
            "SELECT v3_meta.publish_derived_product(%s,'system_search','actor')",
            (current,),
        )

    # Migration 015 must also fail closed if it inherits a malformed pointer
    # that names a non-PUBLISHED base.
    with canonical_database() as invalid_fixture:
        invalid = invalid_fixture[0]
        for name in PRE_015:
            invalid.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
        nonpublished = _insert_generation(invalid, "READY")
        _ready_product(invalid, nonpublished, "system_search")
        invalid.execute(
            "INSERT INTO v3_meta.current_derived_generation VALUES(true,%s,1,transaction_timestamp())",
            (nonpublished,),
        )
        invalid.execute(
            (MIGRATIONS / "015_v3_derived_product_publication.sql").read_text(
                encoding="utf-8"
            )
        )
        with pytest.raises(psycopg.Error, match="current PUBLISHED"):
            invalid.execute(
                "SELECT v3_meta.publish_derived_product(%s,'system_search','actor')",
                (nonpublished,),
            )


# 7.5.6 -- direct DML must satisfy both deferred sides and take the shared lock.
def test_direct_publication_dml_is_deferred_paired_and_serialized(database):
    connection = database[0]
    generation = _current_generation(connection)
    _ready_product(connection, generation, "system_search")

    other = _insert_generation(connection, "READY")
    _ready_product(connection, other, "system_search")
    with pytest.raises(psycopg.Error, match="current PUBLISHED"):
        connection.execute(
            "UPDATE v3_meta.derived_product SET published_at=transaction_timestamp() "
            "WHERE derived_generation_id=%s AND product_code='system_search'",
            (other,),
        )

    _register_product(connection, generation, "building")
    with pytest.raises(psycopg.Error, match="READY/VERIFIED"):
        connection.execute(
            "UPDATE v3_meta.derived_product SET published_at=transaction_timestamp() "
            "WHERE derived_generation_id=%s AND product_code='building'",
            (generation,),
        )

    with pytest.raises(psycopg.Error, match="must equal transaction_timestamp"):
        connection.execute(
            "UPDATE v3_meta.derived_product "
            "SET published_at=transaction_timestamp() + interval '1 second' "
            "WHERE derived_generation_id=%s AND product_code='system_search'",
            (generation,),
        )

    with pytest.raises(psycopg.Error, match="exact audit row"):
        with connection.transaction():
            connection.execute(
                """
                UPDATE v3_meta.derived_product SET published_at=transaction_timestamp()
                 WHERE derived_generation_id=%s AND product_code='system_search'
                """,
                (generation,),
            )

    with pytest.raises(psycopg.Error, match="does not match"):
        with connection.transaction():
            now = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
            connection.execute(
                """
                INSERT INTO v3_meta.derived_product_publication_audit
                    (derived_generation_id,product_code,product_version,
                     manifest_sha256,published_at,actor)
                VALUES (%s,'system_search','wrong-version',%s,%s,'actor')
                """,
                (generation, HASH, now),
            )

    with pytest.raises(psycopg.Error, match="exact audit row|does not match"):
        with connection.transaction():
            now = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
            connection.execute(
                "UPDATE v3_meta.derived_product SET published_at=%s "
                "WHERE derived_generation_id=%s AND product_code='system_search'",
                (now, generation),
            )
            connection.execute(
                """
                INSERT INTO v3_meta.derived_product_publication_audit
                    (derived_generation_id,product_code,product_version,
                     manifest_sha256,published_at,actor)
                VALUES (%s,'system_search','system_search-test-1',%s,
                        %s::timestamptz + interval '1 second','actor')
                """,
                (generation, HASH, now),
            )

    observer_dsn = make_conninfo(connection.info.dsn, password=connection.info.password)
    with psycopg.connect(observer_dsn, autocommit=True) as observer:
        with connection.transaction():
            now = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
            connection.execute(
                """
                UPDATE v3_meta.derived_product SET published_at=%s
                 WHERE derived_generation_id=%s AND product_code='system_search'
                """,
                (now, generation),
            )
            connection.execute(
                """
                INSERT INTO v3_meta.derived_product_publication_audit
                    (derived_generation_id,product_code,product_version,
                     manifest_sha256,published_at,actor)
                VALUES (%s,'system_search','system_search-test-1',%s,%s,'paired')
                """,
                (generation, HASH, now),
            )
            assert (
                observer.execute(
                    "SELECT pg_try_advisory_xact_lock(764003001)"
                ).fetchone()[0]
                is False
            )

    with pytest.raises(psycopg.Error, match="one-way"):
        connection.execute(
            "UPDATE v3_meta.derived_product SET published_at=transaction_timestamp() "
            "WHERE derived_generation_id=%s AND product_code='system_search'",
            (generation,),
        )

    for statement in (
        "UPDATE v3_meta.derived_product_publication_audit SET actor='x'",
        "DELETE FROM v3_meta.derived_product_publication_audit",
        "TRUNCATE v3_meta.derived_product_publication_audit",
    ):
        with pytest.raises(psycopg.Error, match="insert-only"):
            connection.execute(statement)

    with canonical_database() as invalid_fixture:
        invalid = invalid_fixture[0]
        for name in PRE_015:
            invalid.execute((MIGRATIONS / name).read_text(encoding="utf-8"))
        nonpublished = _insert_generation(invalid, "READY")
        _ready_product(invalid, nonpublished, "system_search")
        invalid.execute(
            "INSERT INTO v3_meta.current_derived_generation VALUES(true,%s,1,transaction_timestamp())",
            (nonpublished,),
        )
        invalid.execute(
            (MIGRATIONS / "015_v3_derived_product_publication.sql").read_text(
                encoding="utf-8"
            )
        )
        with pytest.raises(psycopg.Error, match="current PUBLISHED"):
            invalid.execute(
                "UPDATE v3_meta.derived_product SET published_at=transaction_timestamp() "
                "WHERE derived_generation_id=%s AND product_code='system_search'",
                (nonpublished,),
            )


# 7.5.7 -- one transaction publishes the pair and opens every application view.
def test_publishing_both_products_in_one_transaction_opens_views(database):
    connection = database[0]
    generation = _current_generation(connection, with_rating=True)
    _register_product(connection, generation, "system_search")
    _register_product(connection, generation, "system_archetype")
    _insert_search_row(connection, generation)
    _insert_archetype_row(connection, generation)
    for code in FINDER_PRODUCTS:
        connection.execute(
            """
            UPDATE v3_meta.derived_product
               SET lifecycle_state='READY', validation_receipt=%s::jsonb,
                   validation_sha256=%s, validated_at=transaction_timestamp()
             WHERE derived_generation_id=%s AND product_code=%s
            """,
            (json.dumps({"status": "VERIFIED"}), HASH, generation, code),
        )
    with connection.transaction():
        for code in FINDER_PRODUCTS:
            connection.execute(
                "SELECT v3_meta.publish_derived_product(%s,%s,'paired launch')",
                (generation, code),
            )
    assert (
        connection.execute("SELECT count(*) FROM v3_app.system_search").fetchone()[0]
        == 1
    )
    assert (
        connection.execute("SELECT count(*) FROM v3_app.system_archetype").fetchone()[0]
        == 1
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM v3_app.system_archetype_summary"
        ).fetchone()[0]
        == 1
    )


# 7.5.8 -- first launch, counterpart and unfinished-product promotion rules.
def test_generation_publisher_enforces_counterpart_and_unfinished_rules(database):
    connection = database[0]
    first = _ready_generation(connection)
    _ready_product(connection, first, "system_search")
    assert _publish_generation(connection, first) == 1
    assert (
        connection.execute(
            "SELECT published_at FROM v3_meta.derived_product "
            "WHERE derived_generation_id=%s AND product_code='system_search'",
            (first,),
        ).fetchone()[0]
        is None
    )

    refused = _ready_generation(connection)
    with pytest.raises(psycopg.Error, match="unfinished derived product"):
        _publish_generation(connection, refused, first, 1)
    _assert_pointer(connection, first, 1)

    _ready_product(connection, refused, "system_search")
    assert _publish_generation(connection, refused, first, 1, ("system_search",)) == 2
    _assert_pointer(connection, refused, 2)

    # Give the outgoing generation both published Finder products. Naming only
    # one counterpart must roll the whole flip back.
    _ready_product(connection, refused, "system_archetype")
    connection.execute(
        "SELECT v3_meta.publish_derived_product(%s,'system_archetype','launch')",
        (refused,),
    )

    # An absent counterpart and a registered-but-unpublished counterpart are
    # independently refused by the same outgoing-published-set rule.
    unregistered_counterpart = _ready_generation(connection)
    _ready_product(connection, unregistered_counterpart, "system_search")
    with pytest.raises(psycopg.Error, match="lacks an incoming published counterpart"):
        _publish_generation(
            connection,
            unregistered_counterpart,
            refused,
            2,
            ("system_search",),
        )
    _assert_pointer(connection, refused, 2)

    incoming = _ready_generation(connection)
    for code in FINDER_PRODUCTS:
        _ready_product(connection, incoming, code)
    with pytest.raises(psycopg.Error, match="lacks an incoming published counterpart"):
        _publish_generation(connection, incoming, refused, 2, ("system_search",))
    _assert_pointer(connection, refused, 2)
    assert _publish_generation(connection, incoming, refused, 2, FINDER_PRODUCTS) == 3
    _assert_pointer(connection, incoming, 3)

    # Naming a non-READY product is refused before the pointer changes.
    _register_product(connection, incoming, "unfinished")
    nonready_target = _ready_generation(connection)
    _register_product(connection, nonready_target, "unfinished")
    with pytest.raises(
        psycopg.Error, match="named derived product is not READY/VERIFIED"
    ):
        _publish_generation(connection, nonready_target, incoming, 3, ("unfinished",))
    _assert_pointer(connection, incoming, 3)

    # The outgoing BUILDING row blocks a clean replacement. Once the same row
    # is FAILED, it no longer blocks and the exact same Finder replacement can
    # commit.
    replacement = _ready_generation(connection)
    for code in FINDER_PRODUCTS:
        _ready_product(connection, replacement, code)
    with pytest.raises(psycopg.Error, match="unfinished derived product"):
        _publish_generation(connection, replacement, incoming, 3, FINDER_PRODUCTS)
    _assert_pointer(connection, incoming, 3)
    connection.execute(
        """
        UPDATE v3_meta.derived_product
           SET lifecycle_state='FAILED',failed_at=transaction_timestamp(),failure='stop'
         WHERE derived_generation_id=%s AND product_code='unfinished'
        """,
        (incoming,),
    )
    assert (
        _publish_generation(connection, replacement, incoming, 3, FINDER_PRODUCTS) == 4
    )
    _assert_pointer(connection, replacement, 4)

    # READY-unpublished is also unfinished, but is explicitly replaceable by a
    # same-code publication in the incoming transaction.
    _ready_product(connection, replacement, "extra_ready")
    final = _ready_generation(connection)
    for code in (*FINDER_PRODUCTS, "extra_ready"):
        _ready_product(connection, final, code)
    with pytest.raises(psycopg.Error, match="unfinished derived product"):
        _publish_generation(connection, final, replacement, 4, FINDER_PRODUCTS)
    _assert_pointer(connection, replacement, 4)
    assert (
        _publish_generation(
            connection, final, replacement, 4, (*FINDER_PRODUCTS, "extra_ready")
        )
        == 5
    )
    _assert_pointer(connection, final, 5)


# 7.5.9 -- the unsafe overload is gone.
def test_only_new_publish_derived_generation_signature_exists(database):
    connection = database[0]
    assert (
        connection.execute(
            "SELECT to_regprocedure("
            "'v3_meta.publish_derived_generation(uuid,uuid,bigint,uuid,bigint,text,text)')"
        ).fetchone()[0]
        is None
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='v3_meta' AND p.proname='publish_derived_generation'"
        ).fetchone()[0]
        == 1
    )
    with pytest.raises(psycopg.errors.UndefinedFunction):
        connection.execute(
            "SELECT v3_meta.publish_derived_generation("
            "%s::uuid,NULL::uuid,0,%s::uuid,1,'a','r')",
            (uuid4(), _canonical(connection)[0]),
        )


# 7.5.10 -- pointer table is guarded independently of the publisher function.
def test_pointer_table_direct_dml_contract_and_first_insert(database):
    connection = database[0]
    first = _ready_generation(connection)
    canonical, _ = _canonical(connection)
    with connection.transaction():
        now = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
        connection.execute(
            "INSERT INTO v3_meta.current_derived_generation VALUES(true,%s,1,%s)",
            (first, now),
        )
        connection.execute(
            "UPDATE v3_meta.derived_generation "
            "SET lifecycle_state='PUBLISHED',published_at=%s WHERE derived_generation_id=%s",
            (now, first),
        )
        connection.execute(
            """
            INSERT INTO v3_meta.derived_publication_audit
                (publication_sequence,from_generation_id,to_generation_id,
                 canonical_generation_id,published_at,actor,reason)
            VALUES (1,NULL,%s,%s,%s,'direct','first insert')
            """,
            (first, canonical, now),
        )
    _assert_pointer(connection, first, 1)
    with pytest.raises(psycopg.Error, match="singleton already exists"):
        connection.execute(
            "INSERT INTO v3_meta.current_derived_generation VALUES(true,%s,2,transaction_timestamp())",
            (first,),
        )

    target = _ready_generation(connection)
    nonready = _insert_generation(connection)
    with pytest.raises(psycopg.Error, match="candidate is not validated"):
        connection.execute(
            "UPDATE v3_meta.current_derived_generation "
            "SET derived_generation_id=%s,publication_sequence=2,"
            "published_at=transaction_timestamp() WHERE singleton",
            (nonready,),
        )
    with pytest.raises(psycopg.Error, match="sequence must increment by one"):
        connection.execute(
            "UPDATE v3_meta.current_derived_generation "
            "SET derived_generation_id=%s,publication_sequence=3,"
            "published_at=transaction_timestamp() WHERE singleton",
            (target,),
        )
    with pytest.raises(psycopg.Error, match="sequence must increment by one"):
        connection.execute(
            "UPDATE v3_meta.current_derived_generation "
            "SET derived_generation_id=%s,publication_sequence=1,"
            "published_at=transaction_timestamp() WHERE singleton",
            (target,),
        )
    with pytest.raises(psycopg.Error, match="target must change"):
        connection.execute(
            "UPDATE v3_meta.current_derived_generation "
            "SET publication_sequence=2,published_at=transaction_timestamp() WHERE singleton"
        )
    with pytest.raises(
        psycopg.Error, match="current derived generation is not PUBLISHED"
    ):
        with connection.transaction():
            connection.execute(
                """
                UPDATE v3_meta.current_derived_generation
                   SET derived_generation_id=%s,publication_sequence=2,
                       published_at=transaction_timestamp()
                 WHERE singleton
                """,
                (target,),
            )
    _assert_pointer(connection, first, 1)

    # Complete both lifecycle changes but omit the audit. This reaches the
    # deferred audit check rather than failing the earlier lifecycle check.
    with pytest.raises(psycopg.Error, match="requires an exact audit row"):
        with connection.transaction():
            now = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
            connection.execute(
                "UPDATE v3_meta.current_derived_generation "
                "SET derived_generation_id=%s,publication_sequence=2,published_at=%s "
                "WHERE singleton",
                (target, now),
            )
            connection.execute(
                "UPDATE v3_meta.derived_generation SET lifecycle_state='RETIRED' "
                "WHERE derived_generation_id=%s",
                (first,),
            )
            connection.execute(
                "UPDATE v3_meta.derived_generation "
                "SET lifecycle_state='PUBLISHED',published_at=%s "
                "WHERE derived_generation_id=%s",
                (now, target),
            )
    _assert_pointer(connection, first, 1)

    # A complete hand-written transition is accepted: pointer, both lifecycle
    # changes and the exact generation audit all commit together.
    with connection.transaction():
        now = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
        connection.execute(
            """
            UPDATE v3_meta.current_derived_generation
               SET derived_generation_id=%s,publication_sequence=2,published_at=%s
             WHERE singleton
            """,
            (target, now),
        )
        connection.execute(
            "UPDATE v3_meta.derived_generation SET lifecycle_state='RETIRED' "
            "WHERE derived_generation_id=%s",
            (first,),
        )
        connection.execute(
            "UPDATE v3_meta.derived_generation "
            "SET lifecycle_state='PUBLISHED',published_at=%s "
            "WHERE derived_generation_id=%s",
            (now, target),
        )
        connection.execute(
            """
            INSERT INTO v3_meta.derived_publication_audit
                (publication_sequence,from_generation_id,to_generation_id,
                 canonical_generation_id,published_at,actor,reason)
            VALUES (2,%s,%s,%s,%s,'direct','valid update')
            """,
            (first, target, canonical, now),
        )
    _assert_pointer(connection, target, 2)

    # The deferred table contract also evaluates the Finder counterpart rule,
    # even when the publisher function is bypassed.
    _ready_product(connection, target, "system_search")
    connection.execute(
        "SELECT v3_meta.publish_derived_product(%s,'system_search','direct test')",
        (target,),
    )
    counterpart_missing = _ready_generation(connection)
    with pytest.raises(psycopg.Error, match="lacks an incoming published counterpart"):
        with connection.transaction():
            now = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
            connection.execute(
                "UPDATE v3_meta.current_derived_generation "
                "SET derived_generation_id=%s,publication_sequence=3,published_at=%s "
                "WHERE singleton",
                (counterpart_missing, now),
            )
            connection.execute(
                "UPDATE v3_meta.derived_generation SET lifecycle_state='RETIRED' "
                "WHERE derived_generation_id=%s",
                (target,),
            )
            connection.execute(
                "UPDATE v3_meta.derived_generation "
                "SET lifecycle_state='PUBLISHED',published_at=%s "
                "WHERE derived_generation_id=%s",
                (now, counterpart_missing),
            )
            connection.execute(
                """
                INSERT INTO v3_meta.derived_publication_audit
                    (publication_sequence,from_generation_id,to_generation_id,
                     canonical_generation_id,published_at,actor,reason)
                VALUES (3,%s,%s,%s,%s,'direct','bad counterpart')
                """,
                (target, counterpart_missing, canonical, now),
            )
    _assert_pointer(connection, target, 2)

    # A concurrent product publication holds the same advisory transaction
    # lock. The direct pointer writer times out, and the product audit therefore
    # cannot commit against a generation that ceased to be current.
    _ready_product(connection, target, "system_archetype")
    observer_dsn = make_conninfo(connection.info.dsn, password=connection.info.password)
    with psycopg.connect(observer_dsn, autocommit=True) as observer:
        with connection.transaction():
            connection.execute(
                "SELECT v3_meta.publish_derived_product(%s,'system_archetype','race')",
                (target,),
            )
            observer.execute("SET lock_timeout='100ms'")
            with pytest.raises(psycopg.Error, match="lock timeout"):
                observer.execute(
                    "UPDATE v3_meta.current_derived_generation "
                    "SET derived_generation_id=%s,publication_sequence=3,"
                    "published_at=transaction_timestamp() WHERE singleton",
                    (counterpart_missing,),
                )
    assert (
        connection.execute(
            """
        SELECT count(*)
          FROM v3_meta.derived_product_publication_audit a
          JOIN v3_meta.current_derived_generation c USING (derived_generation_id)
         WHERE a.product_code='system_archetype'
        """
        ).fetchone()[0]
        == 1
    )
    _assert_pointer(connection, target, 2)

    _ready_product(connection, target, "extra_ready")
    unfinished_target = _ready_generation(connection)
    for code in (*FINDER_PRODUCTS, "extra_ready"):
        _ready_product(connection, unfinished_target, code)
    with pytest.raises(psycopg.Error, match="unfinished derived product"):
        with connection.transaction():
            now = connection.execute("SELECT transaction_timestamp()").fetchone()[0]
            connection.execute(
                "UPDATE v3_meta.current_derived_generation "
                "SET derived_generation_id=%s,publication_sequence=3,published_at=%s "
                "WHERE singleton",
                (unfinished_target, now),
            )
            connection.execute(
                "UPDATE v3_meta.derived_generation SET lifecycle_state='RETIRED' "
                "WHERE derived_generation_id=%s",
                (target,),
            )
            connection.execute(
                "UPDATE v3_meta.derived_generation "
                "SET lifecycle_state='PUBLISHED',published_at=%s "
                "WHERE derived_generation_id=%s",
                (now, unfinished_target),
            )
            connection.execute(
                """
                INSERT INTO v3_meta.derived_publication_audit
                    (publication_sequence,from_generation_id,to_generation_id,
                     canonical_generation_id,published_at,actor,reason)
                VALUES (3,%s,%s,%s,%s,'direct','unfinished outgoing')
                """,
                (target, unfinished_target, canonical, now),
            )
            for code in FINDER_PRODUCTS:
                connection.execute(
                    "SELECT v3_meta.publish_derived_product(%s,%s,'direct')",
                    (unfinished_target, code),
                )
    _assert_pointer(connection, target, 2)

    for statement in (
        "DELETE FROM v3_meta.current_derived_generation",
        "TRUNCATE v3_meta.current_derived_generation",
    ):
        with pytest.raises(psycopg.Error, match="cannot be removed"):
            connection.execute(statement)


# 7.5.11 -- the inherited pre-010 opt1 Search remains unpublished and hidden.
def test_inherited_ready_opt1_product_stays_unpublished_and_invisible():
    with canonical_database() as fixture:
        connection = fixture[0]
        for name in PRE_010:
            connection.execute((MIGRATIONS / name).read_text(encoding="utf-8"))

        # Materialize the opt1 row before 010 adds the body-count columns. Its
        # new columns are backfilled by 010's defaults, as on the real lineage.
        opt1 = _ready_generation(connection, with_rating=True)
        _register_product(connection, opt1, "system_search", version="pre-010-opt1")
        _insert_search_row(connection, opt1)
        connection.execute(
            """
            UPDATE v3_meta.derived_product
               SET lifecycle_state='READY',validation_receipt=%s::jsonb,
                   validation_sha256=%s,validated_at=transaction_timestamp()
             WHERE derived_generation_id=%s AND product_code='system_search'
            """,
            (json.dumps({"status": "VERIFIED"}), HASH, opt1),
        )
        for name in POST_PRODUCT_PRE_015:
            connection.execute((MIGRATIONS / name).read_text(encoding="utf-8"))

        canonical, _ = _canonical(connection)
        current = _ready_generation(connection)
        with connection.transaction():
            published_at = connection.execute(
                "SELECT transaction_timestamp()"
            ).fetchone()[0]
            connection.execute(
                "UPDATE v3_meta.derived_generation "
                "SET lifecycle_state='PUBLISHED',published_at=%s "
                "WHERE derived_generation_id=%s",
                (published_at, opt1),
            )
            connection.execute(
                "INSERT INTO v3_meta.current_derived_generation VALUES(true,%s,1,%s)",
                (opt1, published_at),
            )
            connection.execute(
                "INSERT INTO v3_meta.derived_publication_audit "
                "VALUES(1,NULL,%s,%s,%s,'test','opt1')",
                (opt1, canonical, published_at),
            )
            connection.execute(
                "UPDATE v3_meta.derived_generation SET lifecycle_state='RETIRED' "
                "WHERE derived_generation_id=%s",
                (opt1,),
            )
            connection.execute(
                "UPDATE v3_meta.derived_generation "
                "SET lifecycle_state='PUBLISHED',published_at=%s "
                "WHERE derived_generation_id=%s",
                (published_at, current),
            )
            connection.execute(
                "UPDATE v3_meta.current_derived_generation "
                "SET derived_generation_id=%s,publication_sequence=2,published_at=%s "
                "WHERE singleton",
                (current, published_at),
            )
            connection.execute(
                "INSERT INTO v3_meta.derived_publication_audit "
                "VALUES(2,%s,%s,%s,%s,'test','current')",
                (opt1, current, canonical, published_at),
            )

        connection.execute(
            (MIGRATIONS / "015_v3_derived_product_publication.sql").read_text(
                encoding="utf-8"
            )
        )
        assert (
            connection.execute(
                "SELECT published_at FROM v3_meta.derived_product "
                "WHERE derived_generation_id=%s AND product_code='system_search'",
                (opt1,),
            ).fetchone()[0]
            is None
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM v3_app.system_search WHERE derived_generation_id=%s",
                (opt1,),
            ).fetchone()[0]
            == 0
        )
