"""JSON-friendly explanations from the rulebook citations attached to steps."""

from __future__ import annotations


from .rules import rule


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


