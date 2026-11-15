// What the galaxy map asks for: the stars in view, the density map, and a
// signed-in person's own travel.
// Offered to the app through ./client, like everything in this folder.
import { parseId64 } from '@ed-finder/api-client/id64';
import type { Id64 } from '@ed-finder/api-client/id64';
import type {
  MapHeatmapApiMapHeatmapGetData,
  MapHeatmapPyramidResponse,
  MapViewportResponse,
  V3TravelHeatmapResponse,
} from './generated';
import {
  getV3JournalTravelHeatmap,
  mapHeatmapApiMapHeatmapGet,
  mapSystemsApiMapSystemsGet,
} from './generated/sdk.gen';
import { losslessId64 } from './values';

export type CatalogueViewportRequest = Readonly<{
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
  minZ: number;
  maxZ: number;
  limit: number;
}>;

export type CatalogueViewportResponse = Readonly<{
  systems: readonly Readonly<{
    id64: Id64;
    name: string;
    x: number;
    y: number;
    z: number;
    mainStarClass?: string | null;
    populated: boolean;
  }>[];
  truncated: boolean;
}>;

export type {
  MapHeatmapPyramidCell,
  MapHeatmapPyramidResponse,
} from './generated';

export type MapHeatmapResponse =
  | MapHeatmapPyramidResponse
  | { source: 'legacy-fallback'; [k: string]: unknown };

export type CommanderTravelHeatmapResponse = Readonly<{
  mode: 'markers' | 'density';
  visits: readonly Readonly<{
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
  }>[];
  count: number;
  truncated: boolean;
}>;

export async function getCatalogueViewportSystems(
  request: CatalogueViewportRequest,
  signal?: AbortSignal,
): Promise<CatalogueViewportResponse> {
  const { data } = await mapSystemsApiMapSystemsGet({
    throwOnError: true,
    priority: 'low',
    query: {
      min_x: request.minX,
      max_x: request.maxX,
      min_y: request.minY,
      max_y: request.maxY,
      min_z: request.minZ,
      max_z: request.maxZ,
      limit: request.limit,
    },
    signal,
  });
  const response = data as MapViewportResponse;
  return {
    systems: response.systems.map((system) => ({
      id64: losslessId64(system.id64),
      name: system.name,
      x: system.x,
      y: system.y,
      z: system.z,
      mainStarClass: system.main_star_class,
      populated: system.populated,
    })),
    truncated: response.truncated ?? false,
  };
}

export type MapHeatmapRequest = NonNullable<
  MapHeatmapApiMapHeatmapGetData['query']
>;

export const getMapHeatmap = async (
  query: MapHeatmapRequest,
  signal?: AbortSignal,
): Promise<MapHeatmapResponse> =>
  (await mapHeatmapApiMapHeatmapGet({ query, throwOnError: true, signal }))
    .data as MapHeatmapResponse;

/**
 * The signed-in account's imported journal travel (V3 journal store) inside
 * one viewport box, aggregated server-side. Rejects with a 401 ApiError when
 * signed out.
 */
export async function getCommanderTravelHeatmap(
  request: CatalogueViewportRequest,
  cameraDistanceLy: number,
  signal?: AbortSignal,
): Promise<CommanderTravelHeatmapResponse> {
  const { data } = await getV3JournalTravelHeatmap({
    throwOnError: true,
    query: {
      min_x: request.minX,
      max_x: request.maxX,
      min_y: request.minY,
      max_y: request.maxY,
      min_z: request.minZ,
      max_z: request.maxZ,
      zoom: Math.min(5_000, Math.max(0.001, cameraDistanceLy)),
      limit: Math.min(20_000, request.limit),
    },
    signal,
  });
  const response = data as V3TravelHeatmapResponse;
  return {
    mode: response.mode,
    visits: response.points.map((point) => ({
      kind: point.kind,
      systemId64:
        point.system_id64 == null ? null : parseId64(point.system_id64),
      systemName: point.system_name,
      x: point.x,
      y: point.y,
      z: point.z,
      visitCount: point.visit_count,
      firstVisitedAt: point.first_visited_at,
      lastVisitedAt: point.last_visited_at,
      cellSizeLy: response.cell_size_ly,
    })),
    count: response.count,
    truncated: response.truncated,
  };
}
