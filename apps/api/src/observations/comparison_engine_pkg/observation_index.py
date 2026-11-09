"""Stage 6C comparison — observation indexing.

Buckets a list of ``PersistedObservedFact`` records by fact type (and,
for service/economy facts, also by subject) so the engine can match
each prediction subject to its observation(s) in O(1) and surface
remaining observations as ``observed_only`` afterwards.

The index is a private collaborator of the engine. Outside the
``observations.comparison_engine_pkg`` package it should be treated
as an implementation detail.
"""
from __future__ import annotations

from typing import Iterable

from edfinder_api.observations.models import (
    ObservedFactType,
    PersistedObservedFact,
)


class ObservationIndex:
    """Bucket observations by fact_type/subject so prediction walks are O(1).

    The buckets are split into:

    * ``_services`` / ``_economies`` — keyed by subject for pop-on-match.
    * ``_cp`` / ``_facilities`` / ``_build_outcomes`` /
      ``_prediction_match`` / ``_prediction_mismatch`` / ``_notes`` —
      flat lists, walked in full by the corresponding rule module.

    An unrecognised ``fact_type`` is bucketed into notes rather than
    dropped, so a future Stage 6A vocabulary extension can never silently
    disappear from a comparison run.
    """

    def __init__(self, facts: Iterable[PersistedObservedFact]):
        self._services: dict[str, list[PersistedObservedFact]] = {}
        self._economies: dict[str, list[PersistedObservedFact]] = {}
        self._cp: list[PersistedObservedFact] = []
        self._facilities: list[PersistedObservedFact] = []
        self._build_outcomes: list[PersistedObservedFact] = []
        self._prediction_match: list[PersistedObservedFact] = []
        self._prediction_mismatch: list[PersistedObservedFact] = []
        self._notes: list[PersistedObservedFact] = []

        for fact in facts:
            self._classify(fact)

    def _classify(self, fact: PersistedObservedFact) -> None:
        ft = fact.fact_type
        if ft == ObservedFactType.SERVICE_PRESENCE.value:
            key = fact.service_id or fact.subject_id or ''
            self._services.setdefault(key, []).append(fact)
        elif ft == ObservedFactType.ECONOMY_PRESENCE.value:
            key = fact.economy or fact.subject_id or ''
            self._economies.setdefault(key, []).append(fact)
        elif ft == ObservedFactType.CP_VALUE.value:
            self._cp.append(fact)
        elif ft == ObservedFactType.FACILITY_STATE.value:
            self._facilities.append(fact)
        elif ft == ObservedFactType.BUILD_OUTCOME.value:
            self._build_outcomes.append(fact)
        elif ft == ObservedFactType.PREDICTION_MATCH.value:
            self._prediction_match.append(fact)
        elif ft == ObservedFactType.PREDICTION_MISMATCH.value:
            self._prediction_mismatch.append(fact)
        elif ft == ObservedFactType.NOTE.value:
            self._notes.append(fact)
        else:
            # Unknown fact_type: surface as note-style observed_only so we
            # never silently drop it.
            self._notes.append(fact)

    def pop_service(self, service_id: str) -> list[PersistedObservedFact]:
        return self._services.pop(service_id, [])

    def services_remaining(self) -> dict[str, list[PersistedObservedFact]]:
        return self._services

    def pop_economy(self, name: str) -> list[PersistedObservedFact]:
        return self._economies.pop(name, [])

    def economies_remaining(self) -> dict[str, list[PersistedObservedFact]]:
        return self._economies

    def cp_facts(self) -> list[PersistedObservedFact]:
        return list(self._cp)

    def facility_facts(self) -> list[PersistedObservedFact]:
        return list(self._facilities)

    def build_outcome_facts(self) -> list[PersistedObservedFact]:
        return list(self._build_outcomes)

    def prediction_match_facts(self) -> list[PersistedObservedFact]:
        return list(self._prediction_match)

    def prediction_mismatch_facts(self) -> list[PersistedObservedFact]:
        return list(self._prediction_mismatch)

    def note_facts(self) -> list[PersistedObservedFact]:
        return list(self._notes)


def index_observations(facts: Iterable[PersistedObservedFact]) -> ObservationIndex:
    return ObservationIndex(facts)


__all__ = ['ObservationIndex', 'index_observations']
