import type { SystemBody } from '@ed-finder/api-client/types';
import type { CoreEconomyName } from './economyVisuals';

const PRIMARY_BASE_WEIGHT = 1.0;
const SECONDARY_BASE_WEIGHT = 0.8;
const MODIFIER_ECONOMY_WEIGHT = 0.45;
const KNOWN_ECONOMIES = new Set<CoreEconomyName>([
  'Agriculture',
  'Refinery',
  'Industrial',
  'HighTech',
  'Military',
  'Tourism',
  'Extraction',
]);

export interface StationBaselineEconomySegment {
  economy: CoreEconomyName;
  percent: number;
  source: 'base' | 'modifier';
}

export interface StationBaselineEconomy {
  segments: StationBaselineEconomySegment[];
  baseEconomies: CoreEconomyName[];
  modifierEconomies: CoreEconomyName[];
  strategicTags: string[];
  confidence: number;
  caveats: string[];
  calculationSource: string;
  unavailableReason: string | null;
}

export function calculateStationBaselineEconomy(body: SystemBody | null | undefined): StationBaselineEconomy {
  if (!body) {
    return unavailableBaseline('Body context is unavailable.');
  }

  const profile = profileBodyForStationBaseline(body);
  const weights = bodyProfileEconomyWeights(profile.baseEconomies, profile.modifierEconomies);
  const total = Object.values(weights).reduce((sum, value) => sum + value, 0);
  const segments = total > 0
    ? Object.entries(weights)
      .map(([economy, value]) => ({
        economy: economy as CoreEconomyName,
        percent: roundPercent((value / total) * 100),
        source: profile.baseEconomies.includes(economy as CoreEconomyName) ? 'base' as const : 'modifier' as const,
      }))
      .sort((left, right) => right.percent - left.percent)
    : [];

  if (segments.length === 0) {
    return {
      ...profile,
      segments,
      calculationSource: 'ED-Finder Mega Guide body economy profile v1',
      unavailableReason: 'No documented body-to-economy rule matched this body.',
    };
  }

  return {
    ...profile,
    segments,
    calculationSource: 'ED-Finder Mega Guide body economy profile v1',
    unavailableReason: null,
  };
}

export function describeStationBaselineEconomy(
  baseline: StationBaselineEconomy,
  { projected = false }: { projected?: boolean } = {},
): string {
  if (baseline.segments.length === 0) {
    return [
      'Inherited/contextual baseline unavailable.',
      baseline.unavailableReason,
      'Run Preview for validated outcome.',
    ].filter(Boolean).join(' ');
  }

  const prefix = projected ? 'Projected inherited/contextual baseline' : 'Inherited/contextual baseline';
  const parts = baseline.segments.map((segment) => `${segment.economy} ${formatPercent(segment.percent)}`);
  return [
    `${prefix}: ${parts.join(' / ')}.`,
    `Source: ${baseline.calculationSource}.`,
    `Confidence ${Math.round(baseline.confidence * 100)}%.`,
    baseline.caveats.length > 0 ? `Caveats: ${baseline.caveats.join(' ')}` : null,
    'Run Preview for validated outcome.',
  ].filter(Boolean).join(' ');
}

