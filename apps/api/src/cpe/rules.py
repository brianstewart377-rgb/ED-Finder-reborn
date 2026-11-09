"""Read the planner's pinned CRE rules and their first source citations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class Rule:
    id: str
    topic: str
    title: str
    status: str
    statement: str
    quote: str
    page: int | None
    value: Mapping[str, Any] | None = None


def _freeze(value: Any) -> Any:
    """Keep cached structured rule values read-only at every depth."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@cache
def _rulebook() -> tuple[str, dict[str, Rule]]:
    path = Path(__file__).parent / 'data' / 'rulebook.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    rules = {}
    for item in data['rules']:
        source = item['sources'][0]
        rules[item['id']] = Rule(
            id=item['id'],
            topic=item['topic'],
            title=item['title'],
            status=item['status'],
            statement=item['statement'],
            quote=source['quote'],
            page=source.get('page'),
            value=_freeze(item['value']),
        )
    return data['release_tag'], rules


RULEBOOK_RELEASE = _rulebook()[0]


def rule(rule_id: str) -> Rule:
    """Return a pinned rule; unknown identifiers raise KeyError."""
    return _rulebook()[1][rule_id]
