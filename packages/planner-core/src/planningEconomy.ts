import type { FacilityTemplate, SimulateBuildPlacement } from '@ed-finder/api-client/types';
import {
  compactEconomyLabel as compactEconomyVisualLabel,
  CORE_ECONOMY_ORDER,
  normaliseCoreEconomy,
  type CoreEconomyName,
} from './economyVisuals';

export const PLANNING_ECONOMY_NOTE = 'Planning economy mix — run Preview for validated outcome.';

export type PlanningEconomyName = CoreEconomyName;

export interface PlanningEconomyEntry {
  economy: PlanningEconomyName;
  planned: number;
  projected: number;
  total: number;
}

export interface PlanningEconomyLedger {
  entries: PlanningEconomyEntry[];
  plannedCount: number;
  projectedCount: number;
  unknownCount: number;
  total: number;
}

interface LedgerInput {
  placements?: SimulateBuildPlacement[];
  projectedPlacements?: SimulateBuildPlacement[];
  templates?: FacilityTemplate[];
}

const ECONOMY_ORDER: PlanningEconomyName[] = CORE_ECONOMY_ORDER;

export function buildPlanningEconomyLedger({
  placements = [],
  projectedPlacements = [],
  templates = [],
}: LedgerInput): PlanningEconomyLedger {
  const templatesById = new Map(templates.map((template) => [template.id, template]));
  const counts = new Map<PlanningEconomyName, { planned: number; projected: number }>();
  let unknownCount = 0;

  const add = (placement: SimulateBuildPlacement, kind: 'planned' | 'projected') => {
    const template = templatesById.get(placement.facility_template_id);
    const economy = normalisePlanningEconomy(template?.economy);
    if (!economy) {
      if (template?.is_port) return;
      unknownCount += 1;
      return;
    }
    const current = counts.get(economy) ?? { planned: 0, projected: 0 };
    current[kind] += 1;
    counts.set(economy, current);
  };

  placements.forEach((placement) => add(placement, 'planned'));
  projectedPlacements.forEach((placement) => add(placement, 'projected'));

  const entries = ECONOMY_ORDER
    .map((economy) => {
      const current = counts.get(economy) ?? { planned: 0, projected: 0 };
      return {
        economy,
        planned: current.planned,
        projected: current.projected,
        total: current.planned + current.projected,
      };
    })
    .filter((entry) => entry.total > 0);

  const plannedCount = entries.reduce((sum, entry) => sum + entry.planned, 0);
  const projectedCount = entries.reduce((sum, entry) => sum + entry.projected, 0);

  return {
    entries,
    plannedCount,
    projectedCount,
    unknownCount,
    total: plannedCount + projectedCount,
  };
}

export function normalisePlanningEconomy(value?: string | null): PlanningEconomyName | null {
  return normaliseCoreEconomy(value);
}

export function compactEconomyLabel(economy: PlanningEconomyName): string {
  return compactEconomyVisualLabel(economy);
}
