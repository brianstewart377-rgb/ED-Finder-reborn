import {
  snapshotRankingIdentity,
  type CompareEntry,
} from '$lib/persistence/storage';
import {
  archetypeLabel,
  isArchetypeKey,
  isTier,
  tierLabel,
} from './archetypes';

export const COMPARE_MAX = 6;
const UNKNOWN = 'Unknown';

export interface CompareCell {
  display: string;
  csvValue: string | number;
  best: boolean;
}

export interface CompareMetricRow {
  label: string;
  cells: CompareCell[];
}

export interface CompareMetricOptions {
  finderF4Enabled?: boolean;
}

// The default-off surface retains its released presentation. F4 never uses
// this historical mapping or derives an API-owned tier from a score.
const legacyArchetypeLabels: Readonly<Record<string, string>> = {
  refinery_industrial: 'Refinery / Industrial Megacomplex',
  extraction_refinery: 'Extraction / Refinery Mining Hub',
  agriculture_terraforming: 'Agriculture / Terraforming Colony',
  hitech_tourism: 'High Tech / Tourism Prestige Colony',
  expansion_capital: 'Expansion Capital',
  trade_logistics: 'Trade / Logistics Hub',
  population_capital: 'Population Capital',
  ax_forward_base: 'AX Forward Operating Base',
  military_industrial: 'Military / Industrial Complex',
  flexible_multirole: 'Flexible Multi-Role Colony',
};

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null;
}

function legacyArchetype(value: unknown): string {
  const key = text(value);
  return key
    ? (legacyArchetypeLabels[key] ??
        key
          .split(/[_-]+/)
          .filter(Boolean)
          .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
          .join(' '))
    : UNKNOWN;
}

function legacyDevelopment(value: number | null): string {
  if (value === null) return UNKNOWN;
  const tier =
    value >= 88
      ? 'S'
      : value >= 76
        ? 'A'
        : value >= 60
          ? 'B'
          : value >= 45
            ? 'C'
            : 'D';
  return `${tier} ${value}`;
}

const SEMANTIC_PROVENANCE_FIELDS = [
  'ranking_version',
  'ranking_sha256',
  'archetype_version',
  'archetype_model_sha256',
] as const;

/** Data refreshes change freshness, not the four score-semantics identities. */
export function comparableOverallScores(
  entries: readonly CompareEntry[],
): boolean {
  const identities = entries.map(snapshotRankingIdentity);
  if (identities.some((identity) => identity.score_kind === 'unknown'))
    return false;
  const provenance = identities.map((identity) => identity.ranking_provenance);
  const first = provenance[0];
  return Boolean(
    first &&
    SEMANTIC_PROVENANCE_FIELDS.every((field) => text(first[field])) &&
    provenance.every(
      (value) =>
        value &&
        SEMANTIC_PROVENANCE_FIELDS.every(
          (field) => value[field] === first[field],
        ),
    ),
  );
}

function comparableSelectedArchetype(
  entries: readonly CompareEntry[],
): string | null {
  const identities = entries.map(snapshotRankingIdentity);
  const first = identities[0];
  return first?.score_kind === 'selected_fit' &&
    isArchetypeKey(first.selected_archetype) &&
    identities.every(
      (identity) =>
        identity.score_kind === 'selected_fit' &&
        identity.selected_archetype === first.selected_archetype,
    )
    ? first.selected_archetype
    : null;
}

function comparableDistance(entries: readonly CompareEntry[]): boolean {
  const references = entries.map(
    (entry) => snapshotRankingIdentity(entry).distance_reference,
  );
  const first = references[0];
  return Boolean(
    first &&
    references.every(
      (reference) =>
        reference?.kind === first.kind && reference.id64 === first.id64,
    ),
  );
}

export function distanceReferenceLabel(entry: CompareEntry): string | null {
  const reference = snapshotRankingIdentity(entry).distance_reference;
  if (!reference) return null;
  return `Distance from ${reference.kind === 'sol' ? 'Sol' : (text(reference.name) ?? reference.id64)}`;
}

/** A reference proves a measurement, including zero; no reference is unknown. */
export function snapshotDistance(entry: CompareEntry): number | null {
  return snapshotRankingIdentity(entry).distance_reference
    ? finite(entry.distance)
    : null;
}

function apiScore(value: number | null, tier: unknown): string {
  if (value === null) return UNKNOWN;
  return `${value}${isTier(tier) ? ` · ${tierLabel(tier)}` : ''}`;
}

/** Any-mode ranking tiers belong to overall potential, never selected fit. */
function snapshotOverallTier(entry: CompareEntry): unknown {
  const identity = snapshotRankingIdentity(entry);
  return identity.score_kind === 'overall_potential'
    ? (identity.ranking_tier ?? entry.best_tier)
    : entry.best_tier;
}

