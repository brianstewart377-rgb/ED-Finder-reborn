"""Compatibility re-exports for colonisation mechanics constants.

New code should import from ``mechanics.*``. This module remains so older
domain/simulation imports do not break while the mechanics layer is hardened.
"""
from __future__ import annotations

from edfinder_api.mechanics.link_rules import MIN_STRONG_LINK_MODIFIER, STRONG_LINK_BY_TIER, WEAK_LINK_STRENGTH

__all__ = ['MIN_STRONG_LINK_MODIFIER', 'STRONG_LINK_BY_TIER', 'WEAK_LINK_STRENGTH']
