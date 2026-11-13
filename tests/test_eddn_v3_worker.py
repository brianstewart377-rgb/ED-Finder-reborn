"""Unit tests for the V3 EDDN station-sighting worker (apps/eddn_v3).

No database and no network: normalisation, filtering, bounds, the state hash,
the uploaderID privacy rule, config bounds, metrics/liveness and the relay
loop against a fake subscriber and a fake writer.
"""
from __future__ import annotations

import ast
import json
import logging
import math
import os
import sys
import threading
import urllib.error
import urllib.request
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'apps' / 'eddn_v3' / 'src'
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

from edfinder_observe import eddn_docked, healthcheck, sightings  # noqa: E402
from edfinder_observe.__main__ import main  # noqa: E402
from edfinder_observe.config import ConfigError, WorkerConfig, load_config  # noqa: E402
from edfinder_observe.eddn_docked import (  # noqa: E402
    Filtered,
    decode_frame,
    normalise_docked,
)
from edfinder_observe.metrics import Metrics, serve_metrics  # noqa: E402
from edfinder_observe.sightings import (  # noqa: E402
    SeenWatermarks,
    SightingRejected,
    WriteResult,
    build_sighting,
    state_sha256,
)
from edfinder_observe.worker import (  # noqa: E402
    FinalFlushFailed,
    FlushFailureThresholdExceeded,
    RECEIVE_QUEUE_BYTE_BUDGET,
    PostgresWriter,
    Worker,
    ZmqSubscriber,
    receive_high_water_mark,
)

pytestmark = pytest.mark.unit

RECEIVED = datetime(2026, 10, 21, 12, 0, 30, tzinfo=UTC)
UPLOADER = 'uploader-7f3c2a-should-never-appear'
WINDOW = {'max_event_age': timedelta(days=1), 'max_clock_skew': timedelta(minutes=5)}


def docked_frame(**message_overrides) -> dict:
    message = {
        'timestamp': '2026-10-21T12:00:00Z',
        'event': 'Docked',
        'StationName': 'Ehrlich City',
        'StationType': 'Coriolis',
        'MarketID': 3223343616,
        'StarSystem': 'Sol',
        'SystemAddress': 10477373803,
        'DistFromStarLS': 506.3,
        'StationEconomies': [
            {'Name': '$economy_Industrial;', 'Proportion': 0.8},
            {'Name': '$economy_Refinery;', 'Proportion': 0.2},
        ],
        'StationServices': ['dock', 'autodock', 'commodities', 'dock', 'shipyard'],
        'LandingPads': {'Small': 4, 'Medium': 8, 'Large': 2},
        'StationGovernment': '$government_Democracy;',
        'Taxi': False,
        'Multicrew': False,
        'StarPos': [0.0, 0.0, 0.0],
        'horizons': True,
        'odyssey': True,
    }
    message.update(message_overrides)
    return {
        '$schemaRef': 'https://eddn.edcd.io/schemas/journal/1',
        'header': {
            'uploaderID': UPLOADER,
            'softwareName': 'E:D Market Connector [Windows]',
            'softwareVersion': '5.12.1',
            'gatewayTimestamp': '2026-10-21T12:00:01.123456Z',
            'gameversion': '4.1.3.0',
            'gamebuild': 'r312345/r0 ',
        },
        'message': message,
    }


def compress(frame: dict) -> bytes:
    return zlib.compress(json.dumps(frame).encode('utf-8'))


def normalise(frame: dict):
    return normalise_docked(frame, received_at=RECEIVED, **WINDOW)


# -- normalisation -------------------------------------------------------


def test_docked_frame_normalises_to_allowlisted_canonical_sighting():
    s = normalise(decode_frame(compress(docked_frame()), max_bytes=262_144))
    assert s.source == 'eddn'
    assert (s.market_id, s.system_id64, s.system_name) == (3223343616, 10477373803, 'Sol')
    assert (s.station_name, s.station_type) == ('Ehrlich City', 'Coriolis')
    assert [(e.name, e.proportion) for e in s.economies] == [('Industrial', 0.8), ('Refinery', 0.2)]
    assert s.services == ('autodock', 'commodities', 'dock', 'shipyard')
    assert s.landing_pads == (('large', 2), ('medium', 8), ('small', 4))
    assert s.distance_ls == 506.3
    assert s.body_id is None and s.body_name is None
    assert s.source_record_ts == datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    assert s.gateway_ts == datetime(2026, 10, 21, 12, 0, 1, 123456, tzinfo=UTC)
    assert s.observed_at == RECEIVED
    assert (s.software_name, s.software_version) == ('E:D Market Connector [Windows]', '5.12.1')
    assert (s.game_version, s.game_build) == ('4.1.3.0', 'r312345/r0')


def test_missing_informational_fields_are_allowed_and_empty_lists_are_real_state():
    frame = docked_frame(StationEconomies=[], StationServices=[])
    for key in ('DistFromStarLS', 'StarSystem'):
        del frame['message'][key]
    s = normalise(frame)
    assert s.economies == () and s.services == ()
    assert s.distance_ls is None and s.system_name is None


@pytest.mark.parametrize('key', ['StationType', 'StationEconomies', 'StationServices', 'LandingPads'])
@pytest.mark.parametrize('how', ['missing', 'null'])
def test_frames_missing_a_hashed_state_field_are_rejected_not_treated_as_empty(key, how):
    frame = docked_frame()
    if how == 'missing':
        del frame['message'][key]
    else:
        frame['message'][key] = None
    with pytest.raises(SightingRejected) as exc:
        normalise(frame)
    assert exc.value.reason == 'incomplete_state'


