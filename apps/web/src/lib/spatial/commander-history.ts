import type { Id64 } from '$lib/domain/id64';
import type { SpatialContribution, Vec3Ly } from './contracts';

export const COMMANDER_HISTORY_LAYER_ID = 'commander-history-heatmap';

export type CommanderVisitPoint = Readonly<{
  positionLy: Vec3Ly;
  visitCount: number;
  firstVisitedAt: string;
  lastVisitedAt: string;
  cellSizeLy?: number;
  systemId64?: Id64;
  systemName?: string;
}>;

export type CommanderHistoryPayload = Readonly<{
  source: 'journal-log';
  mode: 'markers' | 'density';
  points: readonly CommanderVisitPoint[];
}>;

export type CommanderViewportVisit = Readonly<{
  kind: 'marker' | 'density';
  systemId64?: Id64 | null;
  systemName?: string | null;
  x: number;
  y: number;
  z: number;
  visitCount: number;
  firstVisitedAt: string;
  lastVisitedAt: string;
  cellSizeLy?: number | null;
}>;

export function createCommanderHistoryContribution(
  mode: 'markers' | 'density',
  visits: readonly CommanderViewportVisit[],
  revision: number,
  truncated: boolean,
): SpatialContribution {
  const points: CommanderVisitPoint[] = visits.map((visit) => ({
    positionLy: { x: visit.x, y: visit.y, z: visit.z },
    visitCount: visit.visitCount,
    firstVisitedAt: visit.firstVisitedAt,
    lastVisitedAt: visit.lastVisitedAt,
    ...(visit.cellSizeLy != null && { cellSizeLy: visit.cellSizeLy }),
    ...(visit.systemId64 && { systemId64: visit.systemId64 }),
    ...(visit.systemName && { systemName: visit.systemName }),
  }));
  const payload: CommanderHistoryPayload = {
    source: 'journal-log',
    mode,
    points,
  };
  return {
    id: 'commander-history',
    owner: 'COMMANDER_HISTORY',
    revision,
    layers: [
      {
        id: COMMANDER_HISTORY_LAYER_ID,
        version: 1,
        representation: 'DERIVED',
        payload,
        targetCount: points.length,
        truncated,
      },
    ],
  };
}
