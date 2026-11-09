import { describe, expect, it } from 'vitest';
import { buildExportArtifacts } from './exportArtifacts';


describe('buildExportArtifacts', () => {
  it('separates planned, projected, observed, inferred, and warehouse evidence in all export formats', () => {
    const artifacts = buildExportArtifacts({
      system: { id64: 12866676218109, name: 'Shinrarta Dezhra' } as never,
      targetArchetype: 'refinery_industrial',
      placements: [
        { facility_template_id: 'orbital_port_small', local_body_id: '12', is_primary_port: true, build_order: 1 },
      ] as never,
      templates: [{ id: 'orbital_port_small', name: 'Orbital Port', is_port: true }] as never,
      bodies: [{ id: 12, name: 'Body A' }] as never,
      previewResult: {
        final_score: 88,
        cp: { yellow_cp_final: 12, green_cp_final: -2, t2_ports: 1, t3_ports: 0, warnings: [] },
        cp_timeline: [],
        cp_repair_suggestions: [],
      } as never,
      previewResultStale: false,
      roleReview: { consistencyLabel: 'Aligned' } as never,
      observedFacts: [
        { fact_type: 'service_presence' },
        { fact_type: 'cp_value' },
      ] as never,
      provenance: {
        evidence_panels: { warehouse: { state: 'available', report_only: true, stale_records: 0 } },
        guardrails: {
          stage19_paused: true,
          stage19_production_activation_complete: false,
          next_stage19_write_lane_authorized: false,
          canonical_apply_complete: false,
          rebaseline_complete: false,
          scheduler_enabled: false,
          db_writes_authorized: false,
          stage19_operator_commands_authorized: false,
        },
      } as never,
    });

    expect(artifacts.markdown).toContain('## Planned');
    expect(artifacts.markdown).toContain('## Projected');
    expect(artifacts.markdown).toContain('## Observed');
    expect(artifacts.markdown).toContain('## Inferred');
    expect(artifacts.markdown).toContain('## Warehouse');
    expect(artifacts.markdown).toContain('## Guardrails');
    expect(artifacts.markdown).toContain('## Operator review');
    expect(artifacts.markdown).toContain('## Governance');
    expect(artifacts.json).toContain('"planned"');
    expect(artifacts.json).toContain('"projected"');
    expect(artifacts.json).toContain('"observed"');
    expect(artifacts.json).toContain('"inferred"');
    expect(artifacts.json).toContain('"warehouse"');
    expect(artifacts.json).toContain('"operator_review"');
    expect(artifacts.json).toContain('"governance"');
    expect(artifacts.csv).toContain('step,facility_template_id,facility_name,body_name,is_primary_port');
    expect(artifacts.csv).toContain('1,orbital_port_small,Orbital Port,Body A,true');
    expect(artifacts.readiness.closeout_ready).toBe(true);
    expect(artifacts.operatorReview.ready).toBe(true);
    expect(artifacts.operatorReview.references.warehouse_state).toBe('available');
    expect(artifacts.governance.documentation_references).toContain('stage-22d-export-and-documentation-governance-consolidation.md');
  });

  it('marks closeout as not ready when plan or preview is missing', () => {
    const artifacts = buildExportArtifacts({
      system: { id64: 1, name: 'Test' } as never,
      targetArchetype: 'refinery_industrial',
      placements: [] as never,
      templates: [] as never,
      bodies: [] as never,
      previewResult: null,
      previewResultStale: false,
      roleReview: null,
      observedFacts: [] as never,
      provenance: null,
    });

    expect(artifacts.readiness.closeout_ready).toBe(false);
    expect(artifacts.readiness.reasons).toContain('Build plan is empty.');
    expect(artifacts.readiness.reasons).toContain('Preview has not been run.');
    expect(artifacts.readiness.reasons).toContain('Provenance cockpit is unavailable.');
    expect(artifacts.operatorReview.ready).toBe(false);
    expect(artifacts.operatorReview.focus_items).toContain('Build plan is empty.');
    expect(artifacts.governance.authority_scope).toMatch(/not planner authority/i);
  });
});
