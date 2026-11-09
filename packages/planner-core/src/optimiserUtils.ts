import type {
  OptimiserCandidate,
  OptimiserCandidatePlacement,
  OptimiserRanking,
  RankedOptimiserCandidate,
  SimulateBuildPlacement,
} from '@ed-finder/api-client/types';

export type RankLookup = Map<string, RankedOptimiserCandidate>;

export function buildRankLookup(ranking?: OptimiserRanking | null): RankLookup {
  const lookup: RankLookup = new Map();
  for (const item of ranking?.ranked_candidates ?? []) {
    lookup.set(item.candidate_id, item);
  }
  return lookup;
}

export function sortCandidatesForDisplay(
  candidates: OptimiserCandidate[],
  ranking?: OptimiserRanking | null,
): OptimiserCandidate[] {
  const lookup = buildRankLookup(ranking);
  if (lookup.size === 0) return [...candidates];

  const originalIndex = new Map(candidates.map((candidate, index) => [candidate.candidate_id, index]));
  return [...candidates].sort((a, b) => {
    const rankA = lookup.get(a.candidate_id)?.rank;
    const rankB = lookup.get(b.candidate_id)?.rank;
    if (rankA != null && rankB != null) return rankA - rankB;
    if (rankA != null) return -1;
    if (rankB != null) return 1;
    return (originalIndex.get(a.candidate_id) ?? 0) - (originalIndex.get(b.candidate_id) ?? 0);
  });
}

export function candidatePlacementsToPreviewPlacements(
  placements: OptimiserCandidatePlacement[],
): SimulateBuildPlacement[] {
  let primaryPortAssigned = false;
  return [...placements]
    .sort((a, b) => a.build_order - b.build_order)
    .map((placement, index) => {
      const isPrimaryPort = Boolean(placement.is_primary_port && !primaryPortAssigned);
      if (isPrimaryPort) {
        primaryPortAssigned = true;
      }
      return {
        facility_template_id: placement.facility_template_id,
        local_body_id: placement.local_body_id ?? null,
        is_primary_port: isPrimaryPort,
        build_order: index + 1,
      };
    });
}

export function formatScore(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toFixed(value % 1 === 0 ? 0 : 1);
}

export function formatPercent(value?: number | null): string {
  if (value == null || Number.isNaN(value)) return '—';
  const normalized = value <= 1 ? value * 100 : value;
  return `${Math.round(normalized)}%`;
}

export function rankTone(tier?: string | null): string {
  switch ((tier ?? '').toLowerCase()) {
    case 'excellent':
      return 'border-cyan/45 bg-cyan/10 text-cyan';
    case 'strong':
      return 'border-green/45 bg-green/10 text-green';
    case 'viable':
      return 'border-orange/45 bg-orange/10 text-orange';
    case 'risky':
      return 'border-gold/45 bg-gold/10 text-gold';
    case 'weak':
      return 'border-red/45 bg-red/10 text-red';
    default:
      return 'border-border bg-bg3 text-silver-dk';
  }
}

export function strategyLabel(strategy: string): string {
  return strategy.replace(/_/g, ' ');
}
