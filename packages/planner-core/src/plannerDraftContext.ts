export type ColonyProjectObjective =
  | 'materials_coverage'
  | 'industry_economy'
  | 'extraction_mining_support'
  | 'missions_services'
  | 'personal_base_custom_goal'
  | 'balanced'
  | 'decide_later';

export type ColonyProjectStartApproach = 'recommendation_assisted' | 'manual';

export type ColonyProjectCreatedFrom = 'system_detail' | 'expansion_plan';

export interface PlannerObjectiveOption {
  value: ColonyProjectObjective;
  label: string;
  description: string;
}

export const PLANNER_OBJECTIVE_OPTIONS: PlannerObjectiveOption[] = [
  {
    value: 'materials_coverage',
    label: 'Materials coverage',
    description: 'Focus on practical coverage for colony-building materials and support needs.',
  },
  {
    value: 'industry_economy',
    label: 'Industry / economy',
    description: 'Start from a system-level economy and infrastructure perspective.',
  },
  {
    value: 'extraction_mining_support',
    label: 'Extraction / mining support',
    description: 'Shape the draft around extraction and mining support decisions.',
  },
  {
    value: 'missions_services',
    label: 'Missions / services',
    description: 'Prioritise mission-facing and service-facing capability planning.',
  },
  {
    value: 'personal_base_custom_goal',
    label: 'Personal base / custom goal',
    description: 'Start a flexible draft for a personal base or another custom goal.',
  },
  {
    value: 'balanced',
    label: 'Balanced',
    description: 'Keep the draft broad and adaptable while you explore trade-offs.',
  },
  {
    value: 'decide_later',
    label: "I'll decide later",
    description: 'Create the draft now and decide on the objective inside the planner.',
  },
];

export function objectiveLabel(value: ColonyProjectObjective | null | undefined): string {
  switch (value) {
    case 'materials_coverage':
      return 'Materials coverage';
    case 'industry_economy':
      return 'Industry / economy';
    case 'extraction_mining_support':
      return 'Extraction / mining support';
    case 'missions_services':
      return 'Missions / services';
    case 'personal_base_custom_goal':
      return 'Personal base / custom goal';
    case 'balanced':
      return 'Balanced';
    case 'decide_later':
      return "I'll decide later";
    default:
      return 'Objective not set';
  }
}

export function objectiveSummaryLabel(value: ColonyProjectObjective | null | undefined): string {
  if (!value || value === 'decide_later') {
    return 'Objective not set';
  }
  return objectiveLabel(value);
}

export function startApproachLabel(value: ColonyProjectStartApproach | null | undefined): string {
  if (value === 'recommendation_assisted') {
    return 'ED-Finder recommendation';
  }
  if (value === 'manual') {
    return 'Build my own plan';
  }
  return 'Approach not set';
}

export function plannerNextActionCopy(value: ColonyProjectStartApproach | null | undefined): string {
  if (value === 'recommendation_assisted') {
    return 'Review suitable build approaches for this objective.';
  }
  return 'Choose a body to begin planning.';
}

export function defaultDraftProjectName(
  systemName: string,
  objective: ColonyProjectObjective,
): string {
  const safeSystemName = systemName.trim() || 'Unknown system';
  switch (objective) {
    case 'materials_coverage':
      return `${safeSystemName} - Materials coverage`;
    case 'industry_economy':
      return `${safeSystemName} - Industry plan`;
    case 'extraction_mining_support':
      return `${safeSystemName} - Extraction support`;
    case 'missions_services':
      return `${safeSystemName} - Mission services`;
    case 'personal_base_custom_goal':
      return `${safeSystemName} - New plan`;
    case 'balanced':
      return `${safeSystemName} - Balanced plan`;
    case 'decide_later':
    default:
      return `${safeSystemName} - New plan`;
  }
}