@pytest.mark.parametrize('blank', ['', '   ', '\t'])
def test_blank_station_type_is_rejected_and_counted_not_recorded_as_a_removal(blank):
    with pytest.raises(SightingRejected) as exc:
        normalise(docked_frame(StationType=blank))
    assert exc.value.reason == 'incomplete_state'
    written = []
    worker = _worker(writer=lambda b: written.extend(b) or WriteResult(len(b), 0, 0))
    worker.handle(compress(docked_frame(StationType=blank)))
    worker.drain()
    assert written == [] and not worker.buffer
    assert worker.metrics.rejected_total['incomplete_state'] == 1
    assert 'edfinder_eddn_v3_messages_rejected_total{reason="incomplete_state"} 1' in worker.metrics.render()


@pytest.mark.parametrize(('mutate', 'reason'), [
    (lambda f: f['message'].update({'StationName': 'Port \ud800'}), 'invalid_station_name'),
    (lambda f: f['message'].update({'StarSystem': '\udfff'}), 'invalid_system_name'),
    (lambda f: f['message'].update({'StationServices': ['dock', '\ud83d']}), 'invalid_services'),
    (lambda f: f['message'].update({'StationEconomies': [{'Name': '\ud800', 'Proportion': 1.0}]}),
     'invalid_economies'),
    (lambda f: f['header'].update({'softwareName': 'EDMC \udc00'}), 'invalid_header'),
])
def test_lone_surrogates_are_rejected_during_normalisation(mutate, reason):
    frame = docked_frame()
    mutate(frame)
    # Through the real wire path: json.dumps escapes the surrogate, json.loads restores it.
    decoded = decode_frame(compress(frame), max_bytes=262_144)
    with pytest.raises(SightingRejected) as exc:
        normalise(decoded)
    assert exc.value.reason == reason


def test_future_source_times_are_rejected_for_every_source():
    with pytest.raises(SightingRejected, match='invalid_timestamp'):
        _sighting(source='spansh', source_record_ts=datetime(9999, 1, 1, tzinfo=UTC))
    with pytest.raises(SightingRejected, match='invalid_timestamp'):
        _sighting(source_record_ts=RECEIVED + timedelta(hours=1, seconds=1))
    with pytest.raises(SightingRejected, match='invalid_timestamp'):
        _sighting(gateway_ts=RECEIVED + timedelta(hours=2))
    assert _sighting(source_record_ts=RECEIVED + timedelta(minutes=59)).source_record_ts > RECEIVED


def test_replayed_old_frame_is_rejected_even_when_event_and_gateway_agree():
    frame = docked_frame(timestamp='2026-10-18T12:00:00Z')
    frame['header']['gatewayTimestamp'] = '2026-10-18T12:00:01Z'
    with pytest.raises(SightingRejected, match='invalid_timestamp'):
        normalise(frame)


@pytest.mark.parametrize(('mutate', 'reason'), [
    (lambda f: f.update({'$schemaRef': 'https://eddn.edcd.io/schemas/journal/1/test'}), 'schema'),
    (lambda f: f.update({'$schemaRef': 'https://eddn.edcd.io/schemas/commodity/3'}), 'schema'),
    (lambda f: f['message'].update({'event': 'FSDJump'}), 'event'),
    (lambda f: f['message'].update({'event': 'Location'}), 'event'),
    (lambda f: f['message'].update({'StationType': 'FleetCarrier'}), 'fleet_carrier'),
    (lambda f: f['message'].update({'StationType': 'Drake-Class Carrier'}), 'fleet_carrier'),
    (lambda f: f['message'].update({'StationType': 'fleet carrier'}), 'fleet_carrier'),
    (lambda f: f['header'].update({'gameversion': '3.8.0.407'}), 'legacy'),
    (lambda f: f['header'].update({'gameversion': 'CAPI-Legacy-market'}), 'legacy'),
    (lambda f: f['header'].pop('gameversion'), 'legacy'),
])
def test_unwanted_messages_are_filtered(mutate, reason):
    frame = docked_frame()
    mutate(frame)
    with pytest.raises(Filtered) as exc:
        normalise(frame)
    assert exc.value.reason == reason


def test_capi_live_game_version_is_accepted():
    frame = docked_frame()
    frame['header']['gameversion'] = 'CAPI-Live-market'
    assert normalise(frame).game_version == 'CAPI-Live-market'


