export type CoreEconomyName =
  | 'Agriculture'
  | 'Refinery'
  | 'Industrial'
  | 'HighTech'
  | 'Military'
  | 'Tourism'
  | 'Extraction';

export type EconomyVisualName =
  | CoreEconomyName
  | 'Terraforming'
  | 'Civilian'
  | 'Support'
  | 'Contextual'
  | 'Unknown';

interface EconomyVisual {
  label: string;
  compactLabel: string;
  color: string;
  softColor: string;
}

export const CORE_ECONOMY_ORDER: CoreEconomyName[] = [
  'Refinery',
  'Industrial',
  'Extraction',
  'Agriculture',
  'Military',
  'HighTech',
  'Tourism',
];

export const ECONOMY_VISUALS: Record<EconomyVisualName, EconomyVisual> = {
  Agriculture: { label: 'Agriculture', compactLabel: 'Agri', color: '#4f9a4f', softColor: 'rgba(79,154,79,0.24)' },
  Refinery: { label: 'Refinery', compactLabel: 'Ref', color: '#fbbf24', softColor: 'rgba(251,191,36,0.24)' },
  Industrial: { label: 'Industrial', compactLabel: 'Ind', color: '#ff7a14', softColor: 'rgba(255,122,20,0.22)' },
  HighTech: { label: 'HighTech', compactLabel: 'HiTech', color: '#4688cf', softColor: 'rgba(70,136,207,0.22)' },
  Military: { label: 'Military', compactLabel: 'Mil', color: '#9a4242', softColor: 'rgba(154,66,66,0.22)' },
  Tourism: { label: 'Tourism', compactLabel: 'Tour', color: '#8460c4', softColor: 'rgba(132,96,196,0.22)' },
  Extraction: { label: 'Extraction', compactLabel: 'Ext', color: '#94a3b8', softColor: 'rgba(148,163,184,0.24)' },
  Terraforming: { label: 'Terraforming', compactLabel: 'Terra', color: '#3b977a', softColor: 'rgba(59,151,122,0.22)' },
  Civilian: { label: 'Civilian', compactLabel: 'Civ', color: '#c8ccd1', softColor: 'rgba(200,204,209,0.18)' },
  Support: { label: 'Support', compactLabel: 'Supp', color: '#a3a3a3', softColor: 'rgba(163,163,163,0.18)' },
  Contextual: { label: 'Contextual', compactLabel: 'Ctx', color: '#93a3b8', softColor: 'rgba(147,163,184,0.18)' },
  Unknown: { label: 'Unknown', compactLabel: 'Unk', color: '#6b7280', softColor: 'rgba(107,114,128,0.18)' },
};

const ECONOMY_ALIASES: Record<string, EconomyVisualName> = {
  agriculture: 'Agriculture',
  agricultural: 'Agriculture',
  agri: 'Agriculture',
  refinery: 'Refinery',
  ref: 'Refinery',
  industrial: 'Industrial',
  industry: 'Industrial',
  ind: 'Industrial',
  hightech: 'HighTech',
  high_tech: 'HighTech',
  'high tech': 'HighTech',
  'high-tech': 'HighTech',
  hitech: 'HighTech',
  military: 'Military',
  mil: 'Military',
  tourism: 'Tourism',
  tourist: 'Tourism',
  extraction: 'Extraction',
  ext: 'Extraction',
  terraforming: 'Terraforming',
  terraform: 'Terraforming',
  civilian: 'Civilian',
  civil: 'Civilian',
  support: 'Support',
  contextual: 'Contextual',
  unknown: 'Unknown',
};

export function normaliseEconomyName(value?: string | null): EconomyVisualName | null {
  if (!value) return null;
  const normalised = value.trim().replace(/[-_\s]+/g, ' ').toLowerCase();
  if (!normalised) return null;
  return ECONOMY_ALIASES[normalised]
    ?? ECONOMY_ALIASES[normalised.replace(/\s+/g, '')]
    ?? null;
}

export function normaliseCoreEconomy(value?: string | null): CoreEconomyName | null {
  const economy = normaliseEconomyName(value);
  return economy && CORE_ECONOMY_ORDER.includes(economy as CoreEconomyName)
    ? economy as CoreEconomyName
    : null;
}

export function economyColor(value?: string | null): string {
  const economy = normaliseEconomyName(value) ?? 'Unknown';
  return ECONOMY_VISUALS[economy].color;
}

export function economySoftColor(value?: string | null): string {
  const economy = normaliseEconomyName(value) ?? 'Unknown';
  return ECONOMY_VISUALS[economy].softColor;
}

export function economyLabel(value?: string | null): string {
  const economy = normaliseEconomyName(value) ?? 'Unknown';
  return ECONOMY_VISUALS[economy].label;
}

export function compactEconomyLabel(value?: string | null): string {
  const economy = normaliseEconomyName(value) ?? 'Unknown';
  return ECONOMY_VISUALS[economy].compactLabel;
}
