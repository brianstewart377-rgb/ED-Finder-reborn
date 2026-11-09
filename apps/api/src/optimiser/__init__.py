"""Stage 5A optimiser candidate generation package.

This package contains bounded deterministic candidate generation and optional
candidate ranking. It does not compare alternatives for the UI, apply candidates,
or alter simulation mechanics.
"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from edfinder_api.optimiser.candidate_generator import generate_candidates
    from edfinder_api.optimiser.models import (
        CandidateGenerationRequest,
        CandidateGenerationResult,
        CandidateRankingResult,
    )
    from edfinder_api.optimiser.ranker import rank_candidates

__all__ = [
    'CandidateGenerationRequest',
    'CandidateGenerationResult',
    'CandidateRankingResult',
    'generate_candidates',
    'rank_candidates',
]


def __getattr__(name: str) -> Any:
    if name in {'CandidateGenerationRequest', 'CandidateGenerationResult', 'CandidateRankingResult'}:
        module = import_module('edfinder_api.optimiser.models')
        return getattr(module, name)
    if name == 'generate_candidates':
        module = import_module('edfinder_api.optimiser.candidate_generator')
        return getattr(module, name)
    if name == 'rank_candidates':
        module = import_module('edfinder_api.optimiser.ranker')
        return getattr(module, name)
    raise AttributeError(name)
