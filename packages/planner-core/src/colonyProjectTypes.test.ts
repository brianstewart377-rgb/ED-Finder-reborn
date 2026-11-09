import { describe, expect, it, vi } from 'vitest';
import type { SimulateBuildPlacement } from '@ed-finder/api-client/types';
import {
  normaliseDeclaredRoles,
  type DeclaredColonyRole,
} from './colonyRoles';
import {
  activeProjectsForSystem,
  buildColonyProject,
  duplicateColonyProject,
  normaliseLegacyReactColonyProjectRecord,
  projectMatchesSnapshot,
  type ColonyProject,
} from './colonyProjectTypes';

const placement: SimulateBuildPlacement = {
  facility_template_id: 'orbital_port',
  local_body_id: '9007199254740993',
  is_primary_port: true,
  build_order: 1,
};

const legacyRole: DeclaredColonyRole = {
  id: 'declared:9007199254740993:main_station_body',
  body_id: '9007199254740993',
  role_id: 'main_station_body',
  source: 'declared',
  confidence: 'strong',
  label: 'Main Station Body',
};

function project(overrides: Partial<ColonyProject> = {}): ColonyProject {
  return {
    id: 'project-1',
    system_id64: 123,
    system_name: 'Workspace System',
    project_name: 'Starter project',
    build_plan_placements: [placement],
    selected_body_assignments: { 0: placement.local_body_id ?? null },
    declared_roles: [],
    target_archetype: 'refinery_industrial',
    notes: 'Check Architect mode.',
    status: 'draft',
    objective: null,
    start_approach: null,
    created_from: null,
    created_at: '2026-05-01T00:00:00.000Z',
    updated_at: '2026-05-01T00:00:00.000Z',
    archived_at: null,
    ...overrides,
  };
}

function projectMatchesDeclaredRoles(
  savedRoles: DeclaredColonyRole[],
  currentRoles: DeclaredColonyRole[],
): boolean {
  return projectMatchesSnapshot(
    project({ declared_roles: savedRoles }),
    [placement],
    'refinery_industrial',
    'Check Architect mode.',
    'Starter project',
    currentRoles,
  );
}

describe('colony project persistence model', () => {
  it('builds the existing versioned store envelope without losing body identifiers', () => {
    const built = buildColonyProject({
      system_id64: 123,
      system_name: 'Workspace System',
      project_name: 'Starter project',
      build_plan_placements: [placement],
      target_archetype: 'refinery_industrial',
      notes: 'Check Architect mode.',
    }, null, '2026-05-01T00:00:00.000Z', 'project-1');

    expect(built).toEqual(project());
    expect(built.build_plan_placements[0]).not.toBe(placement);
    expect(built.build_plan_placements[0]?.local_body_id).toBe('9007199254740993');
    expect(built.selected_body_assignments).toEqual({ 0: '9007199254740993' });
  });

  it('matches project snapshots including declared roles and normalised placements', () => {
    const saved = project({
      declared_roles: [{
        id: 'declared:9007199254740993:main_station_body',
        body_id: '9007199254740993',
        role_id: 'main_station_body',
        source: 'declared',
        label: 'Main Station Body',
      }],
    });

    expect(projectMatchesSnapshot(
      saved,
      [placement],
      'refinery_industrial',
      'Check Architect mode.',
      'Starter project',
      saved.declared_roles,
    )).toBe(true);
    expect(projectMatchesSnapshot(
      saved,
      [placement],
      'refinery_industrial',
      'Changed notes.',
      'Starter project',
      saved.declared_roles,
    )).toBe(false);
    expect(projectMatchesSnapshot(
      saved,
      [placement],
      'refinery_industrial',
      'Check Architect mode.',
      'Starter project',
      [],
    )).toBe(false);
  });

  it('matches semantically identical legacy roles normalised at different clock times', () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-05-01T00:00:00.000Z'));
      const savedRoles = normaliseDeclaredRoles([legacyRole]);
      vi.setSystemTime(new Date('2026-05-02T00:00:00.000Z'));
      const currentRoles = normaliseDeclaredRoles([legacyRole]);

      expect(savedRoles[0]?.created_at).not.toBe(currentRoles[0]?.created_at);
      expect(projectMatchesDeclaredRoles(savedRoles, currentRoles)).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    ['identity', { ...legacyRole, id: 'declared-role-2' }],
    ['body', { ...legacyRole, body_id: '42' }],
    ['role', { ...legacyRole, role_id: 'industrial_core' as const }],
    ['confidence', { ...legacyRole, confidence: 'likely' as const }],
  ])('does not match when declared role %s changes', (_field, changedRole) => {
    expect(projectMatchesDeclaredRoles([legacyRole], [changedRole])).toBe(false);
  });

  it('keeps declared role order significant', () => {
    const secondaryRole: DeclaredColonyRole = {
      ...legacyRole,
      id: 'declared:9007199254740993:industrial_core',
      role_id: 'industrial_core',
      label: 'Industrial Core',
    };

    expect(projectMatchesDeclaredRoles(
      [legacyRole, secondaryRole],
      [secondaryRole, legacyRole],
    )).toBe(false);
  });

  it('normalises legacy array records while preserving the persisted field names', () => {
    const legacy = {
      ...project(),
      id: 'legacy-project',
      declared_roles: undefined,
      selected_body_assignments: undefined,
      objective: undefined,
      start_approach: undefined,
      created_from: undefined,
      status: 'validated',
      archived_at: undefined,
    };

    const normalised = normaliseLegacyReactColonyProjectRecord([legacy])['legacy-project'];

    expect(normalised).toMatchObject({
      id: 'legacy-project',
      selected_body_assignments: {},
      declared_roles: [],
      objective: null,
      start_approach: null,
      created_from: null,
      status: 'draft',
      archived_at: null,
    });
  });

  it('returns only active projects for the requested system in newest-first order', () => {
    const older = project({ id: 'older', updated_at: '2026-05-01T00:00:00.000Z' });
    const newer = project({ id: 'newer', updated_at: '2026-05-02T00:00:00.000Z' });
    const archived = project({ id: 'archived', archived_at: '2026-05-03T00:00:00.000Z' });
    const otherSystem = project({ id: 'other', system_id64: 456 });

    expect(activeProjectsForSystem([older, archived, otherSystem, newer], 123).map(({ id }) => id)).toEqual([
      'newer',
      'older',
    ]);
  });

  it('duplicates the full project envelope without sharing mutable collections', () => {
    const source = project({
      objective: 'materials_coverage',
      start_approach: 'recommendation_assisted',
      created_from: 'system_detail',
      status: 'ready_to_build',
    });

    const duplicate = duplicateColonyProject(
      source,
      '2026-05-02T00:00:00.000Z',
      'project-2',
    );

    expect(duplicate).toMatchObject({
      id: 'project-2',
      project_name: 'Starter project - Copy',
      objective: 'materials_coverage',
      start_approach: 'recommendation_assisted',
      created_from: 'system_detail',
      status: 'draft',
      created_at: '2026-05-02T00:00:00.000Z',
      updated_at: '2026-05-02T00:00:00.000Z',
      archived_at: null,
    });
    expect(duplicate.build_plan_placements).not.toBe(source.build_plan_placements);
    expect(duplicate.selected_body_assignments).not.toBe(source.selected_body_assignments);
  });
});
