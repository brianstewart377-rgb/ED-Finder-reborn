/**
 * Pure helpers for the Stage 6D Validation panel.
 *
 * These helpers do NOT call the API. They produce stable cache keys for
 * the prediction (so the compare query refreshes when the preview
 * changes) and format compare-API values for display.
 */
import type {
  ObservedJsonValue,
  PredictionObservationComparison,
  SimulateBuildResponse,
} from '@ed-finder/api-client/types';

type ValidationPredictionSource = Pick<
  SimulateBuildResponse,
  | 'final_score'
  | 'confidence'
  | 'cp'
  | 'economy_composition'
  | 'economy_order'
  | 'services'
  | 'port_service_states'
>;

/**
 * The complete prediction input consumed by the Stage 6C backend.
 *
 * Keep this projection aligned with:
 *   * comparison_engine_pkg/prediction_extractors.py
 *   * comparison_engine_pkg/cp_rules.py
 *   * comparison_engine_pkg/build_outcome_rules.py
 *
 * Validation sends this projection to the compare endpoint and uses the
 * same value for its cache fingerprint. That makes it impossible for a
 * backend-relevant prediction change to be hidden behind an unchanged
 * frontend cache key.
 */
export function validationInputProjection(result: ValidationPredictionSource): Record<string, unknown> {
  return {
    final_score: result.final_score,
    confidence: result.confidence,
    cp: {
      yellow_cp_final: result.cp.yellow_cp_final,
      green_cp_final: result.cp.green_cp_final,
      yellow_cp_generated: result.cp.yellow_cp_generated,
      green_cp_generated: result.cp.green_cp_generated,
      yellow_cp_spent: result.cp.yellow_cp_spent,
      green_cp_spent: result.cp.green_cp_spent,
      t2_ports: result.cp.t2_ports,
      t3_ports: result.cp.t3_ports,
      warnings: [...result.cp.warnings],
    },
    economy_composition: sortedRecord(result.economy_composition),
    economy_order: [...result.economy_order],
    services: Object.fromEntries(
      Object.entries(result.services)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([serviceId, service]) => [serviceId, service?.status ?? null]),
    ),
    port_service_states: normalisePortServiceStates(result.port_service_states),
  };
}

/**
 * Stable fingerprint of a Simulation Preview result.
 *
 * The query key for the compare API includes this fingerprint so that:
 *   * two distinct preview runs against the same system+archetype with
 *     different placements produce different query keys (and therefore
 *     fresh compare calls);
 *   * an unchanged preview result reuses the cached compare response
 *     without a network call.
 *
 * The fingerprint serialises the shared, normalised compare-request
 * projection. Do not add an independently maintained field list here.
 */
export function previewResultFingerprint(result: SimulateBuildResponse | null): string | null {
  if (!result) return null;
  return JSON.stringify(validationInputProjection(result));
}

function sortedRecord<T>(record: Record<string, T>): Record<string, T> {
  return Object.fromEntries(
    Object.entries(record).sort(([left], [right]) => left.localeCompare(right)),
  );
}

function normalisePortServiceStates(
  states: SimulateBuildResponse['port_service_states'],
): Array<Record<string, Record<string, true>>> {
  if (states.length === 0) return [];

  const active = new Set<string>();
  const locked = new Set<string>();
  const unknown = new Set<string>();

  for (const state of states) {
    Object.keys(state.active_services).forEach((serviceId) => active.add(serviceId));
    Object.keys(state.locked_services).forEach((serviceId) => locked.add(serviceId));
    Object.keys(state.unknown_services).forEach((serviceId) => unknown.add(serviceId));
  }

  // Match backend precedence: active > locked > unknown > top-level service.
  active.forEach((serviceId) => {
    locked.delete(serviceId);
    unknown.delete(serviceId);
  });
  locked.forEach((serviceId) => unknown.delete(serviceId));

  return [{
    active_services: setRecord(active),
    locked_services: setRecord(locked),
    unknown_services: setRecord(unknown),
  }];
}

function setRecord(values: Set<string>): Record<string, true> {
  return Object.fromEntries(
    [...values]
      .sort((left, right) => left.localeCompare(right))
      .map((value) => [value, true]),
  );
}

/**
 * Render an `observed_value` / `predicted_value` for display in the
 * Validation card. Mirrors the Stage 6B formatter but lives here so
 * the Validation panel does not depend on Observed Evidence internals.
 */
export function formatComparisonValue(value: ObservedJsonValue | null | undefined): string {
  if (value === undefined || value === null) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/**
 * Filter comparison rows by status. An empty status filter returns the
 * full list. Unknown statuses are passed through verbatim so the UI
 * doesn't silently drop rows the backend introduces in a later stage.
 */
export function filterComparisonsByStatus(
  comparisons: PredictionObservationComparison[],
  statusFilter: string | null,
): PredictionObservationComparison[] {
  if (!statusFilter) return comparisons;
  return comparisons.filter((row) => row.status === statusFilter);
}
