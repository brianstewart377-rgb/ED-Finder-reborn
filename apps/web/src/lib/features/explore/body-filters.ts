import type { BodyFilters } from '$lib/api/client';

/** Keep only the named OpenAPI fields, excluding its additional-properties index. */
export type BodyFilterKey = keyof {
  [
    Key in keyof BodyFilters as string extends Key
      ? never
      : number extends Key
        ? never
        : Key
  ]: unknown;
};

export type BodyFilterRange = Readonly<{ min?: number; max?: number }>;
export type BodyFilterValues = Partial<Record<BodyFilterKey, BodyFilterRange>>;

export const HARD_BODY_COUNT_MAX = 999;

export type BodyFilterOption = Readonly<{
  key: BodyFilterKey;
  label: string;
  sliderMax: number;
}>;

export const BODY_FILTER_GROUPS = [
  {
    label: 'Planets',
    filters: [
      { key: 'elw_count', label: 'Earth-like', sliderMax: 20 },
      { key: 'ww_count', label: 'Water worlds', sliderMax: 20 },
      { key: 'ammonia_count', label: 'Ammonia worlds', sliderMax: 20 },
      { key: 'terraformable_count', label: 'Terraformable', sliderMax: 20 },
      { key: 'gas_giant_count', label: 'Gas giants', sliderMax: 20 },
      { key: 'hmc_count', label: 'High metal content', sliderMax: 20 },
      { key: 'metal_rich_count', label: 'Metal-rich', sliderMax: 20 },
      { key: 'rocky_count', label: 'Rocky', sliderMax: 20 },
      { key: 'rocky_ice_count', label: 'Rocky ice', sliderMax: 20 },
      { key: 'icy_count', label: 'Icy', sliderMax: 20 },
    ],
  },
  {
    label: 'Stars',
    filters: [
      { key: 'black_hole_count', label: 'Black holes', sliderMax: 10 },
      { key: 'neutron_count', label: 'Neutron stars', sliderMax: 10 },
      { key: 'white_dwarf_count', label: 'White dwarfs', sliderMax: 10 },
      { key: 'other_star_count', label: 'Other stars', sliderMax: 10 },
    ],
  },
  {
    label: 'Features',
    filters: [
      { key: 'landable_count', label: 'Landable', sliderMax: 50 },
      { key: 'walkable_count', label: 'Walkable', sliderMax: 50 },
      { key: 'ring_count', label: 'Rings', sliderMax: 50 },
      { key: 'bio_signal_total', label: 'Biological signals', sliderMax: 50 },
      { key: 'geo_signal_total', label: 'Geological signals', sliderMax: 50 },
    ],
  },
] as const satisfies readonly Readonly<{
  label: string;
  filters: readonly BodyFilterOption[];
}>[];

export const BODY_FILTER_OPTIONS: readonly BodyFilterOption[] =
  BODY_FILTER_GROUPS.flatMap((group) => [...group.filters]);

const BODY_FILTER_KEYS = new Set<string>(
  BODY_FILTER_OPTIONS.map(({ key }) => key),
);

export function isBodyFilterKey(value: string): value is BodyFilterKey {
  return BODY_FILTER_KEYS.has(value);
}

export function isBodyCount(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= HARD_BODY_COUNT_MAX
  );
}

/** Drop an invalid range in full; never turn a reversed range into a new filter. */
export function normalizeBodyFilters(input: unknown): BodyFilterValues {
  const output: BodyFilterValues = {};
  if (!input || typeof input !== 'object' || Array.isArray(input))
    return output;
  for (const { key } of BODY_FILTER_OPTIONS) {
    if (!Object.hasOwn(input, key)) continue;
    const range = (input as Record<string, unknown>)[key];
    if (!range || typeof range !== 'object' || Array.isArray(range)) continue;
    if (Object.keys(range).some((name) => name !== 'min' && name !== 'max'))
      continue;
    const { min, max } = range as Record<string, unknown>;
    if (
      (min !== undefined && !isBodyCount(min)) ||
      (max !== undefined && !isBodyCount(max)) ||
      (min === undefined && max === undefined) ||
      (typeof min === 'number' && typeof max === 'number' && min > max)
    )
      continue;
    output[key] = {
      ...(isBodyCount(min) ? { min } : {}),
      ...(isBodyCount(max) ? { max } : {}),
    };
  }
  return output;
}

export function bodyFilterLabel(
  key: BodyFilterKey,
  range: BodyFilterRange,
): string {
  const label = BODY_FILTER_OPTIONS.find((option) => option.key === key)!.label;
  if (range.min !== undefined && range.max !== undefined)
    return range.min === range.max
      ? `${label} = ${range.min}`
      : `${label} ${range.min}–${range.max}`;
  if (range.min !== undefined) return `${label} ≥ ${range.min}`;
  if (range.max !== undefined) return `${label} ≤ ${range.max}`;
  return label;
}
