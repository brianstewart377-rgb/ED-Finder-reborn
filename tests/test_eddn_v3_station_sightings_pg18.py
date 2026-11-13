"""PostgreSQL 18 tests for the v3_observe station-sighting migration and writer.

Copied from the old ed-finder repo, where the migration was still a proposal
(017); here it is migration 030, its SQL unchanged.

Applies sql/v3/migrations/001_v3_baseline.sql and
sql/v3/migrations/030_v3_observe_station_sightings.sql into a fresh random
database on the local disposable server named by
RATINGS_V4_VALIDATION_DATABASE_URL (the Finder derived-data CI lane discovers
this module by that name), then drops it.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import zlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from tests.helpers import db_isolation

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'apps' / 'eddn_v3' / 'src'
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

from edfinder_observe.config import WorkerConfig  # noqa: E402
from edfinder_observe.metrics import Metrics  # noqa: E402
from edfinder_observe.sightings import (  # noqa: E402
    OBSERVER_ROLE,
    SeenWatermarks,
    WriteResult,
    build_sighting,
    configure_observer_session,
    record_sighting,
    record_sightings,
)
from edfinder_observe.worker import (  # noqa: E402
    FlushFailureThresholdExceeded,
    PostgresWriter,
    Worker,
)

pytestmark = pytest.mark.db

BASELINE = ROOT / 'sql/v3/migrations/001_v3_baseline.sql'
PROPOSAL = ROOT / 'sql/v3/migrations/030_v3_observe_station_sightings.sql'
T0 = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
UPLOADER = 'uploader-9d1e-must-not-be-stored'


def _admin_dsn() -> str:
    dsn = os.environ.get('RATINGS_V4_VALIDATION_DATABASE_URL')
    if not dsn:
        db_isolation.database_unavailable(
            'isolated PostgreSQL validation URL not set (RATINGS_V4_VALIDATION_DATABASE_URL)')
    db_isolation.import_db_module('psycopg')
    from psycopg.conninfo import conninfo_to_dict

    settings = conninfo_to_dict(dsn)
    if (settings.get('host') not in {'127.0.0.1', 'localhost'}
            or settings.get('dbname') != 'ratings_v4_validation'):
        raise ValueError('station sighting tests require the local ratings_v4_validation service')
    return dsn


@contextmanager
def scratch_database(*, apply_proposal: bool = True):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    admin_dsn = _admin_dsn()
    name = 'obs_test_' + uuid4().hex
    try:
        admin = psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as exc:
        db_isolation.database_unavailable(f'disposable PostgreSQL unreachable: {type(exc).__name__}')
    with admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            dsn = make_conninfo(admin_dsn, dbname=name)
            if apply_proposal:
                with psycopg.connect(dsn, autocommit=True) as conn:
                    conn.execute(BASELINE.read_text(encoding='utf-8'))
                    conn.execute(PROPOSAL.read_text(encoding='utf-8'))
            yield dsn
        finally:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


@pytest.fixture(scope='module')
def dsn():
    with scratch_database() as value:
        yield value


@pytest.fixture
def observer(dsn):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        configure_observer_session(conn)
        yield conn


@pytest.fixture
def owner(dsn):
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        yield conn


_market = iter(range(10_000_000, 20_000_000, 10))


def next_market() -> int:
    return next(_market)


def sighting(market_id, *, source='eddn', ts=T0, economies=(('Industrial', 0.8), ('Refinery', 0.2)),
             services=('dock', 'commodities'), name='Test Port', **extra):
    return build_sighting(
        source=source, market_id=market_id, system_id64=10477373803, station_name=name,
        station_type='Coriolis', economies=list(economies), services=list(services),
        landing_pads={'Small': 4, 'Medium': 8, 'Large': 2}, source_record_ts=ts,
        observed_at=ts + timedelta(seconds=2), system_name='Sol',
        gateway_ts=ts + timedelta(seconds=1) if source == 'eddn' else None, **extra)


def rows(conn, market_id, source=None):
    query = ('SELECT source::text, station_name, economies, services, state_sha256, '
             'source_record_ts FROM v3_observe.station_sighting WHERE market_id = %s')
    params: list = [market_id]
    if source:
        query += ' AND source = %s::v3_observe.sighting_source'
        params.append(source)
    return conn.execute(query + ' ORDER BY sighting_id', params).fetchall()


# -- schema --------------------------------------------------------------


def test_proposal_is_unregistered_self_transactional_and_never_touches_canonical_truth():
    text = PROPOSAL.read_text(encoding='utf-8')
    manifest = (ROOT / 'sql/v3/migration-manifest.txt').read_text(encoding='utf-8')
    # The old, governed list ends at 023; migrations since are applied by hand.
    assert 'v3_observe' not in manifest
    assert text.lstrip().startswith('--') and '\nBEGIN;\n' in text
    assert text.rstrip().endswith('COMMIT;')
    assert "SET LOCAL lock_timeout = '5s'" in text
    code = '\n'.join(line.split('--', 1)[0] for line in text.splitlines())
    for forbidden in ('v3_gen_', 'v3_derived.', 'v3_spatial.', 'v3_app.', 'v3_identity.',
                      'v3_private.', 'DROP ', 'TRUNCATE v3_'):
        assert forbidden not in code, forbidden
    assert 'uploader_id' not in code.lower() and 'uploaderid' not in code.lower()
    assert b'\r\n' not in PROPOSAL.read_bytes()


def test_role_registry_and_grants(owner):
    role = owner.execute(
        'SELECT rolcanlogin, rolinherit, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls '
        'FROM pg_roles WHERE rolname = %s', (OBSERVER_ROLE,)).fetchone()
    assert role == (False, False, False, False, False, False)
    bindings = owner.execute('''
        SELECT b.source::text, s.source_code, s.authority_class, p.rights_class,
               p.distribution_allowed
        FROM v3_observe.sighting_source_binding b
        JOIN v3_source.source s USING (source_id)
        JOIN v3_source.source_rights_policy p USING (rights_policy_id)
        ORDER BY 1''').fetchall()
    assert bindings == [
        ('eddn', 'eddn.journal.docked', 'SECONDARY_AGGREGATED', 'INTERNAL_USE', False),
        ('spansh', 'spansh.galaxy-stations', 'SECONDARY_AGGREGATED', 'INTERNAL_USE', False),
    ]
    privileges = {
        (table, privilege): owner.execute(
            'SELECT has_table_privilege(%s, %s, %s)', (OBSERVER_ROLE, table, privilege)
        ).fetchone()[0]
        for table in ('v3_observe.station_sighting', 'v3_observe.station_current',
                      'v3_observe.sighting_source_binding', 'v3_source.source')
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE')
    }
    granted = {key for key, value in privileges.items() if value}
    assert granted == {
        ('v3_observe.station_sighting', 'SELECT'), ('v3_observe.station_sighting', 'INSERT'),
        ('v3_observe.station_current', 'SELECT'), ('v3_observe.sighting_source_binding', 'SELECT'),
    }


def test_observer_needs_no_sequence_grant_for_the_identity_column(owner, observer):
    # Identity columns draw from their implicit sequence without a separate
    # sequence privilege, so the role is deliberately granted none.
    assert owner.execute(
        "SELECT has_sequence_privilege(%s, 'v3_observe.station_sighting_sighting_id_seq', 'USAGE')",
        (OBSERVER_ROLE,)).fetchone()[0] is False
    assert observer.execute('SELECT current_user').fetchone()[0] == OBSERVER_ROLE
    assert record_sighting(observer, sighting(next_market())) == WriteResult(1, 0, 0)


def test_proposal_refuses_to_adopt_an_observer_role_with_an_unexpected_grantee(dsn, owner):
    import psycopg

    rogue = 'obs_rogue_' + uuid4().hex[:12]
    owner.execute(f'CREATE ROLE {rogue} NOLOGIN')
    try:
        owner.execute(f'GRANT {OBSERVER_ROLE} TO {rogue}')
        with scratch_database(apply_proposal=False) as fresh:
            with psycopg.connect(fresh, autocommit=True) as conn:
                conn.execute(BASELINE.read_text(encoding='utf-8'))
                with pytest.raises(psycopg.errors.RaiseException, match='incoming membership'):
                    conn.execute(PROPOSAL.read_text(encoding='utf-8'))
                conn.execute('ROLLBACK')
                assert conn.execute(
                    "SELECT to_regnamespace('v3_observe') IS NULL").fetchone()[0] is True
    finally:
        owner.execute(f'REVOKE {OBSERVER_ROLE} FROM {rogue}')
        owner.execute(f'DROP ROLE {rogue}')


def test_identity_sequences_stay_ahead_of_seeded_ids(owner):
    owner.execute("""INSERT INTO v3_source.source (source_code, display_name, authority_class)
                     VALUES ('test.after-017', 'after', 'REFERENCE_CURATED')""")
    assert owner.execute("SELECT source_id FROM v3_source.source WHERE source_code = 'test.after-017'"
                         ).fetchone()[0] > 2


def test_no_column_can_hold_an_uploader_identifier(owner):
    columns = [r[0] for r in owner.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'v3_observe'")]
    assert columns
    assert not [c for c in columns if 'uploader' in c.lower()]


def test_check_constraints_bound_the_payload(owner):
    import psycopg

    base = {
        'source': 'eddn', 'market_id': 1, 'system_id64': 2, 'station_name': 'S',
        'economies': '[]', 'services': [], 'landing_pads': None,
        'state_sha256': b'\x00' * 32, 'ts': T0,
    }
    statement = '''INSERT INTO v3_observe.station_sighting
        (source, market_id, system_id64, station_name, economies, services, landing_pads,
         state_sha256, source_record_ts, observed_at)
        VALUES (%(source)s::v3_observe.sighting_source, %(market_id)s, %(system_id64)s,
                %(station_name)s, %(economies)s::jsonb, %(services)s::text[],
                %(landing_pads)s::jsonb, %(state_sha256)s, %(ts)s, %(ts)s)'''
    bad = [
        {'market_id': 0},
        {'system_id64': -1},
        {'station_name': 'x' * 129},
        {'economies': json.dumps([{'name': f'E{i}', 'proportion': 0.1} for i in range(9)])},
        {'economies': json.dumps([{'name': 'A', 'proportion': 1.5}])},
        {'economies': json.dumps([{'name': 'A', 'proportion': 0.5, 'extra': 1}])},
        {'economies': '{"name": "A"}'},
        {'services': [f's{i}' for i in range(129)]},
        {'services': ['x' * 129]},
        {'services': ['']},
        {'landing_pads': '{"huge": 1}'},
        {'landing_pads': '{"small": 256}'},
        {'landing_pads': '{"small": 1.5}'},
        {'landing_pads': '{"small": 1, "medium": 1}'},
        {'landing_pads': '{}'},
        {'state_sha256': b'\x00' * 31},
        {'ts': datetime(2010, 1, 1, tzinfo=UTC)},
    ]
    future = '''INSERT INTO v3_observe.station_sighting
        (source, market_id, system_id64, station_name, economies, services,
         state_sha256, source_record_ts, gateway_ts, observed_at)
        VALUES ('eddn', %s, 2, 'S', '[]', '{}', %s, %s, %s, %s)'''
    now = datetime.now(UTC)
    for record_ts, gateway_ts, observed_at in (
        (datetime(9999, 1, 1, tzinfo=UTC), None, now),          # far-future source time
        (now + timedelta(hours=2), None, now),
        (now, now + timedelta(hours=2), now),                   # future gateway time
        (now + timedelta(days=2), None, now + timedelta(days=2)),  # observed in the future
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute(future, (next_market(), b'\x00' * 32, record_ts, gateway_ts, observed_at))
    for override in bad:
        with pytest.raises(psycopg.errors.CheckViolation):
            owner.execute(statement, {**base, **override})
    owner.execute(statement, {**base, 'market_id': next_market(),
                              'economies': json.dumps([{'name': 'A', 'proportion': 1}]),
                              'landing_pads': '{"small": 3, "medium": 1, "large": 0}'})


def test_history_is_append_only_even_for_the_owner(owner):
    import psycopg

    market = next_market()
    owner.execute('''INSERT INTO v3_observe.station_sighting
        (source, market_id, system_id64, station_name, economies, services, state_sha256,
         source_record_ts, observed_at)
        VALUES ('eddn', %s, 2, 'S', '[]', '{}', %s, %s, %s)''', (market, b'\x01' * 32, T0, T0))
    with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
        owner.execute('UPDATE v3_observe.station_sighting SET station_name = %s WHERE market_id = %s',
                      ('changed', market))
    with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
        owner.execute('DELETE FROM v3_observe.station_sighting WHERE market_id = %s', (market,))
    with pytest.raises(psycopg.errors.RaiseException, match='append-only'):
        owner.execute('TRUNCATE v3_observe.station_sighting')


def test_current_state_lookup_uses_the_index(owner):
    owner.execute('SET enable_seqscan = off')
    plan = '\n'.join(r[0] for r in owner.execute(
        "EXPLAIN SELECT state_sha256 FROM v3_observe.station_current "
        "WHERE source = 'eddn' AND market_id = ANY(ARRAY[1,2,3]::bigint[])"))
    owner.execute('RESET enable_seqscan')
    assert 'station_sighting_current_idx' in plan


# -- change-only writer --------------------------------------------------


def test_same_state_twice_writes_one_row(observer):
    market = next_market()
    assert record_sighting(observer, sighting(market)) == WriteResult(1, 0, 0)
    assert record_sighting(observer, sighting(market, ts=T0 + timedelta(hours=1),
                                              distance_ls=12.0)) == WriteResult(0, 1, 0)
    assert len(rows(observer, market)) == 1


def test_each_state_change_writes_a_row_including_a_return_to_an_earlier_state(observer):
    market = next_market()
    a = sighting(market)
    b = sighting(market, ts=T0 + timedelta(hours=1),
                 economies=(('Industrial', 0.5), ('Refinery', 0.5)), services=('dock',))
    a_again = sighting(market, ts=T0 + timedelta(hours=2))
    assert record_sightings(observer, [a]) == WriteResult(1, 0, 0)
    assert record_sightings(observer, [b]) == WriteResult(1, 0, 0)
    assert record_sightings(observer, [a_again]) == WriteResult(1, 0, 0)
    stored = rows(observer, market)
    assert [r[3] for r in stored] == [['commodities', 'dock'], ['dock'], ['commodities', 'dock']]
    assert stored[1][2] == [{'name': 'Industrial', 'proportion': 0.5},
                            {'name': 'Refinery', 'proportion': 0.5}]
    assert bytes(stored[0][4]) == bytes(stored[2][4]) != bytes(stored[1][4])


def test_older_records_are_stale_not_changes(observer):
    market = next_market()
    record_sighting(observer, sighting(market, ts=T0 + timedelta(hours=5)))
    late = sighting(market, ts=T0, name='Renamed Long Ago')
    assert record_sighting(observer, late) == WriteResult(0, 0, 1)
    assert len(rows(observer, market)) == 1


def test_reobserved_state_blocks_a_delayed_older_change(observer):
    market = next_market()
    seen = SeenWatermarks(1000)
    a_t1 = sighting(market, ts=T0)
    a_t3 = sighting(market, ts=T0 + timedelta(hours=3))
    b_t2 = sighting(market, ts=T0 + timedelta(hours=2), services=('dock',))
    assert record_sighting(observer, a_t1, seen=seen) == WriteResult(1, 0, 0)
    assert record_sighting(observer, a_t3, seen=seen) == WriteResult(0, 1, 0)
    assert seen.get('eddn', market) == T0 + timedelta(hours=3)
    assert record_sighting(observer, b_t2, seen=seen) == WriteResult(0, 0, 1)
    current = observer.execute(
        "SELECT services FROM v3_observe.station_current WHERE source = 'eddn' AND market_id = %s",
        (market,)).fetchone()[0]
    assert current == ['commodities', 'dock']


def test_unchanged_sighting_in_the_same_batch_advances_the_comparison_time(observer):
    market = next_market()
    record_sighting(observer, sighting(market, ts=T0))
    batch = [sighting(market, ts=T0 + timedelta(hours=3)),
             sighting(market, ts=T0 + timedelta(hours=2), services=('dock',))]
    # Sorted by source time: B at t2 is a real change between t1 and t3, then A again.
    assert record_sightings(observer, batch) == WriteResult(2, 0, 0)
    assert [r[3] for r in rows(observer, market)] == [
        ['commodities', 'dock'], ['dock'], ['commodities', 'dock']]


def test_watermarks_do_not_advance_when_the_batch_fails(observer):
    import psycopg

    market = next_market()
    seen = SeenWatermarks(1000)
    bad = sighting(next_market())
    object.__setattr__(bad, 'station_name', 'x' * 200)
    with pytest.raises(psycopg.errors.CheckViolation):
        record_sightings(observer, [sighting(market, ts=T0 + timedelta(days=1)), bad], seen=seen)
    assert seen.get('eddn', market) is None
    assert record_sighting(observer, sighting(market, ts=T0), seen=seen) == WriteResult(1, 0, 0)


def test_one_batch_is_ordered_by_source_time_and_deduplicated(observer):
    market = next_market()
    other = next_market()
    batch = [
        sighting(market, ts=T0 + timedelta(minutes=30), services=('dock',)),
        sighting(market, ts=T0),
        sighting(market, ts=T0 + timedelta(minutes=10)),
        sighting(other, ts=T0),
        sighting(other, ts=T0 + timedelta(minutes=1)),
    ]
    assert record_sightings(observer, batch) == WriteResult(3, 2, 0)
    assert [r[3] for r in rows(observer, market)] == [['commodities', 'dock'], ['dock']]
    assert len(rows(observer, other)) == 1


def test_sources_keep_independent_current_state(observer):
    market = next_market()
    spansh = sighting(market, source='spansh', body_id=7, body_name='Sol 3')
    eddn = sighting(market)
    assert record_sightings(observer, [spansh, eddn]) == WriteResult(2, 0, 0)
    assert record_sightings(observer, [spansh, eddn]) == WriteResult(0, 2, 0)
    assert [r[0] for r in rows(observer, market)] == ['eddn', 'spansh']


def test_writer_refuses_a_session_that_did_not_set_role(owner):
    with pytest.raises(PermissionError):
        record_sighting(owner, sighting(next_market()))


def test_writer_refuses_an_open_transaction(dsn):
    import psycopg

    with psycopg.connect(dsn) as conn:
        configure_observer_session(conn)
        conn.execute('SELECT 1')
        with pytest.raises(RuntimeError, match='idle connection'):
            record_sighting(conn, sighting(next_market()))


def test_failed_batch_rolls_back_entirely(observer):
    market = next_market()
    good = sighting(market)
    bad = sighting(next_market())
    object.__setattr__(bad, 'station_name', 'x' * 200)  # bypass Python bounds; DB must refuse
    import psycopg

    with pytest.raises(psycopg.errors.CheckViolation):
        record_sightings(observer, [good, bad])
    assert rows(observer, market) == []


# -- end to end ------------------------------------------------------------


def _frame(market_id, *, services=('dock', 'commodities'), minutes=0):
    ts = T0 + timedelta(minutes=minutes)
    return zlib.compress(json.dumps({
        '$schemaRef': 'https://eddn.edcd.io/schemas/journal/1',
        'header': {'uploaderID': UPLOADER, 'softwareName': 'EDMC', 'softwareVersion': '5.12',
                   'gatewayTimestamp': (ts + timedelta(seconds=1)).isoformat().replace('+00:00', 'Z'),
                   'gameversion': '4.1.3.0', 'gamebuild': 'r1'},
        'message': {'timestamp': ts.isoformat().replace('+00:00', 'Z'), 'event': 'Docked',
                    'StationName': 'Worker Port', 'StationType': 'Orbis', 'MarketID': market_id,
                    'SystemAddress': 99, 'StarSystem': 'Somewhere',
                    'StationEconomies': [{'Name': '$economy_Tourism;', 'Proportion': 1.0}],
                    'StationServices': list(services),
                    'LandingPads': {'Small': 2, 'Medium': 0, 'Large': 0}},
    }).encode())


class ScriptedSubscriber:
    def __init__(self, frames, stop):
        self.frames = list(frames)
        self.stop = stop

    def recv(self, timeout_ms):
        if self.frames:
            return self.frames.pop(0)
        self.stop.set()
        return None

    def close(self):
        pass


def _config(dsn, **overrides):
    values = {'database_url': dsn, 'flush_max_rows': 4, 'buffer_max_rows': 100,
              'max_consecutive_flush_failures': 2, 'max_event_age': timedelta(days=3650)}
    values.update(overrides)
    return WorkerConfig(**values)


def test_worker_writes_change_only_rows_without_uploader_ids(dsn, owner):
    market = next_market()
    stop = threading.Event()
    frames = [_frame(market), _frame(market, minutes=1), _frame(market, minutes=2),
              _frame(market, services=('dock',), minutes=3), _frame(market, minutes=4)]
    writer = PostgresWriter(dsn)
    worker = Worker(_config(dsn), subscriber_factory=lambda url: ScriptedSubscriber(frames, stop),
                    writer=writer, metrics=Metrics(), stop_event=stop,
                    wall_clock=lambda: T0 + timedelta(minutes=5))
    try:
        worker.run()
    finally:
        writer.close()
    stored = owner.execute(
        'SELECT row_to_json(s)::text FROM v3_observe.station_sighting s '
        'WHERE market_id = %s ORDER BY sighting_id', (market,)).fetchall()
    assert len(stored) == 3
    assert all(UPLOADER not in r[0] for r in stored)
    first = json.loads(stored[0][0])
    assert first['software_name'] == 'EDMC' and first['economies'] == [
        {'name': 'Tourism', 'proportion': 1.0}]
    m = worker.metrics
    assert (m.rows_inserted_total, m.rows_unchanged_total, m.flush_failures_total) == (3, 2, 0)
    assert m.last_successful_flush_at > 0
    sessions = owner.execute(
        "SELECT count(*) FROM pg_stat_activity WHERE application_name = 'edfinder-eddn-v3-observer'"
    ).fetchone()[0]
    assert sessions == 0


def test_batch_failures_reach_metrics_and_trip_the_breaker():
    # A database without the proposal: SET ROLE works (the role is cluster-wide)
    # but the write fails, exactly like a broken write path in production.
    with scratch_database(apply_proposal=False) as empty:
        stop = threading.Event()
        frames = [_frame(next_market(), minutes=i) for i in range(12)]
        writer = PostgresWriter(empty)
        worker = Worker(_config(empty), subscriber_factory=lambda url: ScriptedSubscriber(frames, stop),
                        writer=writer, metrics=Metrics(), stop_event=stop,
                        wall_clock=lambda: T0 + timedelta(minutes=15))
        try:
            with pytest.raises(FlushFailureThresholdExceeded):
                worker.run()
        finally:
            writer.close()
        m = worker.metrics
        assert (m.flush_failures_total, m.consecutive_flush_failures) == (2, 2)
        assert m.flush_failed_rows_total == 8
        assert m.last_successful_flush_at == 0.0 and m.last_flush_attempt_at > 0
        assert m.health(300)[0] is False
