/**
 * Canonical V3 archetype metadata and tier/evidence presentation helpers for
 * the Finder (F4a, docs/superpowers/specs/2026-10-10-v3-finder-f4-archetype-ui-design.md
 * sections 5 and 6).
 *
 * The eight keys mirror `apps/api/src/ranking/profile.py` `ARCHETYPE_KEYS`
 * and the router's `_V3_ARCHETYPE_LABELS`; the one-line descriptions are
 * derived from the archetype model's anchors. Tier thresholds are API-owned
 * (S ≥ 88, A ≥ 76, B ≥ 60, C ≥ 45, else D): this module never derives a tier
 * from a score — it only maps a selected-mode *minimum* tier to the floor the
 * rankings route accepts, and labels tiers the API already supplied.
 */

export const ARCHETYPE_KEYS = [
  'paradise',
  'mining_hub',
  'manufacturing_hub',
  'megacomplex',
  'research_hub',
  'stronghold',
  'population_capital',
  'flexible',
] as const;

export type ArchetypeKey = (typeof ARCHETYPE_KEYS)[number];

export type ArchetypeMetadata = Readonly<{
  key: ArchetypeKey;
  label: string;
  description: string;
}>;

export const ARCHETYPES: readonly ArchetypeMetadata[] = [
  {
    key: 'paradise',
    label: 'Paradise World',
    description:
      'Agriculture and Tourism working together for a habitable destination.',
  },
  {
    key: 'mining_hub',
    label: 'Mining Hub',
    description:
      'Extraction and Refinery strength for a resource-processing centre.',
  },
  {
    key: 'manufacturing_hub',
    label: 'Manufacturing Hub',
    description: 'Refinery and Industrial strength for sustained production.',
  },
  {
    key: 'megacomplex',
    label: 'Megacomplex',
    description: 'A complete Extraction, Refinery and Industrial chain.',
  },
  {
    key: 'research_hub',
    label: 'Research Hub',
    description: 'High Tech potential supported by Industrial capacity.',
  },
  {
    key: 'stronghold',
    label: 'Stronghold',
    description: 'Military and Industrial strength for a defended base.',
  },
  {
    key: 'population_capital',
    label: 'Population Capital',
    description:
      'Agriculture and High Tech supporting a large population centre.',
  },
  {
    key: 'flexible',
    label: 'Flexible Multi-Role Colony',
    description: 'Several viable economies rather than one narrow specialism.',
  },
];

/** The picker's first option: no archetype chosen. */
export const ANY_ARCHETYPE = 'any' as const;
export const ANY_ARCHETYPE_LABEL = 'Any';
export const ANY_ARCHETYPE_DESCRIPTION =
  'Show the best overall colony opportunities';

const METADATA_BY_KEY: ReadonlyMap<ArchetypeKey, ArchetypeMetadata> = new Map(
  ARCHETYPES.map((entry) => [entry.key, entry]),
);

export function isArchetypeKey(value: unknown): value is ArchetypeKey {
  return (
    typeof value === 'string' &&
    (ARCHETYPE_KEYS as readonly string[]).includes(value)
  );
}

/** Fail-closed key parser: anything but one of the eight keys is `null`. */
export function parseArchetypeKey(value: unknown): ArchetypeKey | null {
  return isArchetypeKey(value) ? value : null;
}

export function archetypeMetadata(key: ArchetypeKey): ArchetypeMetadata {
  const entry = METADATA_BY_KEY.get(key);
  if (!entry) throw new RangeError(`Unknown archetype key: ${key}`);
  return entry;
}

/**
 * Canonical label for a V3 key. A historical/unknown key (for example on an
 * older saved snapshot) is returned as a readable fallback rather than being
 * passed off as a V3 archetype; `null`/empty yields `null`.
 */
export function archetypeLabel(key: string | null | undefined): string | null {
  if (key == null || key === '') return null;
  if (isArchetypeKey(key)) return archetypeMetadata(key).label;
  return key.replace(/_/g, ' ');
}

// ---------------------------------------------------------------------------
// Tiers
// ---------------------------------------------------------------------------

export const TIERS = ['S', 'A', 'B', 'C', 'D'] as const;
export type Tier = (typeof TIERS)[number];

export function isTier(value: unknown): value is Tier {
  return (
    typeof value === 'string' && (TIERS as readonly string[]).includes(value)
  );
}

/** Visible tier text always carries the word so colour is never the only cue. */
export function tierLabel(tier: Tier): string {
  return `Tier ${tier}`;
}