@pytest.mark.parametrize(('mutate', 'reason'), [
    (lambda m: m.update({'MarketID': 0}), 'invalid_market_id'),
    (lambda m: m.update({'MarketID': -5}), 'invalid_market_id'),
    (lambda m: m.update({'MarketID': True}), 'invalid_market_id'),
    (lambda m: m.update({'MarketID': '3223343616'}), 'invalid_market_id'),
    (lambda m: m.update({'MarketID': 1 << 63}), 'invalid_market_id'),
    (lambda m: m.pop('SystemAddress'), 'invalid_system_id64'),
    (lambda m: m.update({'StationName': ''}), 'invalid_station_name'),
    (lambda m: m.update({'StationName': 'x' * 129}), 'invalid_station_name'),
    (lambda m: m.update({'StationName': 'tab\tname'}), 'invalid_station_name'),
    (lambda m: m.update({'StationType': 'x' * 65}), 'invalid_station_type'),
    (lambda m: m.update({'StationEconomies': [{'Name': f'E{i}', 'Proportion': 0.1}
                                              for i in range(9)]}), 'invalid_economies'),
    (lambda m: m.update({'StationEconomies': [{'Name': 'A', 'Proportion': 1.5}]}),
     'invalid_economies'),
    (lambda m: m.update({'StationEconomies': [{'Name': 'A', 'Proportion': -0.1}]}),
     'invalid_economies'),
    (lambda m: m.update({'StationEconomies': [{'Name': 'A', 'Proportion': math.nan}]}),
     'invalid_economies'),
    (lambda m: m.update({'StationEconomies': [{'Name': 'A', 'Proportion': '0.5'}]}),
     'invalid_economies'),
    (lambda m: m.update({'StationEconomies': [{'Name': 'A', 'Proportion': 0.5},
                                              {'Name': 'A', 'Proportion': 0.5}]}),
     'invalid_economies'),
    (lambda m: m.update({'StationEconomies': {'A': 1}}), 'invalid_economies'),
    (lambda m: m.update({'StationServices': [f's{i}' for i in range(129)]}), 'invalid_services'),
    (lambda m: m.update({'StationServices': ['x' * 129]}), 'invalid_services'),
    (lambda m: m.update({'StationServices': [None]}), 'invalid_services'),
    (lambda m: m.update({'StationServices': 'dock'}), 'invalid_services'),
    (lambda m: m.update({'LandingPads': {'Small': 256}}), 'invalid_landing_pads'),
    (lambda m: m.update({'LandingPads': {'Huge': 1}}), 'invalid_landing_pads'),
    (lambda m: m.update({'LandingPads': {'Small': 1.5}}), 'invalid_landing_pads'),
    (lambda m: m.update({'DistFromStarLS': -1}), 'invalid_distance'),
    (lambda m: m.update({'DistFromStarLS': math.inf}), 'invalid_distance'),
    (lambda m: m.update({'timestamp': '2026-10-19T12:00:00Z'}), 'invalid_timestamp'),
    (lambda m: m.update({'timestamp': '2026-10-21T12:30:00Z'}), 'invalid_timestamp'),
    (lambda m: m.update({'timestamp': '2026-10-21T12:00:00'}), 'invalid_timestamp'),
    (lambda m: m.update({'timestamp': 'yesterday'}), 'invalid_timestamp'),
])
def test_invalid_messages_are_rejected_with_a_bounded_reason(mutate, reason):
    frame = docked_frame()
    mutate(frame['message'])
    with pytest.raises(SightingRejected) as exc:
        normalise(frame)
    assert exc.value.reason == reason


def test_gateway_time_in_the_future_is_rejected():
    frame = docked_frame(timestamp='2026-10-21T13:00:00Z')
    frame['header']['gatewayTimestamp'] = '2026-10-21T13:00:01Z'
    with pytest.raises(SightingRejected, match='invalid_timestamp'):
        normalise(frame)


def test_decode_bounds_and_errors():
    raw = compress(docked_frame())
    assert decode_frame(raw, max_bytes=262_144)['message']['event'] == 'Docked'
    with pytest.raises(SightingRejected, match='oversize'):
        decode_frame(raw, max_bytes=len(raw) - 1)
    bomb = zlib.compress(b'{"a":"' + b'x' * 2_000_000 + b'"}')
    assert len(bomb) < 262_144
    with pytest.raises(SightingRejected, match='oversize'):
        decode_frame(bomb, max_bytes=262_144)
    with pytest.raises(SightingRejected, match='decode'):
        decode_frame(b'not zlib', max_bytes=1024)
    with pytest.raises(SightingRejected, match='decode'):
        decode_frame(zlib.compress(b'[1, 2]'), max_bytes=1024)
    with pytest.raises(SightingRejected, match='decode'):
        decode_frame(zlib.compress(b'{"truncated": '), max_bytes=1024)


@pytest.mark.parametrize('cut', [1, 2, 3, 4, 5])
def test_truncated_zlib_stream_is_rejected_even_if_the_json_is_complete(cut):
    raw = compress(docked_frame())
    with pytest.raises(SightingRejected, match='decode'):
        decode_frame(raw[:-cut], max_bytes=262_144)


def test_trailing_bytes_after_the_zlib_stream_are_rejected():
    with pytest.raises(SightingRejected, match='decode'):
        decode_frame(compress(docked_frame()) + b'extra', max_bytes=262_144)


# -- state hash ----------------------------------------------------------


def _sighting(**overrides):
    values = {
        'source': 'eddn', 'market_id': 1, 'system_id64': 2, 'station_name': 'S',
        'station_type': 'Orbis', 'economies': [('Industrial', 0.8), ('Refinery', 0.2)],
        'services': ['dock', 'commodities'], 'landing_pads': {'Small': 1, 'Medium': 2, 'Large': 3},
        'source_record_ts': RECEIVED, 'observed_at': RECEIVED,
    }
    values.update(overrides)
    return build_sighting(**values)


