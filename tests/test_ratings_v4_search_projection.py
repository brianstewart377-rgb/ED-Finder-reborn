"""Exact old/new SELECT parity, in a disposable PostgreSQL database only."""
from pathlib import Path
import sys
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.v3_system_search import projection_query_sql  # noqa: E402
from tests.ratings_v4_pg_fixture import canonical_database  # noqa: E402

GENERATION = UUID('00000000-0000-0000-0000-000000000001')
LEGACY = ROOT / 'scripts/operator/v3_system_search_legacy.sql'


@pytest.fixture
def projection_database():
    # The helper enforces localhost + ratings_v4_validation and creates/drops
    # only its own random test database. These edge-case relations are separate
    # from the immutable canonical fixture and do not weaken its guards.
    with canonical_database() as (connection, _, _, _):
        connection.execute("""
            CREATE EXTENSION IF NOT EXISTS cube;
            CREATE SCHEMA search_plan_fixture;
            CREATE TABLE search_plan_fixture.system_rating_vector(
                derived_generation_id uuid, system_id64 bigint, chunk_ordinal int,
                loaded_body_count int, completeness int[], confidence int[]);
            CREATE TABLE search_plan_fixture.systems(
                id64 bigint PRIMARY KEY, name text, x_ly float8, y_ly float8,
                z_ly float8, galaxy_region_id int, source_updated_at timestamptz);
            CREATE TABLE search_plan_fixture.bodies(
                body_pk bigint PRIMARY KEY, system_id64 bigint, lifecycle_state text,
                is_landable bool, terraforming_state_id int,
                atmosphere_classification_id int);
            CREATE INDEX ON search_plan_fixture.bodies(system_id64,body_pk);
            CREATE TABLE search_plan_fixture.body_signal_current(
                body_pk bigint, signal_type_id int, signal_count int,
                PRIMARY KEY(body_pk,signal_type_id));
            CREATE TABLE search_plan_fixture.signal_type(
                signal_type_id int PRIMARY KEY, public_code text);
            CREATE TABLE search_plan_fixture.terraforming_state(
                terraforming_state_id int PRIMARY KEY, public_code text);
            CREATE TABLE search_plan_fixture.atmosphere_classification(
                atmosphere_classification_id int PRIMARY KEY, public_code text);
            CREATE TABLE search_plan_fixture.galaxy_region(
                galaxy_region_id int PRIMARY KEY, display_name text);
            CREATE TABLE search_plan_fixture.rings(
                system_id64 bigint, lifecycle_state text, kind text);
            CREATE TABLE search_plan_fixture.stations(
                system_id64 bigint, lifecycle_state text);
            CREATE TABLE search_plan_fixture.body_mechanics(
                derived_generation_id uuid, system_id64 bigint, body_pk bigint,
                body_class text, is_main_star bool,
                spectral_class text, terraformable bool,
                PRIMARY KEY(derived_generation_id,system_id64,body_pk));
            INSERT INTO search_plan_fixture.systems
                SELECT id,'system-'||id,id,id+1,id+2,NULL,'2026-09-12T00:00:00Z'
                FROM generate_series(1,9) id;
            INSERT INTO search_plan_fixture.system_rating_vector
                SELECT '00000000-0000-0000-0000-000000000001',
                       id,(id-1)/5,2,ARRAY[10000,5000],ARRAY[9000,3000]
                  FROM generate_series(1,9) id;
            INSERT INTO search_plan_fixture.system_rating_vector
                SELECT '00000000-0000-0000-0000-000000000002',
                       9,0,99,ARRAY[0],ARRAY[0];
            INSERT INTO search_plan_fixture.signal_type VALUES
                (1,'saa_signaltype_biological'),(2,'saa_signaltype_geological'),
                (3,'other_signal');
            INSERT INTO search_plan_fixture.terraforming_state VALUES
                (1,'terraformable'),(2,'not_terraformable');
            INSERT INTO search_plan_fixture.atmosphere_classification VALUES
                (1,'no_atmosphere'),(2,'thin_carbon_dioxide');
            INSERT INTO search_plan_fixture.bodies VALUES
                (10,1,'ACTIVE',true,1,1),(11,1,'ACTIVE',false,2,NULL),
                (20,2,'ACTIVE',NULL,NULL,NULL),
                (30,3,'RETIRED',true,1,NULL),
                (50,5,'ACTIVE',false,2,NULL),
                (60,6,'ACTIVE',true,NULL,NULL);
            INSERT INTO search_plan_fixture.body_signal_current VALUES
                (10,1,2),(10,2,3),(11,1,1),
                (20,1,0),(20,2,0),(30,1,5),(30,2,5),
                (50,3,10);
            INSERT INTO search_plan_fixture.rings VALUES
                (1,'ACTIVE','RING'),(1,'ACTIVE','RING'),(2,'ACTIVE','BELT'),
                (3,'RETIRED','RING');
            INSERT INTO search_plan_fixture.stations VALUES
                (1,'ACTIVE'),(1,'ACTIVE'),(1,'RETIRED'),(3,'RETIRED');
            INSERT INTO search_plan_fixture.body_mechanics VALUES
                ('00000000-0000-0000-0000-000000000001',1,11,'LATE',true,NULL,NULL),
                ('00000000-0000-0000-0000-000000000001',1,10,'FIRST',true,NULL,NULL),
                ('00000000-0000-0000-0000-000000000001',1,12,'Earthlike world',false,NULL,false),
                ('00000000-0000-0000-0000-000000000001',1,13,'Water world',false,NULL,false),
                ('00000000-0000-0000-0000-000000000001',1,14,'Rocky body',false,NULL,true),
                ('00000000-0000-0000-0000-000000000001',1,15,'Rocky ice body',false,NULL,false),
                ('00000000-0000-0000-0000-000000000001',2,21,'High metal content world',false,NULL,false),
                ('00000000-0000-0000-0000-000000000001',2,22,'Metal rich body',false,NULL,false),
                ('00000000-0000-0000-0000-000000000001',2,23,'Black Hole',false,NULL,false),
                ('00000000-0000-0000-0000-000000000001',3,31,'Ammonia world',false,NULL,false),
                ('00000000-0000-0000-0000-000000000001',3,32,'Gas Giant with Ammonia-based Life',false,NULL,false),
                ('00000000-0000-0000-0000-000000000001',5,51,NULL,false,'N',false),
                ('00000000-0000-0000-0000-000000000001',5,52,'B9',false,'B9',false),
                ('00000000-0000-0000-0000-000000000001',5,53,'White Dwarf',false,'DA',false),
                ('00000000-0000-0000-0000-000000000001',6,60,'NOT_MAIN',false,NULL,NULL),
                ('00000000-0000-0000-0000-000000000001',6,61,'UNKNOWN',NULL,NULL,NULL),
                ('00000000-0000-0000-0000-000000000001',6,63,'Icy body',false,NULL,false),
                ('00000000-0000-0000-0000-000000000002',8,80,'OTHER_GEN',true,NULL,NULL),
                ('00000000-0000-0000-0000-000000000001',9,90,NULL,true,NULL,NULL),
                ('00000000-0000-0000-0000-000000000001',9,91,'LATE',true,NULL,NULL);
        """)
        yield connection