/** Stored values remain readable even when they cannot enter a comparison. */
export function snapshotScoreSummary(entry: CompareEntry): string {
  const identity = snapshotRankingIdentity(entry);
  const parts: string[] = [];
  const overall = finite(entry.overall_development_potential);
  if (overall !== null)
    parts.push(
      `Best Colony Potential ${apiScore(overall, snapshotOverallTier(entry))}`,
    );
  if (identity.score_kind === 'selected_fit') {
    const label = archetypeLabel(text(entry.selected_archetype));
    parts.push(
      `${label ? `${label}${isArchetypeKey(entry.selected_archetype) ? '' : ' (historical archetype)'} fit` : 'Stored selected fit'} ${apiScore(identity.ranking_score, identity.ranking_tier)}`,
    );
  } else if (overall === null && identity.ranking_score !== null) {
    parts.push(`Stored ranking score ${identity.ranking_score}`);
  }
  if (parts.length === 0) {
    const stored = finite(entry.archetype_score);
    parts.push(`Stored score ${stored === null ? UNKNOWN : stored}`);
  }
  if (!identity.ranking_provenance || identity.score_kind === 'unknown')
    parts.push('Older save: ranking mode or provenance unknown');
  return parts.join('; ');
}

export function snapshotProvenanceSummary(entry: CompareEntry): string | null {
  const provenance = snapshotRankingIdentity(entry).ranking_provenance;
  if (!provenance) return null;
  return `Ranked on generation ${provenance.derived_generation_id}; publication ${provenance.publication_sequence}; archetype manifest ${provenance.archetype_manifest_sha256}`;
}

export function snapshotCapturedAt(entry: CompareEntry): string | null {
  const value =
    entry.captured_at === undefined ? entry.pinned_at : entry.captured_at;
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
    ? new Date(value).toISOString()
    : null;
}

export function formatCaptureTime(value: unknown): string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
    ? `${new Date(value).toISOString().slice(0, 19).replace('T', ' ')} UTC`
    : UNKNOWN;
}

export function buildCompareNotes(
  entries: readonly CompareEntry[],
  options?: CompareMetricOptions,
): string[] {
  if (!options?.finderF4Enabled || entries.length === 0) return [];
  const identities = entries.map(snapshotRankingIdentity);
  const notes: string[] = [];
  if (
    identities.some(
      (identity) =>
        !identity.ranking_provenance || identity.score_kind === 'unknown',
    )
  )
    notes.push(
      'Older save: ranking mode or provenance unknown. Stored scores remain visible.',
    );
  if (!comparableOverallScores(entries))
    notes.push(
      identities.some((identity) => identity.score_kind === 'unknown')
        ? 'Score comparison omitted: ranking mode unknown.'
        : 'Score comparison omitted: different ranking versions or missing ranking provenance.',
    );
  if (
    identities.some((identity) => identity.score_kind === 'selected_fit') &&
    !comparableSelectedArchetype(entries)
  )
    notes.push('Selected-fit comparison omitted: different ranking modes.');
  if (!comparableDistance(entries))
    notes.push(
      'Distance comparison omitted: different or unknown distance references.',
    );
  return notes;
}

function population(entry: CompareEntry): string {
  const value = finite(entry.population);
  if (value === null || value < 0) return 'Unknown';
  if (value === 0) {
    return entry.is_colonised === true || entry.is_being_colonised === true
      ? 'Population unknown'
      : 'Uninhabited';
  }
  if (value >= 1_000_000_000) return `${(value / 1e9).toFixed(1)}B`;
  if (value >= 1_000_000) return `${(value / 1e6).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1e3).toFixed(1)}K`;
  return String(value);
}

function status(entry: CompareEntry): string {
  const inhabitants = finite(entry.population);
  if (entry.is_colonised === true || (inhabitants !== null && inhabitants > 0))
    return 'Colonised';
  if (entry.is_being_colonised === true) return 'Colonising';
  if (entry.is_colonised === false || inhabitants === 0) return 'Available';
  return 'Unknown';
}

