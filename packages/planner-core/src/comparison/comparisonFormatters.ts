import type { BuildComparisonResult, NumericDelta, PlacementChange, PlacementChangeType } from './types';

export function formatDeltaValue(delta: NumericDelta, suffix = ''): string {
  if (delta.direction === 'unknown' || delta.delta == null) return 'unknown';
  if (delta.direction === 'unchanged') return `0${suffix}`;
  const sign = delta.delta > 0 ? '+' : '';
  const value = delta.delta.toFixed(Math.abs(delta.delta) % 1 === 0 ? 0 : 1);
  return `${sign}${value}${suffix}`;
}

export function formatVerdictLabel(verdict: BuildComparisonResult['recommendation']['verdict']): string {
  switch (verdict) {
    case 'prefer_after':
      return 'Prefer after';
    case 'prefer_before':
      return 'Prefer before';
    case 'mixed':
      return 'Mixed tradeoff';
    case 'insufficient_data':
      return 'Insufficient data';
    default:
      return 'Unknown verdict';
  }
}

export function formatChangeType(changeType: PlacementChangeType): string {
  return changeType.replace(/_/g, ' ');
}

export function formatRiskDirection(direction: BuildComparisonResult['risk_delta']['risk_direction']): string {
  switch (direction) {
    case 'lower':
      return 'Lower risk';
    case 'higher':
      return 'Higher risk';
    case 'unchanged':
      return 'Risk unchanged';
    case 'unknown':
      return 'Risk unknown';
    default:
      return 'Risk unknown';
  }
}

export function formatPlacementChange(change: PlacementChange): string {
  return `${change.facility_template_id}: ${formatChangeType(change.change_type)}`;
}
