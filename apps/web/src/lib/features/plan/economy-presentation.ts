import type { EconomyItemV1, EconomyV1 } from '$lib/api';

export interface EconomyWhySelection {
  siteId: string;
  economyName: string;
  ruleIds: string[];
}

// One stable palette for segments and legends. These use the existing Plan
// palette; token fallbacks also support the forthcoming design kit.
const ECONOMY_COLOURS: Readonly<Record<string, string>> = {
  Agriculture: 'var(--ok, var(--color-tier-b, #86d99c))',
  Extraction: 'var(--warn, var(--color-tier-c, #e9c46a))',
  Industrial: 'var(--accent, var(--color-signal, #ff7a14))',
  'High Tech': 'var(--neon, var(--color-cyan, #7dd3fc))',
  HighTech: 'var(--neon, var(--color-cyan, #7dd3fc))',
  Military:
    'color-mix(in srgb, var(--accent, var(--color-signal)) 65%, var(--neon, var(--color-cyan)))',
  Refinery: '#ffb18b',
  Tourism:
    'color-mix(in srgb, var(--neon, var(--color-cyan)) 60%, var(--ok, var(--color-tier-b)))',
  Terraforming:
    'color-mix(in srgb, var(--ok, var(--color-tier-b)) 60%, var(--warn, var(--color-tier-c)))',
};

export function economyColour(name: string): string {
  return ECONOMY_COLOURS[name] ?? 'var(--text-muted, var(--muted, #a6acb4))';
}

export const ECONOMY_CHANGE_THRESHOLD = 5;

export const CONTRIBUTION_LABELS: Record<EconomyItemV1['kind'], string> = {
  own: 'Own',
  strong_link: 'Strong link',
  passed_on_link: 'Passed on',
  planetary_port_link: 'Planetary port',
  weak_links: 'Weak link',
};

const numberFormat = new Intl.NumberFormat('en', {
  maximumFractionDigits: 6,
});

/** The rulebook's strength 1.0 is 100%; never normalise a port to 100%. */
export function economyPercent(strength: number): string {
  return `${numberFormat.format(strength * 100)}%`;
}

export function economyTotal(economies: EconomyV1[]): number {
  return economies.reduce((total, economy) => total + economy.strength, 0);
}

export function economyChange(
  name: string,
  now: EconomyV1[],
  after: EconomyV1[],
): string | null {
  const before = now.find((economy) => economy.name === name);
  const next = after.find((economy) => economy.name === name);
  if (!before && next) return 'Appears after plan';
  if (before && !next) return 'Disappears after plan';
  if (!before || !next) return null;
  const change = (next.strength - before.strength) * 100;
  // Ignore only floating point noise at the documented strict threshold.
  if (Math.abs(change) <= ECONOMY_CHANGE_THRESHOLD + 1e-9) return null;
  return `${change > 0 ? 'Increases' : 'Decreases'} by ${numberFormat.format(Math.abs(change))} percentage points`;
}

export function economyRuleIds(
  name: string,
  now: EconomyV1[],
  after: EconomyV1[],
): string[] {
  return [
    ...new Set(
      [...now, ...after]
        .filter((economy) => economy.name === name)
        .flatMap((economy) => economy.items.flatMap((item) => item.rule_ids)),
    ),
  ];
}
