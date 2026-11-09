import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ADMIN_TOKEN_SESSION_KEY,
  ApiError,
  LEGACY_ADMIN_ENDPOINTS,
  adminEndpointClass,
  apiRequest,
  canonicalApiPath,
} from './core';

const jsonResponse = (body: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(body), {
    ...init,
    headers: { 'content-type': 'application/json', ...init.headers },
  });

const concretePath = (template: string) =>
  template.replace(/\{[^{}]+\}/g, 'test-value');

describe('canonical browser API transport', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('allows only normalized same-origin paths below /api', () => {
    expect(canonicalApiPath('/health')).toBe('/api/health');
    expect(canonicalApiPath('/api/health?fresh=1')).toBe('/api/health?fresh=1');
    expect(canonicalApiPath('/api/admin/../auth/session')).toBe('/api/auth/session');
    for (const hostile of [
      'https://example.test/api/health',
      '//example.test/api/health',
      '/admin/../../outside',
      '/api\\auth\\session',
    ]) expect(() => canonicalApiPath(hostile)).toThrow();
  });

  it('matches the exact method-specific admin compatibility inventory', () => {
    expect(LEGACY_ADMIN_ENDPOINTS).toHaveLength(28);
    for (const endpoint of LEGACY_ADMIN_ENDPOINTS) {
      expect(adminEndpointClass(
        `${concretePath(endpoint.path)}?ignored=yes`, endpoint.method,
      )).toBe(endpoint.endpointClass);
    }
    expect(adminEndpointClass('/api/admin/not-an-endpoint')).toBeNull();
    expect(adminEndpointClass('/api/cache/stats', 'POST')).toBeNull();
    expect(adminEndpointClass('/api/admin/../auth/session')).toBeNull();
  });

  it('uses cookie credentials and injects only the bounded session token', async () => {
    sessionStorage.setItem(ADMIN_TOKEN_SESSION_KEY, ' token-123 ');
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => jsonResponse({}));

    await apiRequest('/cache/stats', {
      headers: { 'X-Admin-Token': 'caller-secret' },
    });
    await apiRequest('/auth/owner/claim', {
      method: 'POST',
      headers: { 'X-Admin-Token': 'one-time-owner-secret' },
      body: '{}',
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/cache/stats');
    expect(fetchMock.mock.calls[0]?.[1]?.credentials).toBe('include');
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get('X-Admin-Token')).toBe('token-123');
    expect(new Headers(fetchMock.mock.calls[1]?.[1]?.headers).has('X-Admin-Token')).toBe(false);
  });

  it('parses uint64 response fields before JavaScript can round them', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      '{"system_id64":18446744073709551615,"nested":{"id64":9007199254740993}}',
      { headers: { 'content-type': 'application/json' } },
    ));

    await expect(apiRequest('/system/max')).resolves.toEqual({
      system_id64: '18446744073709551615',
      nested: { id64: '9007199254740993' },
    });
  });

  it('preserves structured errors and normalizes network failures', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    fetchMock.mockResolvedValueOnce(new Response(
      '{"detail":"No access","system_id64":9007199254740993}',
      { status: 403, headers: { 'content-type': 'application/json' } },
    ));
    await expect(apiRequest('/system/9007199254740993')).rejects.toMatchObject({
      status: 403,
      path: '/api/system/9007199254740993',
      body: { detail: 'No access', system_id64: '9007199254740993' },
      message: 'No access',
    });

    const cause = new TypeError('offline');
    fetchMock.mockRejectedValueOnce(cause);
    const failure = await apiRequest('/health').catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 0, path: '/api/health', cause });
  });
});
