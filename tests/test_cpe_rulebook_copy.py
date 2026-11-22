"""The runtime rulebook is the exact pinned CRE release, with usable citations."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps' / 'api' / 'src'))

from edfinder_api.cpe import RULEBOOK_RELEASE, rule  # noqa: E402
from edfinder_api.cpe.explain import explain_rule  # noqa: E402


def test_runtime_rulebook_matches_its_asset_record():
    # The old repo kept a pin file and a second copy under docs/; this repo
    # keeps the pin in the rulebook's entry in assets/PROVENANCE.json.
    path = 'apps/api/src/cpe/data/rulebook.json'
    provenance = json.loads((ROOT / 'assets' / 'PROVENANCE.json').read_text(encoding='utf-8'))
    (entry,) = [item for item in provenance['entries'] if item['filename'] == path]
    runtime = (ROOT / path).read_bytes()

    assert hashlib.sha256(runtime).hexdigest() == entry['sha256']
    assert RULEBOOK_RELEASE == 'cre-knowledge-v0.5.0'
    assert f'({RULEBOOK_RELEASE})' in entry['attribution_text']


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def test_rules_preserve_values_status_and_first_source_citations():
    data = json.loads(
        (ROOT / 'apps' / 'api' / 'src' / 'cpe' / 'data' / 'rulebook.json').read_text(
            encoding='utf-8',
        )
    )
    for item in data['rules']:
        actual = rule(item['id'])
        assert actual.topic == item['topic']
        assert _plain(actual.value) == item['value']
        assert actual.quote == item['sources'][0]['quote']
        assert actual.page == item['sources'][0].get('page')
        assert explain_rule(item['id']) == {
            key: getattr(actual, key)
            for key in ('id', 'title', 'status', 'statement', 'quote', 'page')
        }
    assert rule('RB-0003').status == 'Extracted'
    with pytest.raises(FrozenInstanceError):
        rule('RB-0003').status = 'Confirmed'


def test_rule_values_are_deeply_immutable():
    tiers = rule('RB-0015').value
    with pytest.raises(TypeError):
        tiers['strong_by_tier'] = {}
    with pytest.raises(TypeError):
        tiers['strong_by_tier']['1'] = 99
    conditions = rule('RB-0018').value['economies']['Agriculture']['boosts']
    assert isinstance(conditions, tuple)
    with pytest.raises(TypeError):
        conditions[0] = {}
    with pytest.raises(TypeError):
        conditions[0]['condition'] = 'changed'
    with pytest.raises(FrozenInstanceError):
        rule('RB-0015').value = {}
    assert rule('RB-0015').value['strong_by_tier']['1'] == 0.4
    assert rule('RB-0018').value['economies']['Agriculture']['boosts'][0]['condition'] == 'orbiting_earth_like_world'


def test_unknown_rule_fails_closed():
    with pytest.raises(KeyError, match='RB-9999'):
        rule('RB-9999')
