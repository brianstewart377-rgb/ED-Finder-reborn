import { describe, expect, it } from 'vitest';
import type { SimulateBuildResponse } from '@ed-finder/api-client/types';
import { previewResultFingerprint, validationInputProjection } from './validationUtils';

function validationPreview(): SimulateBuildResponse {
  return {
    final_score: 80,
    confidence: 0.7,
    cp: {
      yellow_cp_final: 1,
      green_cp_final: 2,
      yellow_cp_generated: 3,
      green_cp_generated: 4,
      yellow_cp_spent: 5,
      green_cp_spent: 6,
      t2_ports: 7,
      t3_ports: 8,
      warnings: ['baseline warning'],
    },
    economy_composition: { industrial: 1 },
    economy_order: ['industrial'],
    services: {
      market: { status: 'active' },
    },
    port_service_states: [{
      active_services: { market: {} },
      locked_services: { shipyard: {} },
      unknown_services: { vista_genomics: {} },
    }],
  } as unknown as SimulateBuildResponse;
}

type BackendReadChange = [string, (preview: SimulateBuildResponse) => void];

const backendReadChanges: BackendReadChange[] = [
  ['final_score', (preview) => { preview.final_score += 1; }],
  ['confidence', (preview) => { preview.confidence += 0.01; }],
  ['cp.yellow_cp_final', (preview) => { preview.cp.yellow_cp_final += 1; }],
  ['cp.green_cp_final', (preview) => { preview.cp.green_cp_final += 1; }],
  ['cp.yellow_cp_generated', (preview) => { preview.cp.yellow_cp_generated += 1; }],
  ['cp.green_cp_generated', (preview) => { preview.cp.green_cp_generated += 1; }],
  ['cp.yellow_cp_spent', (preview) => { preview.cp.yellow_cp_spent += 1; }],
  ['cp.green_cp_spent', (preview) => { preview.cp.green_cp_spent += 1; }],
  ['cp.t2_ports', (preview) => { preview.cp.t2_ports += 1; }],
  ['cp.t3_ports', (preview) => { preview.cp.t3_ports += 1; }],
  ['cp.warnings', (preview) => { preview.cp.warnings.push('changed warning'); }],
  ['economy_composition', (preview) => { preview.economy_composition.industrial += 1; }],
  ['economy_order', (preview) => { preview.economy_order[0] = 'tourism'; }],
  ['services status', (preview) => { preview.services.market.status = 'locked'; }],
  ['port_service_states.active_services', (preview) => {
    preview.port_service_states[0].active_services.repair = {} as never;
  }],
  ['port_service_states.locked_services', (preview) => {
    preview.port_service_states[0].locked_services.refuel = {} as never;
  }],
  ['port_service_states.unknown_services', (preview) => {
    preview.port_service_states[0].unknown_services.bartender = {} as never;
  }],
];

describe('validation cache input', () => {
  it.each(backendReadChanges)('changes the fingerprint when %s changes', (_field, change) => {
    const before = validationPreview();
    const after = structuredClone(before);
    change(after);

    expect(previewResultFingerprint(after)).not.toBe(previewResultFingerprint(before));
  });

  it('uses the same complete projection for the request and fingerprint', () => {
    const preview = validationPreview();

    expect(previewResultFingerprint(preview)).toBe(
      JSON.stringify(validationInputProjection(preview)),
    );
    expect(validationInputProjection(preview)).toMatchObject({
      cp: {
        yellow_cp_generated: 3,
        green_cp_generated: 4,
        yellow_cp_spent: 5,
        green_cp_spent: 6,
      },
      port_service_states: [{
        active_services: { market: true },
        locked_services: { shipyard: true },
        unknown_services: { vista_genomics: true },
      }],
    });
  });
});
