export const PLAN_TABS = [
  { id: 'system-layout', label: 'System layout' },
  { id: 'build-order', label: 'Build order' },
] as const;

export type PlanTab = (typeof PLAN_TABS)[number]['id'];

/** Unknown or repeated values cannot select a task. The default needs no URL state. */
export function readPlanTab(params: URLSearchParams): PlanTab {
  const values = params.getAll('tab');
  return values.length === 1 && values[0] === 'build-order'
    ? 'build-order'
    : 'system-layout';
}

export function planTabUrl(source: URL, tab: PlanTab): URL {
  const url = new URL(source);
  url.searchParams.delete('tab');
  if (tab === 'build-order') url.searchParams.set('tab', tab);
  return url;
}
