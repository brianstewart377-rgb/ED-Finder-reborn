"""Shared mechanics constants and standard labels.

Keep raw rule numbers here unless they are purely local formatting concerns.
This makes future changes reviewable and keeps simulation modules explainable.
"""
from __future__ import annotations


STANDARD_CONFIDENCE_LABELS = {
    'observed': 'Observed',
    'verified': 'Verified',
    'community_observed': 'Community observed',
    'inferred': 'Inferred',
    'estimated': 'Estimated',
    'speculative': 'Speculative',
    'unknown': 'Unknown',
}

UNKNOWN_RULE_LABEL = 'unknown'
UNSUPPORTED_RULE_LABEL = 'unsupported'
ESTIMATED_RULE_LABEL = 'estimated'
