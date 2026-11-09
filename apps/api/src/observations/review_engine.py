"""Compatibility import for the Stage 6E validation review engine.

Implementation lives in observations.review.engine so review guidance can
grow without becoming a monolith.
"""
from __future__ import annotations

from edfinder_api.observations.review.engine import build_validation_review

__all__ = ["build_validation_review"]