def _query(source):
    from psycopg import sql
    return sql.SQL(
        source.replace('v3_derived.', 'search_plan_fixture.')
              .replace('v3_vocab.', 'search_plan_fixture.')
    ).format(schema=sql.Identifier('search_plan_fixture'))


def test_projection_laterals_correlate_directly_with_the_final_target():
    source = projection_query_sql()
    assert source.count('JOIN LATERAL') == 2
    assert 'signal_summary AS' not in source
    assert 'main_star AS' not in source
    assert 'WHERE b.system_id64=t.system_id64' in source
    assert 'AND bm.system_id64=t.system_id64' in source


def test_projection_matches_legacy_for_sparse_and_adversarial_rows(projection_database):
    connection = projection_database
    rows = []
    for ordinal in (0, 1, 99):
        params = {'generation_id': GENERATION, 'chunk_ordinal': ordinal}
        old = connection.execute(_query(LEGACY.read_text()), params).fetchall()
        new = connection.execute(_query(projection_query_sql()), params).fetchall()
        assert new == old
        assert len(new) == {0: 5, 1: 4, 99: 0}[ordinal]
        rows.extend(new)
    assert [row[1] for row in rows] == list(range(1, 10))
    first = rows[0]
    assert first[9:17] == ('FIRST', 2, 1, 2, True, True, True, True)
    assert first[18:20] == (0.5, 0.3)
    # elw,ww,ammonia,terraformable,gas_giant,hmc,metal_rich,rocky,rocky_ice,icy,
    # black_hole,neutron,white_dwarf,other_star,ring_count,walkable_count,
    # bio_signal_total,geo_signal_total
    assert first[20:38] == (1, 1, 0, 1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 2, 1, 3, 3)
    # Zero counts, retired bodies, no bodies, and unrelated signals are false.
    for row in rows[1:]:
        assert row[14:17] == (False, False, False)
        assert row[9] is None
    assert rows[3][11:17] == (0, 0, False, False, False, False)


def test_profile_equivalence_sql_executes_with_duplicate_output_names(projection_database):
    from psycopg import sql
    connection = projection_database
    query = sql.SQL("""
        WITH baseline AS MATERIALIZED ({}), candidate AS MATERIALIZED ({}),
        differences AS (
            (SELECT * FROM baseline EXCEPT ALL SELECT * FROM candidate)
            UNION ALL
            (SELECT * FROM candidate EXCEPT ALL SELECT * FROM baseline)
        )
        SELECT (SELECT count(*) FROM baseline),
               (SELECT count(*) FROM candidate),
               (SELECT count(*) FROM differences)
    """).format(_query(LEGACY.read_text()), _query(projection_query_sql()))
    assert connection.execute(query, {
        'generation_id': GENERATION, 'chunk_ordinal': 0,
    }).fetchone() == (5, 5, 0)


def test_projection_prepared_generic_plan_preserves_results(projection_database):
    from psycopg import sql
    connection = projection_database
    prepared = _query(projection_query_sql()
                      .replace('%(generation_id)s', '$1')
                      .replace('%(chunk_ordinal)s', '$2'))
    expected = connection.execute(_query(LEGACY.read_text()), {
        'generation_id': GENERATION, 'chunk_ordinal': 0,
    }).fetchall()
    with connection.transaction():
        connection.execute('SET TRANSACTION READ ONLY')
        connection.execute('SET LOCAL plan_cache_mode=force_generic_plan')
        connection.execute('SET LOCAL jit=off')
        connection.execute(sql.SQL('PREPARE search_candidate(uuid,bigint) AS ') + prepared)
        actual = connection.execute(
            sql.SQL('EXECUTE search_candidate({},0)').format(sql.Literal(GENERATION))
        ).fetchall()
        assert actual == expected
        connection.execute('DEALLOCATE search_candidate')