def test_state_hash_ignores_order_time_distance_and_header_fields():
    base = state_sha256(_sighting())
    assert len(base) == 32
    assert state_sha256(_sighting(
        economies=[('Refinery', 0.2), ('Industrial', 0.8)],
        services=['commodities', 'dock', 'dock'],
        source_record_ts=RECEIVED + timedelta(days=3),
        observed_at=RECEIVED + timedelta(days=3),
        distance_ls=12.5, system_name='Other', software_name='X', game_version='4.2',
        landing_pads={'small': 1, 'MEDIUM': 2, 'large': 3},
    )) == base
    # Proportions are rounded to 4 decimals before hashing.
    assert state_sha256(_sighting(economies=[('Industrial', 0.80000001),
                                             ('Refinery', 0.19999999)])) == base


@pytest.mark.parametrize('change', [
    {'system_id64': 3},             # a megaship moved, same MarketID
    {'station_name': 'S renamed'},
    {'station_type': 'Coriolis'},
    {'economies': [('Industrial', 0.7), ('Refinery', 0.3)]},
    {'economies': [('Industrial', 1.0)]},
    {'services': ['dock']},
    {'landing_pads': {'Small': 2, 'Medium': 2, 'Large': 3}},
    {'landing_pads': None},
])
def test_state_hash_changes_with_research_state(change):
    assert state_sha256(_sighting(**change)) != state_sha256(_sighting())


def test_negative_zero_proportion_hashes_like_zero():
    signed = _sighting(economies=[('Industrial', 1.0), ('Refinery', -0.0)])
    plain = _sighting(economies=[('Industrial', 1.0), ('Refinery', 0.0)])
    assert [math.copysign(1, e.proportion) for e in signed.economies] == [1.0, 1.0]
    assert state_sha256(signed) == state_sha256(plain)
    assert '-0' not in signed.economies_json()
    frame = docked_frame(StationEconomies=[{'Name': '$economy_Industrial;', 'Proportion': 1.0},
                                           {'Name': '$economy_Refinery;', 'Proportion': -0.0}])
    via_wire = normalise(decode_frame(compress(frame), max_bytes=262_144))
    assert state_sha256(via_wire) == state_sha256(normalise(docked_frame(
        StationEconomies=[{'Name': '$economy_Industrial;', 'Proportion': 1.0},
                          {'Name': '$economy_Refinery;', 'Proportion': 0.0}])))


@pytest.mark.parametrize('pads', [
    {}, {'Small': 4}, {'Small': 4, 'Medium': 8}, {'Medium': 8, 'Large': 2},
])
def test_partial_landing_pad_snapshot_is_rejected_and_counted(pads):
    with pytest.raises(SightingRejected) as exc:
        normalise(docked_frame(LandingPads=pads))
    assert exc.value.reason == 'incomplete_state'
    worker = _worker(writer=lambda b: WriteResult(len(b), 0, 0))
    worker.handle(compress(docked_frame(LandingPads=pads)))
    assert not worker.buffer and worker.metrics.rejected_total['incomplete_state'] == 1


def test_source_specific_fields_are_enforced():
    with pytest.raises(SightingRejected, match='invalid_source_fields'):
        _sighting(body_id=3)
    with pytest.raises(SightingRejected, match='invalid_source_fields'):
        _sighting(source='spansh', software_name='EDMC')
    with pytest.raises(SightingRejected, match='invalid_source'):
        _sighting(source='journal')
    spansh = _sighting(source='spansh', body_id=3, body_name='Earth')
    assert state_sha256(spansh) != state_sha256(_sighting(source='spansh'))


# -- uploaderID privacy --------------------------------------------------


