"""Spansh classification and deterministic main-star selection before COPY."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from v3_spansh.adapter import CanonicalBatch, SpanshAdapter
from v3_spansh.contracts import COPY_COLUMNS, finite_float, stable_int63

from .classification import BODY_TYPE_VOCABULARY, classify_body


def _main_flag(body: Mapping[str, Any]) -> bool | None:
    values = [body[key] for key in ("mainStar", "isMainStar", "is_main_star", "MainStar")
              if body.get(key) is not None]
    if any(type(value) is not bool for value in values) or len(set(values)) > 1:
        raise ValueError("main-star flags must be consistent JSON booleans")
    return values[0] if values else None


def _body_pk(system_id64: int, body: Mapping[str, Any]) -> int:
    return (int(body["id64"]) if body.get("id64") is not None else
            stable_int63("body", system_id64, body.get("bodyId"), body.get("name")))


def normalize_system(record: dict[str, Any]) -> tuple[dict[str, Any], set[tuple[str, str]]]:
    """Preserve source payloads, prefer explicit primary, never select a planet.

    Unflagged stars fall back to zero arrival distance, Frontier body 0, nearest
    nonnegative distance, then stable body identity. Explicit false is respected.
    Multiple explicit true flags use the same deterministic order.
    """
    bodies = [dict(body) for body in record.get("bodies", []) if isinstance(body, dict)]
    classifications = [classify_body(body) for body in bodies]
    identities = [_body_pk(int(record["id64"]), body) for body in bodies]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate Spansh body identity")
    unmapped = {item for classification in classifications for item in classification.unmapped}
    flags = [_main_flag(body) for body in bodies]
    for index, classification in enumerate(classifications):
        if flags[index] is True and classification.body_type_code != "star":
            raise ValueError("only a classified star can be the main star")
    stars = [index for index, classification in enumerate(classifications)
             if classification.body_type_code == "star" and flags[index] is not False]
    explicit = [index for index in stars if flags[index] is True]

    def rank(index: int) -> tuple:
        body = bodies[index]
        distance = finite_float(body.get("distanceToArrival"))
        if distance is not None and distance < 0:
            distance = None
        return (distance != 0, body.get("bodyId") != 0,
                distance if distance is not None else float("inf"), identities[index])

    selected = min(explicit or stars, key=rank) if stars else None
    if len(explicit) > 1:
        unmapped.add(("main_star_selection", "multiple explicit main stars"))
    for index, (body, classification) in enumerate(zip(bodies, classifications, strict=True)):
        if classification.body_type_code:
            body["type"] = BODY_TYPE_VOCABULARY[classification.body_type_code][1]
        body["spectralClass"] = classification.spectral_class
        body["luminosity"] = classification.luminosity_class
        body["mainStar"] = index == selected
    return {**record, "bodies": bodies}, unmapped


class ClassifiedSpanshAdapter(SpanshAdapter):
    """Reuse physical conversions, resolving body types by public code.

    A database writer must supply its resolved vocabulary IDs; defaults are the
    recovered baseline seed values and are useful for database-free dry runs.
    """

    def __init__(self, *, body_type_ids: Mapping[str, int] | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self.body_type_ids = dict(body_type_ids) if body_type_ids is not None else {
            code: entry[0] for code, entry in BODY_TYPE_VOCABULARY.items()
        }
        if set(BODY_TYPE_VOCABULARY) - self.body_type_ids.keys():
            raise ValueError("body_type vocabulary is missing required public codes")

    def adapt_system(self, record: dict[str, Any]) -> CanonicalBatch:
        normalized, unmapped = normalize_system(record)
        batch = super().adapt_system(normalized)
        batch.unmapped.update(unmapped)
        index = COPY_COLUMNS["bodies"].index("body_type_id")
        code_by_id = {entry[0]: code for code, entry in BODY_TYPE_VOCABULARY.items()}
        rows = []
        for original in batch.rows.get("bodies", []):
            row = list(original)
            if row[index] is not None:
                row[index] = self.body_type_ids[code_by_id[row[index]]]
            rows.append(tuple(row))
        batch.rows["bodies"] = rows
        return batch
