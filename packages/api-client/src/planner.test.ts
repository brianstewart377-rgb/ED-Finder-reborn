import { afterEach, describe, expect, it, vi } from 'vitest';
import type {
  DevelopmentRerankRequest,
  JournalImportRequest,
  OptimiserCandidatesRequest,
  SimulateBuildRequest,
} from './types';
import {
  archetypeRerank,
  archetypeSystem,
  buildability,
  evidenceSystemSummary,
  facilityTemplates,
  importJournal,
  importSystemLayout,
  journalImportReceipt,
  journalTelemetry,
  optimiserCandidates,
  profileSyncPull,
  profileSyncPush,
  provenanceCockpit,
  recommendedBuilds,
  regionalAnalysis,
  simulateBuild,
  simulationSummary,
  slotPredictions,
  system,
  warehousePlannerEvidence,
} from './planner';

const SYSTEM_ID64 = 12866676218109;

function responseJson(payload: unknown = {}): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function stubFetch(payload: unknown = {}) {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => responseJson(payload));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function expectReadOnlyCall(
  fetchMock: ReturnType<typeof stubFetch>,
  expectedUrl: string,
): void {
  const calls = fetchMock.mock.calls;
  expect(calls).toHaveLength(1);
  expect(String(calls[0]?.[0])).toBe(expectedUrl);
  const init = calls[0]?.[1] as RequestInit | undefined;
  expect(['GET', undefined]).toContain(init?.method);
  expect(String(init?.method ?? 'GET')).not.toMatch(/POST|PATCH|DELETE|PUT/i);
}

describe('provenance cockpit API helpers', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('calls the Stage 20B provenance endpoint with a GET/read-only request only', async () => {
    const fetchMock = stubFetch();

    await provenanceCockpit(SYSTEM_ID64);

    expectReadOnlyCall(
      fetchMock,
      '/api/colony-planner/system/12866676218109/provenance-cockpit',
    );
  });

  it('calls the Stage 18H.2 warehouse planner evidence endpoint with a GET/read-only request only', async () => {
    const fetchMock = stubFetch();

    await warehousePlannerEvidence(SYSTEM_ID64);

    expectReadOnlyCall(
      fetchMock,
      '/api/colony-planner/system/12866676218109/warehouse-planner-evidence',
    );
  });

  it('calls the system evidence summary endpoint with a GET/read-only request only', async () => {
    const fetchMock = stubFetch();

    await evidenceSystemSummary(SYSTEM_ID64);

    expectReadOnlyCall(fetchMock, '/api/evidence/systems/12866676218109/summary');
  });

  it('calls the journal telemetry summary endpoint with a GET/read-only request only', async () => {
    const fetchMock = stubFetch();

    await journalTelemetry('sync-key-1234567890');

    expectReadOnlyCall(fetchMock, '/api/journal/telemetry/sync-key-1234567890');
  });
});

describe('planner API URL contracts', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it.each([
    ['archetype system', () => archetypeSystem(SYSTEM_ID64), '/api/archetypes/system/12866676218109'],
    ['simulation summary', () => simulationSummary(SYSTEM_ID64), '/api/systems/12866676218109/simulation-summary'],
    ['slot predictions', () => slotPredictions(SYSTEM_ID64), '/api/systems/12866676218109/slot-predictions'],
    ['buildability', () => buildability(SYSTEM_ID64), '/api/systems/12866676218109/buildability'],
    ['recommended builds', () => recommendedBuilds(SYSTEM_ID64), '/api/systems/12866676218109/recommended-builds'],
    ['journal receipt', () => journalImportReceipt('run-key'), '/api/journal/imports/run-key'],
    ['regional analysis', () => regionalAnalysis(SYSTEM_ID64), '/api/systems/12866676218109/regional-analysis'],
    ['facility templates', () => facilityTemplates(), '/api/facility-templates'],
    ['profile sync pull', () => profileSyncPull('sync-key'), '/api/profile/sync/sync-key'],
  ])('calls the %s endpoint with a GET/read-only request', async (_name, invoke, expectedUrl) => {
    const fetchMock = stubFetch();

    await invoke();

    expectReadOnlyCall(fetchMock, expectedUrl);
  });

  it('adds and encodes an archetype query only when supplied', async () => {
    const fetchMock = stubFetch();

    await simulationSummary(SYSTEM_ID64, 'trade / logistics');
    await buildability(SYSTEM_ID64, 'trade / logistics');
    await recommendedBuilds(SYSTEM_ID64, 'trade / logistics');

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/systems/12866676218109/simulation-summary?archetype=trade+%2F+logistics',
      '/api/systems/12866676218109/buildability?archetype=trade+%2F+logistics',
      '/api/systems/12866676218109/recommended-builds?archetype=trade+%2F+logistics',
    ]);
  });

  it('encodes journal and profile keys as path segments', async () => {
    const fetchMock = stubFetch();

    await journalImportReceipt('run/a b');
    await journalTelemetry('sync/a b');
    await profileSyncPull('profile/a b');

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/journal/imports/run%2Fa%20b',
      '/api/journal/telemetry/sync%2Fa%20b',
      '/api/profile/sync/profile%2Fa%20b',
    ]);
  });

  it('unwraps the canonical record and retains the legacy system fallback', async () => {
    const canonical = { id64: SYSTEM_ID64, name: 'Canonical' };
    const legacy = { id64: SYSTEM_ID64, name: 'Legacy' };
    const losslessCanonical = { id64: String(SYSTEM_ID64), name: 'Canonical' };
    const losslessLegacy = { id64: String(SYSTEM_ID64), name: 'Legacy' };
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => responseJson())
      .mockResolvedValueOnce(responseJson({ record: canonical, system: legacy }))
      .mockResolvedValueOnce(responseJson({ record: null, system: legacy }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(system(SYSTEM_ID64)).resolves.toEqual(losslessCanonical);
    await expect(system(SYSTEM_ID64)).resolves.toEqual(losslessLegacy);
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/system/12866676218109',
      '/api/system/12866676218109',
    ]);
  });
});