class UploaderTrap(dict):
    """A header that fails the test if uploaderID is read or the header is iterated."""

    def _guard(self, key):
        if key == 'uploaderID':
            raise AssertionError('uploaderID was read')

    def __getitem__(self, key):
        self._guard(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self._guard(key)
        return super().get(key, default)

    def __iter__(self):
        raise AssertionError('header was iterated')

    def keys(self):
        raise AssertionError('header was iterated')

    def items(self):
        raise AssertionError('header was iterated')

    def values(self):
        raise AssertionError('header was iterated')

    def copy(self):
        raise AssertionError('header was copied')


def test_uploader_id_is_never_read_by_normalisation():
    frame = docked_frame()
    frame['header'] = UploaderTrap(frame['header'])
    sighting = normalise(frame)
    assert UPLOADER not in repr(sighting)


def test_uploader_id_is_never_read_by_decoding(monkeypatch):
    trapped = docked_frame()
    trapped['header'] = UploaderTrap(trapped['header'])

    class TrapJson:
        @staticmethod
        def loads(_data):
            return trapped

    monkeypatch.setattr(eddn_docked, 'json', TrapJson)
    decoded = decode_frame(zlib.compress(b'{}'), max_bytes=1024)
    assert set(decoded['header']) == set(eddn_docked.HEADER_ALLOWLIST)
    assert 'uploaderID' not in decoded['header']


def test_uploader_id_never_reaches_rows_logs_or_metrics(caplog):
    caplog.set_level(logging.DEBUG)
    written = []
    worker = _worker(writer=lambda batch: written.extend(batch) or WriteResult(len(batch), 0, 0))
    worker.handle(compress(docked_frame()))
    worker.handle(compress(docked_frame(StationType='FleetCarrier')))
    worker.handle(compress(docked_frame(MarketID=-1)))
    worker.handle(b'\x00garbage')
    worker.drain()
    assert len(written) == 1
    row = sightings._row(written[0], written[0].state_sha256)
    assert all(UPLOADER not in str(value) for value in row)
    assert UPLOADER not in caplog.text
    assert UPLOADER not in worker.metrics.render()


def test_package_source_never_names_uploader_id_as_a_key():
    for path in (PACKAGE / 'edfinder_observe').glob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value.strip().casefold() not in {'uploaderid', 'uploader_id'}, path
            if isinstance(node, ast.Name | ast.Attribute | ast.arg):
                name = getattr(node, 'id', None) or getattr(node, 'attr', None) or getattr(node, 'arg', '')
                assert 'uploader' not in name.casefold(), path


# -- config --------------------------------------------------------------

DSN = 'postgresql://edfinder:secret-password@127.0.0.1:55432/edfinder'


def test_config_defaults_and_redaction():
    config = load_config({'EDDN_V3_DATABASE_URL': DSN})
    assert config.relay_url == 'tcp://eddn.edcd.io:9500'
    assert (config.flush_interval_seconds, config.flush_max_rows, config.buffer_max_rows) == (15, 500, 5000)
    assert config.max_consecutive_flush_failures == 5
    assert 'secret-password' not in repr(config)


@pytest.mark.parametrize(('env', 'message'), [
    ({}, 'EDDN_V3_DATABASE_URL'),
    ({'EDDN_V3_DATABASE_URL': 'mysql://x'}, 'EDDN_V3_DATABASE_URL'),
    ({'EDDN_V3_RELAY_URL': 'http://eddn.edcd.io:9500'}, 'EDDN_V3_RELAY_URL'),
    ({'EDDN_V3_FLUSH_MAX_ROWS': '0'}, 'EDDN_V3_FLUSH_MAX_ROWS'),
    ({'EDDN_V3_FLUSH_MAX_ROWS': '5001'}, 'EDDN_V3_FLUSH_MAX_ROWS'),
    ({'EDDN_V3_FLUSH_INTERVAL_SECONDS': 'soon'}, 'EDDN_V3_FLUSH_INTERVAL_SECONDS'),
    ({'EDDN_V3_BUFFER_MAX_ROWS': '200', 'EDDN_V3_FLUSH_MAX_ROWS': '300'}, 'EDDN_V3_BUFFER_MAX_ROWS'),
    ({'EDDN_V3_MAX_CONSECUTIVE_FLUSH_FAILURES': '0'}, 'EDDN_V3_MAX_CONSECUTIVE_FLUSH_FAILURES'),
    ({'EDDN_V3_MAX_MESSAGE_BYTES': '99999999'}, 'EDDN_V3_MAX_MESSAGE_BYTES'),
    ({'EDDN_V3_METRICS_HOST': 'example.com'}, 'EDDN_V3_METRICS_HOST'),
    ({'EDDN_V3_METRICS_HOST': '::1'}, 'EDDN_V3_METRICS_HOST'),
    ({'EDDN_V3_WATERMARK_MAX_STATIONS': '10'}, 'EDDN_V3_WATERMARK_MAX_STATIONS'),
    ({'EDDN_V3_METRICS_PORT': '80'}, 'EDDN_V3_METRICS_PORT'),
    ({'EDDN_V3_LOG_LEVEL': 'TRACE'}, 'EDDN_V3_LOG_LEVEL'),
])
def test_config_rejects_out_of_bounds_values_without_echoing_secrets(env, message):
    full = env if not env or 'EDDN_V3_DATABASE_URL' in env else {'EDDN_V3_DATABASE_URL': DSN, **env}
    with pytest.raises(ConfigError) as exc:
        load_config(full)
    assert message in str(exc.value)
    assert 'secret-password' not in str(exc.value)


def test_main_exits_2_on_bad_config(monkeypatch, capsys):
    for key in list(os.environ):
        if key.startswith('EDDN_V3_'):
            monkeypatch.delenv(key)
    assert main() == 2
    assert 'EDDN_V3_DATABASE_URL' in capsys.readouterr().err


# -- worker loop ---------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeSubscriber:
    """Plays a script of frames (bytes), quiet polls (None) or errors."""

    def __init__(self, script, clock, *, step=1.0, on_exhausted=None):
        self.script = list(script)
        self.clock = clock
        self.step = step
        self.on_exhausted = on_exhausted
        self.closed = False

    def recv(self, timeout_ms):
        self.clock.now += self.step
        if not self.script:
            if self.on_exhausted:
                self.on_exhausted()
            return None
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed = True


def _config(**overrides) -> WorkerConfig:
    values = {'database_url': DSN, 'flush_interval_seconds': 15.0, 'flush_max_rows': 3,
              'buffer_max_rows': 100, 'max_consecutive_flush_failures': 3,
              'recv_idle_reconnect_seconds': 30.0, 'reconnect_backoff_max_seconds': 8.0}
    values.update(overrides)
    return WorkerConfig(**values)


def _worker(*, writer, config=None, factory=None, clock=None, stop=None, waits=None):
    clock = clock or FakeClock()
    stop = stop or threading.Event()
    worker = Worker(config or _config(), subscriber_factory=factory or (lambda url: None),
                    writer=writer, metrics=Metrics(clock=lambda: clock.now), stop_event=stop,
                    clock=clock, wall_clock=lambda: RECEIVED,
                    wait=(lambda seconds: waits.append(seconds) or stop.is_set())
                    if waits is not None else None)
    return worker


def _frames(count, *, start=1):
    return [compress(docked_frame(MarketID=start + i)) for i in range(count)]


def test_flush_on_max_rows_and_on_interval():
    batches = []
    clock = FakeClock()
    worker = _worker(writer=lambda b: batches.append(list(b)) or WriteResult(len(b), 0, 0),
                     clock=clock)
    for raw in _frames(4):
        worker.handle(raw)
        worker.maybe_flush(clock.now)
    assert [len(b) for b in batches] == [3]
    assert len(worker.buffer) == 1
    clock.now += 14
    worker.maybe_flush(clock.now)
    assert len(batches) == 1
    clock.now += 2
    worker.maybe_flush(clock.now)
    assert [len(b) for b in batches] == [3, 1]
    assert worker.metrics.rows_inserted_total == 4
    assert worker.metrics.last_successful_flush_at == clock.now


def test_full_buffer_drops_oldest_and_counts_it():
    batches = []
    worker = _worker(writer=lambda b: batches.append(list(b)) or WriteResult(len(b), 0, 0),
                     config=_config(buffer_max_rows=100, flush_max_rows=100))
    for raw in _frames(103):
        worker.handle(raw)
    assert len(worker.buffer) == 100
    assert worker.metrics.buffer_dropped_total == 3
    assert worker.buffer[0].market_id == 4
    worker.drain()
    assert worker.metrics.rows_inserted_total == 100


def test_filtered_and_rejected_frames_are_counted_by_reason():
    worker = _worker(writer=lambda b: WriteResult(len(b), 0, 0))
    worker.handle(compress(docked_frame(event='Scan')))
    worker.handle(compress(docked_frame(StationType='FleetCarrier')))
    worker.handle(compress(docked_frame(MarketID=0)))
    worker.handle(b'junk')
    worker.handle(compress(docked_frame()))
    m = worker.metrics
    assert m.messages_received_total == 5
    assert m.filtered_total['event'] == 1 and m.filtered_total['fleet_carrier'] == 1
    assert m.rejected_total['invalid_market_id'] == 1 and m.rejected_total['decode'] == 1
    assert m.sightings_accepted_total == 1
    text = m.render()
    assert 'edfinder_eddn_v3_messages_filtered_total{reason="fleet_carrier"} 1' in text
    assert 'edfinder_eddn_v3_messages_rejected_total{reason="decode"} 1' in text
    assert text.count('# TYPE edfinder_eddn_v3_messages_rejected_total counter') == 1


class WriteBroken(RuntimeError):
    sqlstate = '57014'


def test_batch_failure_updates_metrics_health_and_trips_the_breaker(caplog):
    outcomes = [WriteBroken('boom row (secret detail)'), WriteBroken('boom'), None,
                WriteBroken('a'), WriteBroken('b'), WriteBroken('c')]

    def writer(batch):
        outcome = outcomes.pop(0)
        if outcome:
            raise outcome
        return WriteResult(len(batch), 0, 0)

    clock = FakeClock()
    worker = _worker(writer=writer, clock=clock)
    m = worker.metrics
    m.message_received()
    for raw in _frames(3):
        worker.handle(raw)
    assert worker.flush_once() is False
    assert (m.flush_failures_total, m.consecutive_flush_failures, m.flush_failed_rows_total) == (1, 1, 3)
    # The failed batch is kept, oldest first, for the next attempt.
    assert [s.market_id for s in worker.buffer] == [1, 2, 3]
    assert m.buffered_rows == 3
    assert m.last_successful_flush_at == 0.0 and m.last_flush_attempt_at == clock.now
    assert m.health(300) == (False, 'flush failing (1 consecutive)')
    assert 'secret detail' not in caplog.text and 'sqlstate=57014' in caplog.text
    assert 'edfinder_eddn_v3_flush_failures_total 1' in m.render()

    for raw in _frames(1):
        worker.handle(raw)
    assert worker.flush_once() is False
    assert m.consecutive_flush_failures == 2
    worker.handle(_frames(1)[0])
    assert worker.flush_once() is True
    assert m.consecutive_flush_failures == 0 and m.flush_failures_total == 2
    assert m.last_successful_flush_at == clock.now
    assert m.health(300) == (True, 'ok')

    for _ in range(2):
        worker.handle(_frames(1)[0])
        assert worker.flush_once() is False
    worker.handle(_frames(1)[0])
    with pytest.raises(FlushFailureThresholdExceeded):
        worker.flush_once()
    assert m.consecutive_flush_failures == 3 and m.flush_failures_total == 5


def test_failed_batch_requeue_drops_oldest_when_the_buffer_overflows():
    def writer(batch):
        raise WriteBroken('down')

    worker = _worker(writer=writer, config=_config(buffer_max_rows=100, flush_max_rows=50,
                                                   max_consecutive_flush_failures=10))
    for raw in _frames(100):
        worker.handle(raw)
    assert worker.flush_once() is False
    assert len(worker.buffer) == 100 and worker.metrics.buffer_dropped_total == 0
    for raw in _frames(30, start=101):
        worker.handle(raw)          # full buffer: oldest 30 dropped on append
    assert worker.metrics.buffer_dropped_total == 30
    assert worker.flush_once() is False
    assert [s.market_id for s in worker.buffer][:3] == [31, 32, 33]
    assert worker.buffer[-1].market_id == 130 and len(worker.buffer) == 100


def test_retried_batch_is_written_once_the_database_recovers():
    outcomes = [WriteBroken('down'), None]
    written = []

    def writer(batch):
        outcome = outcomes.pop(0)
        if outcome:
            raise outcome
        written.extend(batch)
        return WriteResult(len(batch), 0, 0)

    worker = _worker(writer=writer)
    for raw in _frames(3):
        worker.handle(raw)
    assert worker.flush_once() is False
    assert worker.flush_once() is True
    assert [s.market_id for s in written] == [1, 2, 3] and not worker.buffer


def test_quiet_relay_still_retries_until_the_breaker_trips():
    clock = FakeClock()

    def factory(url):
        return FakeSubscriber(_frames(1), clock, step=5.0)

    def writer(batch):
        raise WriteBroken('db down')

    worker = _worker(writer=writer, factory=factory, clock=clock, waits=[])
    with pytest.raises(FlushFailureThresholdExceeded):
        worker.run()
    assert worker.metrics.flush_failures_total == 3
    assert worker.metrics.messages_received_total <= 3


def test_seen_watermarks_keep_the_maximum_and_stay_bounded():
    marks = SeenWatermarks(2)
    marks.advance('eddn', 1, RECEIVED)
    marks.advance('eddn', 1, RECEIVED - timedelta(hours=1))
    assert marks.get('eddn', 1) == RECEIVED
    marks.advance('eddn', 2, RECEIVED)
    marks.get('eddn', 1)                     # touch: 2 becomes least recent
    marks.advance('spansh', 1, RECEIVED)
    assert len(marks) == 2
    assert marks.get('eddn', 2) is None and marks.get('eddn', 1) == RECEIVED
    with pytest.raises(ValueError):
        SeenWatermarks(0)


def test_failed_final_flush_on_shutdown_is_not_a_clean_exit():
    clock = FakeClock()
    stop = threading.Event()

    def factory(url):
        return FakeSubscriber(_frames(2), clock, on_exhausted=stop.set)

    def writer(batch):
        raise WriteBroken('db down during shutdown')

    worker = _worker(writer=writer, factory=factory, clock=clock, stop=stop, waits=[])
    with pytest.raises(FinalFlushFailed) as exc:
        worker.run()
    assert exc.value.lost_rows == 2
    assert worker.metrics.flush_failures_total == 1


def test_successful_final_flush_on_shutdown_returns_cleanly():
    clock = FakeClock()
    stop = threading.Event()
    written = []
    worker = _worker(writer=lambda b: written.extend(b) or WriteResult(len(b), 0, 0),
                     factory=lambda url: FakeSubscriber(_frames(2), clock, on_exhausted=stop.set),
                     clock=clock, stop=stop, waits=[])
    worker.run()
    assert len(written) == 2 and not worker.buffer


def test_zmq_subscriber_bounds_inbound_bytes_before_allocation():
    import time

    import zmq

    context = zmq.Context.instance()
    publisher = context.socket(zmq.PUB)
    publisher.setsockopt(zmq.LINGER, 0)
    port = publisher.bind_to_random_port('tcp://127.0.0.1')
    endpoint = f'tcp://127.0.0.1:{port}'
    received = []

    def pump_until_small(subscriber, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            publisher.send(b'small')
            raw = subscriber.recv(100)
            while raw is not None:
                received.append(raw)
                if raw == b'small':
                    return True
                raw = subscriber.recv(10)
        return False

    first = ZmqSubscriber(endpoint, max_message_bytes=1024)
    try:
        assert first._socket.getsockopt(zmq.MAXMSGSIZE) == 1024
        assert first._socket.getsockopt(zmq.RCVHWM) == receive_high_water_mark(1024)
        assert pump_until_small(first, 10)       # connection established
        publisher.send(b'x' * 4096)              # oversized: ZeroMQ drops the peer
        # The oversized frame is never delivered, and this socket goes quiet;
        # the worker's idle-reconnect path then builds a fresh subscriber.
        assert not pump_until_small(first, 1.5)
    finally:
        first.close()
    second = ZmqSubscriber(endpoint, max_message_bytes=1024)
    try:
        assert pump_until_small(second, 10)
    finally:
        second.close()
        publisher.close(linger=0)
    assert all(len(item) <= 1024 for item in received)


@pytest.mark.parametrize(('env', 'url'), [
    ({}, 'http://127.0.0.1:9465/healthz'),
    ({'EDDN_V3_METRICS_HOST': '0.0.0.0', 'EDDN_V3_METRICS_PORT': '9500'},
     'http://127.0.0.1:9500/healthz'),
    ({'EDDN_V3_METRICS_HOST': '10.1.2.3'}, 'http://10.1.2.3:9465/healthz'),
])
def test_healthcheck_probes_the_configured_bind_address(env, url):
    assert healthcheck.probe_url(env) == url


@pytest.mark.parametrize('env', [
    {'EDDN_V3_METRICS_HOST': 'evil.example'},
    {'EDDN_V3_METRICS_HOST': '127.0.0.1/x?'},
    {'EDDN_V3_METRICS_PORT': '80'},
    {'EDDN_V3_METRICS_PORT': '9465/../x'},
])
def test_healthcheck_refuses_anything_but_an_ipv4_host_and_integer_port(env):
    with pytest.raises(ValueError):
        healthcheck.probe_url(env)


def test_healthcheck_exit_codes(monkeypatch):
    clock = FakeClock()
    metrics = Metrics(clock=lambda: clock.now)
    server = serve_metrics(metrics, '127.0.0.1', 0, 300)
    try:
        monkeypatch.setenv('EDDN_V3_METRICS_HOST', '127.0.0.1')
        monkeypatch.setenv('EDDN_V3_METRICS_PORT', str(server.server_address[1]))
        assert healthcheck.main() == 0
        metrics.flush_failure(1)
        assert healthcheck.main() == 1
    finally:
        server.shutdown()
        server.server_close()
    assert healthcheck.main() == 1          # nothing listening


def test_health_turns_stale_when_relay_is_silent():
    clock = FakeClock()
    m = Metrics(clock=lambda: clock.now)
    assert m.health(300) == (True, 'ok')
    clock.now += 301
    assert m.health(300)[0] is False
    m.message_received()
    assert m.health(300) == (True, 'ok')


def test_relay_loop_reconnects_with_capped_backoff_and_stops_cleanly():
    clock = FakeClock()
    stop = threading.Event()
    waits: list[float] = []
    written = []
    subscribers = []
    plan = [
        ConnectionError('relay down'),
        ConnectionError('relay down'),
        'idle',          # connects, never receives, idles out
        'error',         # receives one frame then a socket error
        'frames',        # receives frames, then stop is requested
    ]

    def factory(url):
        assert url == 'tcp://eddn.edcd.io:9500'
        step = plan.pop(0)
        if isinstance(step, BaseException):
            raise step
        if step == 'idle':
            sub = FakeSubscriber([], clock, step=5.0)
        elif step == 'error':
            sub = FakeSubscriber([_frames(1, start=100)[0], OSError('reset')], clock)
        else:
            sub = FakeSubscriber(_frames(2, start=200), clock, on_exhausted=stop.set)
        subscribers.append(sub)
        return sub

    worker = _worker(writer=lambda b: written.extend(b) or WriteResult(len(b), 0, 0),
                     factory=factory, clock=clock, stop=stop, waits=waits)
    worker.run()

    assert not plan
    assert all(sub.closed for sub in subscribers)
    # Exponential from 1 s; reset after a session that received frames.
    assert waits == [1.0, 2.0, 4.0, 1.0]
    assert worker.metrics.reconnects_total == 4
    # Every accepted frame is flushed: before the reconnect wait and on stop.
    assert sorted(s.market_id for s in written) == [100, 200, 201]
    assert worker.metrics.messages_received_total == 3


def test_backoff_is_capped():
    clock = FakeClock()
    stop = threading.Event()
    waits: list[float] = []

    def factory(url):
        if len(waits) >= 6:
            stop.set()
        raise ConnectionError('down')

    worker = _worker(writer=lambda b: WriteResult(), factory=factory, clock=clock,
                     stop=stop, waits=waits, config=_config(reconnect_backoff_max_seconds=8.0))
    worker.run()
    assert waits == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]


def test_relay_loop_reraises_after_consecutive_flush_failures():
    clock = FakeClock()

    def factory(url):
        return FakeSubscriber(_frames(30), clock)

    def writer(batch):
        raise WriteBroken('db down')

    worker = _worker(writer=writer, factory=factory, clock=clock, waits=[])
    with pytest.raises(FlushFailureThresholdExceeded):
        worker.run()
    assert worker.metrics.flush_failures_total == 3


def test_postgres_writer_sets_role_and_reconnects_after_error(monkeypatch):
    events = []

    class FakeConn:
        closed = False
        autocommit = True

        def execute(self, statement, *args):
            events.append(('execute', statement.as_string(None) if hasattr(statement, 'as_string')
                           else statement))

        def close(self):
            self.closed = True
            events.append(('close',))

    def connect(dsn, **kwargs):
        events.append(('connect', kwargs['application_name'], kwargs['autocommit']))
        return FakeConn()

    calls = []

    def fake_record(conn, batch, *, seen=None):
        calls.append(len(batch))
        if len(calls) == 1:
            raise WriteBroken('x')
        return WriteResult(len(batch), 0, 0)

    monkeypatch.setattr('edfinder_observe.worker.record_sightings', fake_record)
    writer = PostgresWriter(DSN, connect=connect)
    with pytest.raises(WriteBroken):
        writer([_sighting()])
    assert writer([_sighting()]) == WriteResult(1, 0, 0)
    connects = [e for e in events if e[0] == 'connect']
    assert connects == [('connect', 'edfinder-eddn-v3-observer', True)] * 2
    executed = [e[1] for e in events if e[0] == 'execute']
    assert executed[:3] == ['SET ROLE "v3_station_observer"', "SET lock_timeout = '2s'",
                            "SET statement_timeout = '15s'"]
    assert ('close',) in events


def test_metrics_http_endpoints():
    clock = FakeClock()
    metrics = Metrics(clock=lambda: clock.now)
    server = serve_metrics(metrics, '127.0.0.1', 0, 300)
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics', timeout=5) as response:
            body = response.read().decode()
            assert response.status == 200
        assert 'edfinder_eddn_v3_last_successful_flush_timestamp_seconds 0.0' in body
        assert 'edfinder_eddn_v3_consecutive_flush_failures 0' in body
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=5) as response:
            assert response.status == 200
        metrics.flush_failure(10)
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=5)
        exc.value.close()
        assert exc.value.code == 503
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/other', timeout=5)
        exc.value.close()
        assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_receive_queue_is_bounded_by_bytes_not_message_count():
    for limit in (1024, 262_144, 1_048_576):
        hwm = receive_high_water_mark(limit)
        assert hwm >= 1
        assert hwm * limit <= RECEIVE_QUEUE_BYTE_BUDGET
    assert receive_high_water_mark(262_144) == 512
    assert receive_high_water_mark(1_048_576) == 128
