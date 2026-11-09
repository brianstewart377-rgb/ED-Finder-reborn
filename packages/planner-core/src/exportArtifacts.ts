import type {
  FacilityTemplate,
  ObservedFact,
  ProvenanceCockpitResponse,
  SimulateBuildPlacement,
  SimulateBuildResponse,
  SystemBody,
  SystemDetail,
} from '@ed-finder/api-client/types';
import type { RoleReviewResult } from './colonyRoleReview';


export interface ExportArtifacts {
  markdown: string;
  json: string;
  csv: string;
  readiness: {
    closeout_ready: boolean;
    reasons: string[];
  };
  operatorReview: {
    ready: boolean;
    focus_items: string[];
    safeguards: string[];
    sections: {
      planned: boolean;
      projected: boolean;
      observed: boolean;
      inferred: boolean;
      warehouse: boolean;
      guardrails: boolean;
    };
    references: {
      system_name: string | null;
      system_id64: number;
      source_run_key: string | null;
      artifact_name: string | null;
      warehouse_state: string;
      provenance_warnings: string[];
    };
  };
  governance: {
    authority_scope: string;
    exclusions: string[];
    documentation_references: string[];
    historical_status: string;
  };
}

export function buildExportArtifacts({
  system,
  targetArchetype,
  placements,
  templates,
  bodies,
  previewResult,
  previewResultStale,
  roleReview,
  observedFacts,
  provenance,
}: {
  system: SystemDetail;
  targetArchetype: string;
  placements: SimulateBuildPlacement[];
  templates: FacilityTemplate[];
  bodies: SystemBody[];
  previewResult: SimulateBuildResponse | null;
  previewResultStale: boolean;
  roleReview: RoleReviewResult | null | undefined;
  observedFacts: ObservedFact[];
  provenance: ProvenanceCockpitResponse | null | undefined;
}): ExportArtifacts {
  const plannedRows = placements
    .slice()
    .sort((a, b) => (a.build_order ?? 0) - (b.build_order ?? 0))
    .map((placement, index) => {
      const template = templates.find((item) => item.id === placement.facility_template_id);
      const body = placement.local_body_id ? bodies.find((item) => String(item.id) === String(placement.local_body_id)) : null;
      return {
        step: placement.build_order ?? index + 1,
        facility_template_id: placement.facility_template_id,
        facility_name: template?.name ?? placement.facility_template_id,
        body_name: body?.name ?? '',
        is_primary_port: Boolean(placement.is_primary_port),
      };
    });

  const observedByType = countBy(observedFacts.map((fact) => fact.fact_type));
  const reasons: string[] = [];
  if (plannedRows.length === 0) reasons.push('Build plan is empty.');
  if (!previewResult) reasons.push('Preview has not been run.');
  if (previewResultStale) reasons.push('Preview result is stale.');
  if (!provenance) reasons.push('Provenance cockpit is unavailable.');

  const closeoutReady = reasons.length === 0;
  const operatorReviewSections = {
    planned: plannedRows.length > 0,
    projected: Boolean(previewResult),
    observed: observedFacts.length > 0,
    inferred: Boolean(roleReview),
    warehouse: Boolean(provenance?.evidence_panels.warehouse),
    guardrails: Boolean(provenance?.guardrails),
  };
  const operatorReviewFocusItems = [
    ...(reasons.length > 0 ? reasons : ['Current planner/export state has no closeout blockers.']),
    provenance?.evidence_panels.warehouse.state === 'stale'
      ? 'Warehouse evidence is stale and should be reviewed before operator handoff.'
      : null,
    provenance?.warnings?.length
      ? `Provenance warnings require review (${provenance.warnings.length}).`
      : null,
  ].filter((item): item is string => Boolean(item));
  const operatorReviewSafeguards = [
    'Read-only export only; no operator commands, DB writes, or scheduler hooks are triggered.',
    'Planned, projected, observed, inferred, warehouse, and guardrail sections remain separated.',
    'Private paths, secrets, runtime source files, and operator artifact JSON are excluded from the exported pack.',
    'Source run references are informational review aids only and do not become planner authority.',
  ];
  const operatorReview = {
    ready: closeoutReady && provenance?.evidence_panels.warehouse.state !== 'stale',
    focus_items: operatorReviewFocusItems,
    safeguards: operatorReviewSafeguards,
    sections: operatorReviewSections,
    references: {
      system_name: system.name ?? null,
      system_id64: system.id64,
      source_run_key: provenance?.provenance_summary?.latest_source_run_key ?? null,
      artifact_name: provenance?.evidence_panels?.source_run?.artifact_name ?? null,
      warehouse_state: provenance?.evidence_panels.warehouse.state ?? 'unknown',
      provenance_warnings: provenance?.warnings ?? [],
    },
  };
  const governance = {
    authority_scope:
      'Review/export artifact only. This pack is not planner authority and does not authorize Stage 19 work.',
    exclusions: [
      'Private filesystem paths are excluded.',
      'Secrets, admin tokens, and DSNs are excluded.',
      'Runtime source files are excluded.',
      'Operator artifact JSON is excluded as committed authority.',
    ],
    documentation_references: [
      'stage-20e-export-operator-pack-closeout-readiness.md',
      'stage-22c-operator-artifact-review-and-audit-surfaces.md',
      'stage-22d-export-and-documentation-governance-consolidation.md',
    ],
    historical_status:
      'Historical closeouts remain review context only; current authority stays in the Stage 22 roadmap and state authority file.',
  };

  const payload = {
    system: {
      id64: system.id64,
      name: system.name ?? null,
    },
    planned: {
      target_archetype: targetArchetype,
      placements: plannedRows,
    },
    projected: {
      available: Boolean(previewResult),
      stale: previewResultStale,
      score: previewResult?.final_score ?? null,
      cp: previewResult?.cp ?? null,
      cp_timeline: previewResult?.cp_timeline ?? [],
      cp_repair_suggestions: previewResult?.cp_repair_suggestions ?? [],
    },
    observed: {
      total_facts: observedFacts.length,
      by_fact_type: observedByType,
    },
    inferred: {
      role_review_consistency: roleReview?.consistencyLabel ?? 'No role review',
    },
    warehouse: provenance
      ? {
          state: provenance.evidence_panels.warehouse.state,
          report_only: provenance.evidence_panels.warehouse.report_only,
          stale_records: provenance.evidence_panels.warehouse.stale_records ?? null,
        }
      : null,
    guardrails: provenance?.guardrails ?? null,
    closeout_readiness: {
      closeout_ready: closeoutReady,
      reasons,
    },
    operator_review: operatorReview,
    governance,
  };

  const markdown = [
    `# Stage 20 planning export pack`,
    ``,
    `System: ${system.name ?? `ID64 ${system.id64}`}`,
    `Target archetype: ${targetArchetype}`,
    ``,
    `## Planned`,
    plannedRows.length > 0
      ? plannedRows.map((row) => `- ${row.step}. ${row.facility_name} (${row.body_name || 'Unassigned'})${row.is_primary_port ? ' [primary port]' : ''}`).join('\n')
      : `- No placements`,
    ``,
    `## Projected`,
    `- Preview available: ${previewResult ? 'yes' : 'no'}`,
    `- Preview stale: ${previewResultStale ? 'yes' : 'no'}`,
    `- Overall score: ${previewResult?.final_score ?? 'unknown'}`,
    ``,
    `## Observed`,
    `- Total facts: ${observedFacts.length}`,
    ...Object.entries(observedByType).map(([factType, count]) => `- ${factType}: ${count}`),
    ``,
    `## Inferred`,
    `- Role review: ${roleReview?.consistencyLabel ?? 'No role review'}`,
    ``,
    `## Warehouse`,
    `- Warehouse state: ${provenance?.evidence_panels.warehouse.state ?? 'unknown'}`,
    `- Report only: ${provenance?.evidence_panels.warehouse.report_only ? 'yes' : 'no'}`,
    `- Stale records: ${provenance?.evidence_panels.warehouse.stale_records ?? 'unknown'}`,
    ``,
    `## Guardrails`,
    provenance
      ? Object.entries(provenance.guardrails).map(([key, value]) => `- ${key}: ${value ? 'true' : 'false'}`).join('\n')
      : `- Guardrails unavailable`,
    ``,
    `## Closeout readiness`,
    `- Ready: ${closeoutReady ? 'yes' : 'no'}`,
    ...(reasons.length > 0 ? reasons.map((reason) => `- ${reason}`) : ['- No blockers']),
    ``,
    `## Operator review`,
    `- Review ready: ${operatorReview.ready ? 'yes' : 'no'}`,
    `- Source run key: ${operatorReview.references.source_run_key ?? 'unknown'}`,
    `- Artifact reference: ${operatorReview.references.artifact_name ?? 'unknown'}`,
    `- Warehouse state: ${operatorReview.references.warehouse_state}`,
    ...operatorReview.focus_items.map((reason) => `- Focus: ${reason}`),
    ...operatorReview.safeguards.map((item) => `- Safeguard: ${item}`),
    ``,
    `## Governance`,
    `- Authority scope: ${governance.authority_scope}`,
    `- Historical status: ${governance.historical_status}`,
    ...governance.exclusions.map((item) => `- Exclusion: ${item}`),
    ...governance.documentation_references.map((item) => `- Reference: ${item}`),
    ``,
    `No private paths, secrets, runtime source files, or operator artifacts are included in this export pack.`,
  ].join('\n');

  const csvLines = [
    'step,facility_template_id,facility_name,body_name,is_primary_port',
    ...plannedRows.map((row) =>
      [row.step, row.facility_template_id, escapeCsv(row.facility_name), escapeCsv(row.body_name), row.is_primary_port ? 'true' : 'false'].join(','),
    ),
  ];

  return {
    markdown,
    json: JSON.stringify(payload, null, 2),
    csv: csvLines.join('\n'),
    readiness: {
      closeout_ready: closeoutReady,
      reasons,
    },
    operatorReview,
    governance,
  };
}

function countBy(values: string[]) {
  return values.reduce<Record<string, number>>((acc, value) => {
    acc[value] = (acc[value] ?? 0) + 1;
    return acc;
  }, {});
}

function escapeCsv(value: string) {
  if (!value.includes(',') && !value.includes('"')) return value;
  return `"${value.replaceAll('"', '""')}"`;
}
