// The Plan page: its catalogue, a system's bodies as the planner sees them,
// and the sums for a plan.
// Offered to the app through ./client, like everything in this folder.
import { ApiError } from '@ed-finder/api-client/core';
import { parseId64 } from '@ed-finder/api-client/id64';
import type { PlanRequestV1 } from './generated';
import {
  evaluateV3Plan as evaluatePlanSdk,
  getV3PlanCatalogue as getPlanCatalogueSdk,
  getV3System,
  listV3PlanBodies as listPlanBodiesSdk,
} from './generated/sdk.gen';

export const getV3PlanCatalogue = async (signal?: AbortSignal) =>
  (await getPlanCatalogueSdk({ throwOnError: true, signal })).data;

export const listV3PlanBodies = async (
  systemId: string,
  signal?: AbortSignal,
) =>
  (
    await listPlanBodiesSdk({
      path: { system_id: parseId64(systemId) },
      throwOnError: true,
      signal,
    })
  ).data;

export const evaluateV3Plan = async (
  body: PlanRequestV1,
  signal?: AbortSignal,
) => {
  const result = await evaluatePlanSdk({ body, throwOnError: false, signal });
  if (result.data !== undefined) return result.data;
  const header = result.response?.headers.get('Retry-After')?.trim();
  const seconds = header && /^\d+$/.test(header) ? Number(header) : null;
  const retryAfterSeconds =
    seconds !== null && Number.isSafeInteger(seconds) ? seconds : null;
  const error =
    result.error instanceof ApiError
      ? result.error
      : new ApiError(
          result.response?.status ?? 0,
          '/api/v1/plan/evaluate',
          result.error,
        );
  throw Object.assign(error, { retryAfterSeconds });
};

/** Identity only: Plan does not load Inspect's map, stations or ratings. */
export const getPlanSystem = async (systemId: string, signal?: AbortSignal) =>
  (
    await getV3System({
      path: { system_id: parseId64(systemId) },
      throwOnError: true,
      signal,
    })
  ).data;
