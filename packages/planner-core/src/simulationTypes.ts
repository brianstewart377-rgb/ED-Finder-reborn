import type { BuildabilityData } from '@ed-finder/api-client/types';

export const ARCHETYPES = [
  { id: 'refinery_industrial', label: 'Refinery / Industrial' },
  { id: 'extraction_refinery', label: 'Extraction / Refinery' },
  { id: 'agriculture_terraforming', label: 'Agriculture / Terraforming' },
  { id: 'hitech_tourism', label: 'High Tech / Tourism' },
  { id: 'military_industrial', label: 'Military / Industrial' },
  { id: 'trade_logistics', label: 'Trade / Logistics' },
  { id: 'flexible_multirole', label: 'Flexible Multirole' },
];

export type StartMode = 'recommended' | 'edit_recommended' | 'blank_advanced' | 'optimiser_candidate';
export type RecommendedStep = NonNullable<BuildabilityData['recommended_build_order']>[number];
