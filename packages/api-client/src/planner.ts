/**
 * Retained React migration adapter. Its generated numeric signatures mirror
 * the legacy application only; the V3 Svelte boundary is the Id64-aware
 * application facade over `core.ts` and must not import this module.
 */
import type {
  DevelopmentRerankRequest,
  DevelopmentRerankResponse,
  EvidenceSystemSummaryResponse,
  FacilityTemplate,
  JournalImportReceipt,
  JournalImportRequest,
  JournalTelemetrySummaryResponse,
  LayoutImportRequest,
  LayoutImportResponse,
  OptimiserCandidatesRequest,
  OptimiserCandidatesResponse,
  ProvenanceCockpitResponse,
  RecommendedBuildsResponse,
  RegionalAnalysisResponse,
  SimulateBuildRequest,
  SimulateBuildResponse,
  SimulationSummary,
  SlotPredictionResponse,
  SystemArchetypeResponse,
  SystemBuildability,
  SystemDetail,
  SystemDetailResponse,
  WarehousePlannerEvidenceContract,
} from './types';
import { jsonFetch } from './core';

export interface ProfileSyncPull<TBlob> {
  blob:       TBlob;
  updated_at: string;
  blob_bytes: number;
}

export interface ProfileSyncPush {
  updated_at: string;
  blob_bytes: number;
}

/** Full system detail (joins ratings + bodies + stations). Cached
 *  server-side via Redis under the `sys:{id64}` key, so calling this
 *  multiple times in a row is cheap. */
export async function system(id64: number): Promise<SystemDetail> {
  const res = await jsonFetch<SystemDetailResponse>(`/system/${id64}`);
  // Endpoint returns {record, system} — same data twice for legacy compat.
  return res.record ?? res.system;
}

export function archetypeSystem(id64: number): Promise<SystemArchetypeResponse> {
  return jsonFetch(`/archetypes/system/${id64}`);
}

export function simulationSummary(id64: number, archetype?: string): Promise<SimulationSummary> {
  const params = archetype ? `?${new URLSearchParams({ archetype }).toString()}` : '';
  return jsonFetch(`/systems/${id64}/simulation-summary${params}`);
}

export function slotPredictions(id64: number): Promise<SlotPredictionResponse> {
  return jsonFetch(`/systems/${id64}/slot-predictions`);
}

export function buildability(id64: number, archetype?: string): Promise<SystemBuildability> {
  const params = archetype ? `?${new URLSearchParams({ archetype }).toString()}` : '';
  return jsonFetch(`/systems/${id64}/buildability${params}`);
}

export function recommendedBuilds(id64: number, archetype?: string): Promise<RecommendedBuildsResponse> {
  const params = archetype ? `?${new URLSearchParams({ archetype }).toString()}` : '';
  return jsonFetch(`/systems/${id64}/recommended-builds${params}`);
}

export function provenanceCockpit(id64: number): Promise<ProvenanceCockpitResponse> {
  return jsonFetch(`/colony-planner/system/${id64}/provenance-cockpit`);
}

export function warehousePlannerEvidence(id64: number): Promise<WarehousePlannerEvidenceContract> {
  return jsonFetch(`/colony-planner/system/${id64}/warehouse-planner-evidence`);
}

export function evidenceSystemSummary(id64: number): Promise<EvidenceSystemSummaryResponse> {
  return jsonFetch(`/evidence/systems/${id64}/summary`);
}

export function importJournal(request: JournalImportRequest): Promise<JournalImportReceipt> {
  return jsonFetch('/journal/import', {
    method: 'POST',
    body:   JSON.stringify(request),
  });
}

export function journalImportReceipt(runKey: string): Promise<JournalImportReceipt> {
  return jsonFetch(`/journal/imports/${encodeURIComponent(runKey)}`);
}

export function journalTelemetry(syncKey: string): Promise<JournalTelemetrySummaryResponse> {
  return jsonFetch(`/journal/telemetry/${encodeURIComponent(syncKey)}`);
}

export function regionalAnalysis(id64: number): Promise<RegionalAnalysisResponse> {
  return jsonFetch(`/systems/${id64}/regional-analysis`);
}

export function facilityTemplates(): Promise<FacilityTemplate[]> {
  return jsonFetch('/facility-templates');
}

export function simulateBuild(body: SimulateBuildRequest): Promise<SimulateBuildResponse> {
  return jsonFetch('/simulate/build', {
    method: 'POST',
    body:   JSON.stringify(body),
  });
}

export function importSystemLayout(id64: number, body: LayoutImportRequest = { source: 'spansh' }): Promise<LayoutImportResponse> {
  return jsonFetch(`/colony-planner/system/${id64}/import-layout`, {
    method: 'POST',
    body:   JSON.stringify(body),
  });
}

export function optimiserCandidates(body: OptimiserCandidatesRequest): Promise<OptimiserCandidatesResponse> {
  return jsonFetch('/optimiser/candidates', {
    method: 'POST',
    body:   JSON.stringify({
      max_candidates: 5,
      allow_estimated_data: true,
      run_preview: true,
      include_ranking: true,
      ...body,
    }),
  });
}

export function profileSyncPull<TBlob>(syncKey: string): Promise<ProfileSyncPull<TBlob>> {
  return jsonFetch(`/profile/sync/${encodeURIComponent(syncKey)}`);
}

export function profileSyncPush<TBlob>(syncKey: string, blob: TBlob): Promise<ProfileSyncPush> {
  return jsonFetch(`/profile/sync/${encodeURIComponent(syncKey)}`, {
    method: 'PUT',
    body:   JSON.stringify({ blob }),
  });
}

// ── Development Tuning / archetype rerank ────────────────────────────
// This endpoint only reorders supplied Finder result IDs; it is separate
// from Colony Planner optimiser candidates.
export function archetypeRerank(body: DevelopmentRerankRequest): Promise<DevelopmentRerankResponse> {
  return jsonFetch('/archetypes/rerank', {
    method: 'POST',
    body:   JSON.stringify(body),
  });
}
