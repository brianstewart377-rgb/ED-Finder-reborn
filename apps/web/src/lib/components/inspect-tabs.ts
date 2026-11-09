export const INSPECT_TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'bodies', label: 'Bodies' },
  { value: 'stations', label: 'Stations' },
] as const;

export type InspectTab = (typeof INSPECT_TABS)[number]['value'];

/** Only the three task names belong in Inspect's URL state. */
export function parseInspectTab(value: string | null): InspectTab {
  return value === 'bodies' || value === 'stations' ? value : 'overview';
}
