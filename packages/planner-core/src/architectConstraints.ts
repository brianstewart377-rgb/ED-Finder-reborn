export type ArchitectPlanScale = 'bootstrap' | 'starter' | 'expansion' | 'full';

export type PrimaryPortPolicy =
  | 'no_preference'
  | 'anchor_only'
  | 'allow_main_station'
  | 'outpost_only';

export interface ArchitectConstraintSet {
  mustProduce: string[];
  prefer: string[];
  avoid: string[];
  mainStationBody: string | null;
  primaryPortPolicy: PrimaryPortPolicy;
  scale: ArchitectPlanScale | null;
  requiredStructures: string[];
  forbiddenStructures: string[];
  preserveExisting: boolean;
  maxWarnings: number | null;
}

const VALID_SCALES: ArchitectPlanScale[] = ['bootstrap', 'starter', 'expansion', 'full'];
const VALID_PRIMARY_PORT_POLICIES: PrimaryPortPolicy[] = ['no_preference', 'anchor_only', 'allow_main_station', 'outpost_only'];

export function emptyArchitectConstraints(): ArchitectConstraintSet {
  return {
    mustProduce: [],
    prefer: [],
    avoid: [],
    mainStationBody: null,
    primaryPortPolicy: 'no_preference',
    scale: null,
    requiredStructures: [],
    forbiddenStructures: [],
    preserveExisting: true,
    maxWarnings: null,
  };
}

export function normaliseArchitectConstraints(
  input: Partial<ArchitectConstraintSet> | null | undefined,
): ArchitectConstraintSet {
  const base = emptyArchitectConstraints();
  if (!input) return base;

  const scale = input.scale && VALID_SCALES.includes(input.scale) ? input.scale : null;
  const primaryPortPolicy = input.primaryPortPolicy && VALID_PRIMARY_PORT_POLICIES.includes(input.primaryPortPolicy)
    ? input.primaryPortPolicy
    : 'no_preference';

  return {
    mustProduce: compactStrings(input.mustProduce),
    prefer: compactStrings(input.prefer),
    avoid: compactStrings(input.avoid),
    mainStationBody: compactString(input.mainStationBody),
    primaryPortPolicy,
    scale,
    requiredStructures: compactStrings(input.requiredStructures),
    forbiddenStructures: compactStrings(input.forbiddenStructures),
    preserveExisting: input.preserveExisting !== false,
    maxWarnings: coerceMaxWarnings(input.maxWarnings),
  };
}

function compactStrings(values: unknown): string[] {
  if (!Array.isArray(values)) return [];
  const unique = new Set<string>();
  values.forEach((value) => {
    if (typeof value !== 'string') return;
    const trimmed = value.trim();
    if (!trimmed) return;
    unique.add(trimmed);
  });
  return Array.from(unique);
}

function compactString(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function coerceMaxWarnings(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  if (value < 0) return 0;
  return Math.floor(value);
}
