export function formatObservedValue(value: unknown): string {
  if (value == null) return 'none';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function signed(value: number): string {
  return value > 0 ? `+${value}` : String(value);
}

export function standardLabel(value: string): string {
  const labels: Record<string, string> = {
    observed: 'Observed',
    verified: 'Verified',
    community_observed: 'Community observed',
    inferred: 'Inferred',
    estimated: 'Estimated',
    speculative: 'Speculative',
    unknown: 'Unknown',
    predicted: 'Estimated',
  };
  return labels[value] ?? titleCase(value);
}

export function asString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

export function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

export function formatLocation(location: string): string {
  return location.replace(/_/g, ' ');
}

export function titleCase(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

export function confidenceLabel(value: number): string {
  if (value >= 0.75) return 'High';
  if (value >= 0.55) return 'Medium';
  return 'Low';
}