function profileBodyForStationBaseline(body: SystemBody): Omit<StationBaselineEconomy, 'segments' | 'calculationSource' | 'unavailableReason'> {
  const record = body as SystemBody & Record<string, unknown>;
  const subtype = bodySubtype(record);
  const bodyType = String(record.body_type ?? '').toLowerCase();
  const base: CoreEconomyName[] = [];
  const modifiers: CoreEconomyName[] = [];
  const strategicTags: string[] = [];
  const caveats: string[] = [];
  const confidence = clampConfidence(readNumber(record.confidence) ?? 0.55);

  const isRinged = readBoolean(record.is_ringed) || readBoolean(record.has_rings);
  const hasBio = readBoolean(record.has_bio) || (readNumber(record.bio_signal_count) ?? 0) > 0;
  const hasGeo = readBoolean(record.has_geo) || (readNumber(record.geo_signal_count) ?? 0) > 0 || Boolean(record.volcanism);
  const isTerraformable = readBoolean(record.is_terraformable)
    || String(record.terraform_state ?? record.terraforming_state ?? '').toLowerCase().includes('terraform');
  const isLandable = readBoolean(record.is_landable);

  if (isRinged) strategicTags.push('ringed');
  if (hasBio) strategicTags.push('bio');
  if (hasGeo) strategicTags.push('geological');
  if (isTerraformable) strategicTags.push('terraforming_candidate');
  if (isLandable) strategicTags.push('landable');

  if (subtype.includes('earth-like') || subtype.includes('earthlike') || readBoolean(record.is_earth_like)) {
    base.push('Tourism', 'HighTech', 'Agriculture', 'Military');
    strategicTags.push('elw_mixed');
    caveats.push('ELW is mixed economy: Agriculture, HighTech, Military, and Tourism; not Industrial.');
  } else if (subtype.includes('water world') || readBoolean(record.is_water_world)) {
    base.push('Tourism', 'Agriculture');
  } else if (subtype.includes('ammonia') || readBoolean(record.is_ammonia_world)) {
    base.push('Tourism');
    modifiers.push('HighTech');
    strategicTags.push('exotic');
    caveats.push('Ammonia HighTech value is treated as exotic/supporting, not a pure base economy.');
  } else if (subtype.includes('gas giant')) {
    base.push('HighTech', 'Industrial');
  } else if (subtype.includes('black hole') || subtype.includes('neutron') || subtype.includes('white dwarf') || bodyType === 'star') {
    if (subtype.includes('black hole') || subtype.includes('neutron') || subtype.includes('white dwarf')) {
      base.push('Tourism', 'HighTech');
      strategicTags.push('exotic');
    }
  } else if (subtype.includes('high metal content')) {
    base.push('Extraction');
    if (hasGeo) modifiers.push('Industrial');
    if (hasBio) {
      modifiers.push('Agriculture');
      strategicTags.push('terraforming_pressure');
    }
    if (isTerraformable) strategicTags.push('terraforming_candidate');
  } else if (subtype.includes('metal rich') || subtype.includes('metal-rich')) {
    base.push('Extraction');
  } else if (subtype.includes('rocky ice') || subtype.includes('rocky-ice')) {
    base.push('Industrial', 'Refinery');
  } else if (subtype.includes('rocky')) {
    base.push('Refinery');
    if (isRinged) modifiers.push('Extraction');
    if (hasBio) {
      modifiers.push('Agriculture');
      strategicTags.push('terraforming_pressure');
    }
    if (hasGeo) modifiers.push('Industrial', 'Extraction');
  } else if (subtype.includes('icy')) {
    base.push('Industrial');
  }

  const baseEconomies = uniqueEconomies(base);
  const modifierEconomies = uniqueEconomies(modifiers).filter((economy) => !baseEconomies.includes(economy));
  const finalCaveats = [...caveats];
  const finalConfidence = baseEconomies.length || modifierEconomies.length
    ? confidence
    : Math.min(confidence, 0.35);
  if (!baseEconomies.length && !modifierEconomies.length) {
    finalCaveats.push('No documented body-to-economy rule matched this body.');
  }

  return {
    baseEconomies,
    modifierEconomies,
    strategicTags: uniqueStrings(strategicTags),
    confidence: Math.max(0.2, Math.min(0.95, finalConfidence)),
    caveats: uniqueStrings(finalCaveats),
  };
}

function bodyProfileEconomyWeights(
  baseEconomies: CoreEconomyName[],
  modifierEconomies: CoreEconomyName[],
): Record<CoreEconomyName, number> {
  const raw = {} as Record<CoreEconomyName, number>;
  baseEconomies.forEach((economy, index) => {
    raw[economy] = (raw[economy] ?? 0) + (index === 0 ? PRIMARY_BASE_WEIGHT : SECONDARY_BASE_WEIGHT);
  });
  modifierEconomies.forEach((economy) => {
    raw[economy] = (raw[economy] ?? 0) + MODIFIER_ECONOMY_WEIGHT;
  });
  return raw;
}

function unavailableBaseline(reason: string): StationBaselineEconomy {
  return {
    segments: [],
    baseEconomies: [],
    modifierEconomies: [],
    strategicTags: [],
    confidence: 0,
    caveats: [],
    calculationSource: 'ED-Finder Mega Guide body economy profile v1',
    unavailableReason: reason,
  };
}

function bodySubtype(record: Record<string, unknown>): string {
  return String(record.subtype ?? record.planet_class ?? record.body_type ?? '').toLowerCase();
}

function readBoolean(value: unknown): boolean {
  if (typeof value === 'string') return ['true', 't', '1', 'yes', 'y'].includes(value.toLowerCase());
  return Boolean(value);
}

function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function clampConfidence(value: number): number {
  return Math.max(0.2, Math.min(0.95, value));
}

function uniqueEconomies(items: CoreEconomyName[]): CoreEconomyName[] {
  const seen = new Set<CoreEconomyName>();
  return items.filter((item) => {
    if (!KNOWN_ECONOMIES.has(item) || seen.has(item)) return false;
    seen.add(item);
    return true;
  });
}

function uniqueStrings(items: string[]): string[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item)) return false;
    seen.add(item);
    return true;
  });
}

function roundPercent(value: number): number {
  return Math.round(value * 10) / 10;
}

function formatPercent(value: number): string {
  return Number.isInteger(value) ? `${value}%` : `${value.toFixed(1)}%`;
}