/**
 * Selected-archetype minimum-tier floors accepted by `/api/archetypes/rankings`
 * (`min_score` is `Literal[60, 76, 88]`). C and D are outside the initial
 * product scope and are rejected, not mapped.
 */
export const SELECTED_MINIMUM_TIERS = ['S', 'A', 'B'] as const;
export type SelectedMinimumTier = (typeof SELECTED_MINIMUM_TIERS)[number];

export const MINIMUM_TIER_SCORES: Readonly<
  Record<SelectedMinimumTier, 60 | 76 | 88>
> = { S: 88, A: 76, B: 60 };

export type MinimumScore = (typeof MINIMUM_TIER_SCORES)[SelectedMinimumTier];

/** Selected mode defaults to B (60), not the endpoint's non-tier legacy 40. */
export const DEFAULT_MINIMUM_TIER: SelectedMinimumTier = 'B';

export function isSelectedMinimumTier(
  value: unknown,
): value is SelectedMinimumTier {
  return (
    typeof value === 'string' &&
    (SELECTED_MINIMUM_TIERS as readonly string[]).includes(value)
  );
}

/** Fail-closed: `C`, `D`, lower-case, blanks and unknown values are `null`. */
export function parseMinimumTier(value: unknown): SelectedMinimumTier | null {
  return isSelectedMinimumTier(value) ? value : null;
}

export function minimumScoreForTier(tier: SelectedMinimumTier): MinimumScore {
  return MINIMUM_TIER_SCORES[tier];
}

// ---------------------------------------------------------------------------
// Honest uncertainty wording (presentation decision; unit tested; never alters
// ranking). The word "fit" is forbidden in Any mode because no archetype was
// picked.
// ---------------------------------------------------------------------------

export type RankingMode = 'any' | 'selected';

/** Owner decision 2026-10-31: selected score versus the API's best rival. */
export const CLEAR_FIT_MINIMUM_GAP = 3;

/**
 * The caller renders this only for a committed selected-archetype ranking.
 * Missing or unrecognised evidence cannot establish a meaningful comparison.
 * The returned text is both the visible and accessible label.
 */
export function selectedFitLabel(
  score: number | null | undefined,
  rivalArchetype: string | null | undefined,
  rivalScore: number | null | undefined,
): string | null {
  if (
    typeof score !== 'number' ||
    !Number.isFinite(score) ||
    typeof rivalScore !== 'number' ||
    !Number.isFinite(rivalScore) ||
    !isArchetypeKey(rivalArchetype)
  )
    return null;
  return score - rivalScore >= CLEAR_FIT_MINIMUM_GAP
    ? 'Clear fit'
    : `Close fit · ${archetypeMetadata(rivalArchetype).label} ${rivalScore}`;
}

export type EvidenceLevel = 'unknown' | 'limited' | 'moderate' | 'strong';

export type EvidenceBadge = Readonly<{
  level: EvidenceLevel;
  /** Visible badge text, e.g. "Evidence confidence: strong". */
  label: string;
  /** Accessible description with both raw percentages. */
  description: string;
}>;

export const EVIDENCE_LIMITED_BELOW = 0.5;
export const EVIDENCE_STRONG_AT_LEAST = 0.8;

const finiteRatio = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value);

export function evidenceLevel(
  confidence: number | null | undefined,
  completeness: number | null | undefined,
): EvidenceLevel {
  if (!finiteRatio(confidence) || !finiteRatio(completeness)) return 'unknown';
  if (
    confidence < EVIDENCE_LIMITED_BELOW ||
    completeness < EVIDENCE_LIMITED_BELOW
  )
    return 'limited';
  if (
    confidence >= EVIDENCE_STRONG_AT_LEAST &&
    completeness >= EVIDENCE_STRONG_AT_LEAST
  )
    return 'strong';
  return 'moderate';
}

const percent = (value: number | null | undefined): string =>
  finiteRatio(value) ? `${Math.round(value * 100)}%` : 'unknown';

export function evidenceBadge(
  mode: RankingMode,
  confidence: number | null | undefined,
  completeness: number | null | undefined,
): EvidenceBadge {
  const noun = mode === 'selected' ? 'Fit confidence' : 'Evidence confidence';
  const level = evidenceLevel(confidence, completeness);
  const label = level === 'unknown' ? `${noun} unknown` : `${noun}: ${level}`;
  const confidencePart = `${noun} ${percent(confidence)}`;
  const completenessPart = finiteRatio(completeness)
    ? `evidence ${percent(completeness)} complete`
    : 'evidence completeness unknown';
  return {
    level,
    label,
    description: `${confidencePart}; ${completenessPart}`,
  };
}