/** Normalize legacy snapshots at the display boundary without changing stored data. */
export function buildCompareRows(
  entries: readonly CompareEntry[],
  options?: CompareMetricOptions,
): CompareMetricRow[] {
  const finderF4Enabled = options?.finderF4Enabled ?? false;
  const plain = (
    label: string,
    render: (entry: CompareEntry) => string,
  ): CompareMetricRow => ({
    label,
    cells: entries.map((entry) => {
      const display = render(entry);
      return { display, csvValue: display, best: false };
    }),
  });
  const numeric = (
    label: string,
    extract: (entry: CompareEntry) => unknown,
    render: (value: number | null, entry: CompareEntry) => string = (value) =>
      value === null ? UNKNOWN : String(value),
    higherIsBetter = true,
  ): CompareMetricRow => {
    const values = entries.map((entry) => finite(extract(entry)));
    const known = values.filter((value): value is number => value !== null);
    // One measured value cannot establish a winner against unknowns. All-equal
    // values need no annotation; tied best values otherwise receive equal credit.
    const hasComparison = known.length >= 2 && new Set(known).size > 1;
    const best = hasComparison
      ? higherIsBetter
        ? Math.max(...known)
        : Math.min(...known)
      : null;
    return {
      label,
      cells: values.map((value, index) => ({
        display: render(value, entries[index]),
        csvValue: value ?? UNKNOWN,
        best: value !== null && best !== null && value === best,
      })),
    };
  };
  const count = (label: string, key: string) =>
    numeric(label, (entry) => {
      const value = finite(entry[key]);
      return value !== null && value >= 0 ? value : null;
    });

  const selected = comparableSelectedArchetype(entries);
  const scoresComparable = comparableOverallScores(entries);
  const labelArchetype = finderF4Enabled
    ? (value: unknown) => archetypeLabel(text(value)) ?? UNKNOWN
    : legacyArchetype;

  return [
    plain('Captured at', (entry) =>
      formatCaptureTime(snapshotCapturedAt(entry)),
    ),
    ...(finderF4Enabled
      ? [
          ...(scoresComparable
            ? [
                numeric(
                  'Best Colony Potential',
                  (entry) => entry.overall_development_potential,
                  (value, entry) => apiScore(value, snapshotOverallTier(entry)),
                ),
              ]
            : []),
          ...(scoresComparable && selected
            ? [
                numeric(
                  `${archetypeLabel(selected)} fit`,
                  (entry) => snapshotRankingIdentity(entry).ranking_score,
                  (value, entry) =>
                    apiScore(
                      value,
                      snapshotRankingIdentity(entry).ranking_tier,
                    ),
                ),
              ]
            : []),
        ]
      : [
          numeric(
            'Development score',
            (entry) =>
              finite(entry.archetype_score) ??
              finite(entry.overall_development_potential),
            legacyDevelopment,
          ),
        ]),
    plain('Primary archetype', (entry) =>
      labelArchetype(entry.primary_archetype),
    ),
    plain('Secondary archetype', (entry) =>
      labelArchetype(entry.secondary_archetype),
    ),
    plain('Archetype confidence', (entry) => {
      const confidence = finite(entry.archetype_confidence);
      return confidence !== null && confidence >= 0 && confidence <= 1
        ? `${Math.round(confidence * 100)}%`
        : UNKNOWN;
    }),
    numeric('Buildability', (entry) => entry.buildability_score),
    numeric('Purity', (entry) => entry.purity_score),
    count('Estimated slots', 'est_total_slots'),
    plain(
      'Primary economy',
      (entry) =>
        text(entry.primaryEconomy) ?? text(entry.primary_economy) ?? UNKNOWN,
    ),
    ...(!finderF4Enabled || comparableDistance(entries)
      ? [
          numeric(
            finderF4Enabled
              ? distanceReferenceLabel(entries[0])!
              : 'Distance from ref',
            (entry) => {
              if (finderF4Enabled) return snapshotDistance(entry);
              const distance = finite(entry.distance);
              // Legacy searches can use zero when no reference was chosen.
              return distance !== null && distance > 0 ? distance : null;
            },
            (value) => (value === null ? UNKNOWN : `${value.toFixed(2)} LY`),
            false,
          ),
        ]
      : []),
    ...(entries.some((entry) => 'distance_captured_at' in entry)
      ? [
          plain('Distance captured at', (entry) =>
            formatCaptureTime(
              'distance_captured_at' in entry
                ? entry.distance_captured_at
                : snapshotCapturedAt(entry),
            ),
          ),
        ]
      : []),
    plain('Population', population),
    plain('Status', status),
    plain(
      'Main star',
      (entry) =>
        text(entry.main_star_subtype) ?? text(entry.main_star_type) ?? UNKNOWN,
    ),
    plain('Security', (entry) => text(entry.security) ?? UNKNOWN),
    plain('Allegiance', (entry) => text(entry.allegiance) ?? UNKNOWN),
    count('ELW', 'elw_count'),
    count('Water worlds', 'ww_count'),
    count('Ammonia', 'ammonia_count'),
    count('Terraformable', 'terraformable_count'),
    count('Landable', 'landable_count'),
    count('Bio signals', 'bio_signal_total'),
    count('Geo signals', 'geo_signal_total'),
  ];
}

export function compareCsv(
  entries: readonly CompareEntry[],
  options?: CompareMetricOptions,
): string {
  const quote = (value: string | number) => {
    const safe =
      typeof value === 'string' && /^[=+\-@\t\r]/.test(value)
        ? `'${value}`
        : String(value);
    return `"${safe.replace(/"/g, '""')}"`;
  };
  return [
    ['Metric', ...entries.map((entry) => entry.name)],
    ...buildCompareRows(entries, options).map((row) => [
      row.label.startsWith('Distance from ') ? `${row.label} LY` : row.label,
      ...row.cells.map((cell) => cell.csvValue),
    ]),
  ]
    .map((row) => row.map(quote).join(','))
    .join('\r\n');
}
