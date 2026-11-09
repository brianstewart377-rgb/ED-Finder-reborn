export type PlannerQueryKey = readonly unknown[];

/** All cached reads whose data can change when imported layout changes. */
export function layoutImportInvalidationKeys(systemId64: number): PlannerQueryKey[] {
  return [
    ['system', systemId64],
    ['slot-predictions-preview', systemId64],
    ['sim-summary-preview', systemId64],
    ['buildability', systemId64],
    ['recommended-builds', systemId64],
  ];
}
