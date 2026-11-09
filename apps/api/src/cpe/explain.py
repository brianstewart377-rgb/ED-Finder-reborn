"""JSON-friendly explanations from the rulebook citations attached to steps."""

from __future__ import annotations

from typing import Protocol

from .rules import rule


class _RuleStep(Protocol):
    rule_ids: tuple[str, ...]


def explain_rule(rule_id: str) -> dict[str, str | int | None]:
    """Explain a rule with its original first-source quote and page."""
    item = rule(rule_id)
    return {
        'id': item.id,
        'title': item.title,
        'status': item.status,
        'statement': item.statement,
        'quote': item.quote,
        'page': item.page,
    }


def explain_step(step: _RuleStep) -> list[dict[str, str | int | None]]:
    """Explain each rule cited by an evaluated step, preserving its order."""
    return [explain_rule(rule_id) for rule_id in step.rule_ids]
