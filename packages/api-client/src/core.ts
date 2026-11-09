/** Canonical same-origin browser transport shared by the V3 application facade. */
import { parseLosslessJson } from './lossless-json';

export const DEFAULT_API_BASE = '/api';
export const ADMIN_TOKEN_SESSION_KEY = 'ed_admin_token';

export function canonicalApiPath(path: string): string {
  if (
    /^[A-Za-z][A-Za-z\d+.-]*:/u.test(path) ||
    path.startsWith('//') ||
    path.includes('\\')
  )
    throw new TypeError('API paths must be same-origin');

  const rooted = path.startsWith('/') ? path : `/${path}`;
  const rootedPath = rooted.split(/[?#]/u, 1)[0];
  const apiPath =
    rootedPath === '/api' || rootedPath.startsWith('/api/')
      ? rooted
      : `/api${rooted}`;
  const normalised = new URL(apiPath, 'https://ed-finder.invalid');
  if (
    normalised.origin !== 'https://ed-finder.invalid' ||
    (normalised.pathname !== '/api' &&
      !normalised.pathname.startsWith('/api/'))
  )
    throw new TypeError('API paths must stay under /api');
  return `${normalised.pathname}${normalised.search}${normalised.hash}`;
}

export const resolveApiUrl = canonicalApiPath;

export type AdminEndpointClass =
  'admin' | 'operator' | 'owner-maintenance' | null;

type LegacyAdminEndpoint = {
  readonly method: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  readonly path: `/api/${string}`;
  readonly endpointClass: Exclude<AdminEndpointClass, null>;
};

/**
 * Exact browser compatibility boundary for backend routes guarded by
 * `require_admin`. Keep this method-specific inventory in sync with the API
 * decorators; broad route prefixes could disclose the reusable session token
 * to an unrelated endpoint added later.
 */
export const LEGACY_ADMIN_ENDPOINTS = [
  {
    method: 'GET',
    path: '/api/cache/stats',
    endpointClass: 'owner-maintenance',
  },
  {
    method: 'POST',
    path: '/api/cache/clear',
    endpointClass: 'owner-maintenance',
  },
  {
    method: 'POST',
    path: '/api/admin/rebuild-clusters',
    endpointClass: 'admin',
  },
  {
    method: 'POST',
    path: '/api/admin/rebuild-ratings',
    endpointClass: 'admin',
  },
  {
    method: 'POST',
    path: '/api/admin/operations/{operation_key}',
    endpointClass: 'admin',
  },
  {
    method: 'GET',
    path: '/api/admin/operations/history',
    endpointClass: 'admin',
  },
  {
    method: 'GET',
    path: '/api/admin/cron-status',
    endpointClass: 'admin',
  },
  {
    method: 'GET',
    path: '/api/admin/enrichment/station-status',
    endpointClass: 'admin',
  },
  {
    method: 'GET',
    path: '/api/admin/enrichment/warehouse-status',
    endpointClass: 'admin',
  },
  {
    method: 'GET',
    path: '/api/admin/data-status',
    endpointClass: 'admin',
  },
  {
    method: 'POST',
    path: '/api/evidence/records',
    endpointClass: 'operator',
  },
  {
    method: 'POST',
    path: '/api/evidence/features',
    endpointClass: 'operator',
  },
  {
    method: 'POST',
    path: '/api/evidence/rule-proposals',
    endpointClass: 'operator',
  },
  {
    method: 'POST',
    path: '/api/evidence/rule-proposals/{proposal_key}/decisions',
    endpointClass: 'operator',
  },
  {
    method: 'POST',
    path: '/api/evidence/systems/{system_id64}/promote-canonical',
    endpointClass: 'operator',
  },
  {
    method: 'POST',
    path: '/api/journal/imports/{run_key}/promote',
    endpointClass: 'operator',
  },
  {
    method: 'GET',
    path: '/api/status',
    endpointClass: 'owner-maintenance',
  },
  {
    method: 'GET',
    path: '/api/local/status',
    endpointClass: 'owner-maintenance',
  },
  {
    method: 'POST',
    path: '/api/observations/facts',
    endpointClass: 'operator',
  },
  {
    method: 'PATCH',
    path: '/api/observations/facts/{observation_id}',
    endpointClass: 'operator',
  },
  {
    method: 'DELETE',
    path: '/api/observations/facts/{observation_id}',
    endpointClass: 'operator',
  },
  {
    method: 'GET',
    path: '/api/operator/source-runs',
    endpointClass: 'operator',
  },
  {
    method: 'GET',
    path: '/api/operator/source-run-detail',
    endpointClass: 'operator',
  },
  {
    method: 'GET',
    path: '/api/operator/source-run-artifacts',
    endpointClass: 'operator',
  },
  {
    method: 'GET',
    path: '/api/operator/source-run-bridge',
    endpointClass: 'operator',
  },
  {
    method: 'GET',
    path: '/api/operator/source-run-staging-impact',
    endpointClass: 'operator',
  },
  {
    method: 'GET',
    path: '/api/operator/diagnostic-staging-rows',
    endpointClass: 'operator',
  },
  {
    method: 'GET',
    path: '/api/operator/safety-gates',
    endpointClass: 'operator',
  },
] as const satisfies readonly LegacyAdminEndpoint[];

function matchesEndpointPath(template: string, path: string): boolean {
  const templateParts = template.split('/');
  const pathParts = path.split('/');
  return (
    templateParts.length === pathParts.length &&
    templateParts.every(
      (part, index) =>
        (/^\{[^{}]+\}$/.test(part) && Boolean(pathParts[index])) ||
        part === pathParts[index],
    )
  );
}

export function adminEndpointClass(
  path: string,
  method = 'GET',
): AdminEndpointClass {
  const canonical = canonicalApiPath(path).split(/[?#]/, 1)[0];
  const normalizedMethod = method.toUpperCase();
  return (
    LEGACY_ADMIN_ENDPOINTS.find(
      (endpoint) =>
        endpoint.method === normalizedMethod &&
        matchesEndpointPath(endpoint.path, canonical),
    )?.endpointClass ?? null
  );
}

function sessionAdminToken(): string {
  if (typeof window === 'undefined') return '';
  try {
    return window.sessionStorage.getItem(ADMIN_TOKEN_SESSION_KEY)?.trim() ?? '';
  } catch {
    return '';
  }
}

/** Retained React call-site helper; the transport still revalidates the exact endpoint. */
export function operatorMutationHeaders(): HeadersInit | undefined {
  const token = sessionAdminToken();
  return token ? { 'X-Admin-Token': token } : undefined;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly path: string,
    public readonly body: unknown,
    message?: string,
    options?: ErrorOptions,
  ) {
    super(
      message ??
        (typeof body === 'string' && body.trim()
          ? `API ${status} on ${path}: ${body}`
          : `API request failed (${status}) on ${path}`),
      options,
    );
    this.name = 'ApiError';
  }
}

function usefulMessage(
  status: number,
  path: string,
  body: unknown,
  statusText: string,
): string {
  let detail = '';
  if (typeof body === 'string') detail = body;
  else if (body && typeof body === 'object') {
    const candidate =
      (body as { detail?: unknown; message?: unknown }).detail ??
      (body as { message?: unknown }).message;
    if (typeof candidate === 'string') detail = candidate;
  }
  return (
    detail.trim() ||
    statusText.trim() ||
    `API request failed (${status}) on ${path}`
  );
}

async function readResponse(
  response: Response,
  path: string,
): Promise<unknown> {
  const text = await response.text();
  if (!text) return '';
  if (
    !(response.headers.get('content-type') ?? '').toLowerCase().includes('json')
  )
    return text;
  try {
    return parseLosslessJson(text);
  } catch (cause) {
    throw new ApiError(
      response.status,
      path,
      text,
      'API returned invalid JSON',
      { cause },
    );
  }
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const canonicalPath = canonicalApiPath(path);
  const headers = new Headers(init.headers);
  if (!headers.has('Accept')) headers.set('Accept', 'application/json');
  if (init.body != null && !headers.has('Content-Type'))
    headers.set('Content-Type', 'application/json');
  // The browser transport accepts only the bounded session value. Never let a
  // caller smuggle an owner-link secret into a reusable admin header.
  headers.delete('X-Admin-Token');
  if (adminEndpointClass(canonicalPath, init.method ?? 'GET')) {
    const token = sessionAdminToken();
    if (token) headers.set('X-Admin-Token', token);
  }
  let response: Response;
  try {
    response = await fetch(canonicalPath, {
      ...init,
      credentials: 'include',
      headers,
    });
  } catch (cause) {
    if (
      cause &&
      typeof cause === 'object' &&
      'name' in cause &&
      cause.name === 'AbortError'
    )
      throw cause;
    throw new ApiError(
      0,
      canonicalPath,
      '',
      `Network request failed on ${canonicalPath}`,
      { cause },
    );
  }
  const body = await readResponse(response, canonicalPath);
  if (!response.ok)
    throw new ApiError(
      response.status,
      canonicalPath,
      body,
      usefulMessage(response.status, canonicalPath, body, response.statusText),
    );
  return body as T;
}


/** Compatibility name retained while React remains migration evidence. */
export const jsonFetch = apiRequest;