describe('planner API request body contracts', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('posts simulation placements without changing their IDs or ordering', async () => {
    const fetchMock = stubFetch();
    const request: SimulateBuildRequest = {
      system_id64: SYSTEM_ID64,
      target_archetype: 'trade_logistics',
      placements: [
        {
          facility_template_id: 'orbital-starport',
          local_body_id: '9007199254740993',
          is_primary_port: true,
          build_order: 2,
        },
        {
          facility_template_id: 'surface-outpost',
          local_body_id: '7',
          build_order: 1,
        },
      ],
    };

    await simulateBuild(request);

    expect(fetchMock).toHaveBeenCalledWith('/api/simulate/build', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify(request),
    }));
  });

  it('posts the exact journal staging envelope with lossless Id64 strings and privacy evidence', async () => {
    const fetchMock = stubFetch();
    const request: JournalImportRequest = {
      sync_key: 'sync-key-1234567890',
      client_manifest: {
        parser_version: 'journal-parser-v1',
        files: [{ name: 'Journal.2026-09-04T120000.01.log', event_count: 1 }],
      },
      evidence_mode: 'staging_only',
      observations: [{
        observation_key: 'obs-1',
        source_file: 'Journal.2026-09-04T120000.01.log',
        source_offset: 12,
        event_type: 'FSDJump',
        observed_at: '2026-09-04T12:00:00Z',
        system_id64: '9007199254740993',
        system_name: 'Lossless Test',
        subject_type: 'system',
        subject_id: '9007199254740993',
        summary: null,
        payload: { StarSystem: 'Lossless Test', SystemAddress: '9007199254740993' },
        privacy_boundary: { commander_name_removed: true },
      }],
    };

    await importJournal(request);

    expect(fetchMock).toHaveBeenCalledWith('/api/journal/import', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify(request),
    }));
    const body = JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string) as JournalImportRequest;
    expect(body.evidence_mode).toBe('staging_only');
    expect(body.observations[0]?.system_id64).toBe('9007199254740993');
    expect(body.observations[0]?.privacy_boundary).toEqual({ commander_name_removed: true });
  });

  it('uses the spansh layout source by default and preserves an explicit body', async () => {
    const fetchMock = stubFetch();

    await importSystemLayout(SYSTEM_ID64);
    await importSystemLayout(SYSTEM_ID64, { source: 'spansh' });

    expect(fetchMock.mock.calls).toHaveLength(2);
    for (const [url, init] of fetchMock.mock.calls) {
      expect(String(url)).toBe('/api/colony-planner/system/12866676218109/import-layout');
      expect(init).toEqual(expect.objectContaining({
        method: 'POST',
        body: '{"source":"spansh"}',
      }));
    }
  });

  it('injects optimiser defaults without mutating the caller request', async () => {
    const fetchMock = stubFetch();
    const request: OptimiserCandidatesRequest = { system_id64: SYSTEM_ID64 };

    await optimiserCandidates(request);

    expect(JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string)).toEqual({
      max_candidates: 5,
      allow_estimated_data: true,
      run_preview: true,
      include_ranking: true,
      system_id64: SYSTEM_ID64,
    });
    expect(request).toEqual({ system_id64: SYSTEM_ID64 });
  });

  it('lets explicit optimiser options override every injected default', async () => {
    const fetchMock = stubFetch();
    const request: OptimiserCandidatesRequest = {
      system_id64: SYSTEM_ID64,
      max_candidates: 9,
      allow_estimated_data: false,
      run_preview: false,
      include_ranking: false,
      preferred_body_ids: ['9007199254740993'],
    };

    await optimiserCandidates(request);

    expect(JSON.parse((fetchMock.mock.calls[0]?.[1] as RequestInit).body as string)).toEqual(request);
  });

  it('wraps the profile blob in the persisted sync envelope and encodes the key', async () => {
    const fetchMock = stubFetch();
    const blob = {
      schema_version: 1,
      selected_system_id64: '9007199254740993',
      planner: { target_archetype: 'trade_logistics' },
    };

    await profileSyncPush('profile/a b', blob);

    expect(fetchMock).toHaveBeenCalledWith('/api/profile/sync/profile%2Fa%20b', expect.objectContaining({
      method: 'PUT',
      body: JSON.stringify({ blob }),
    }));
  });

  it('posts archetype reranking separately from optimiser candidate generation', async () => {
    const fetchMock = stubFetch();
    const request: DevelopmentRerankRequest = {
      id64s: [SYSTEM_ID64],
      archetype: 'manufacturing_hub',
      profile: null,
    };

    await archetypeRerank(request);

    expect(fetchMock).toHaveBeenCalledWith('/api/archetypes/rerank', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify(request),
    }));
  });
});
