import type { SimulateBuildPlacement } from '@ed-finder/api-client/types';
import {
  normaliseDeclaredRoles,
  type DeclaredColonyRole,
} from './colonyRoles';
import type {
  ColonyProjectCreatedFrom,
  ColonyProjectObjective,
  ColonyProjectStartApproach,
} from './plannerDraftContext';

export type ColonyProjectStatus = 'draft' | 'ready_to_build' | 'building' | 'established';

export interface ColonyProject {
  id: string;
  system_id64: number;
  system_name: string;
  project_name: string;
  build_plan_placements: SimulateBuildPlacement[];
  selected_body_assignments: Record<number, string | null>;
  declared_roles: DeclaredColonyRole[];
  target_archetype: string;
  notes: string;
  status: ColonyProjectStatus;
  objective?: ColonyProjectObjective | null;
  start_approach?: ColonyProjectStartApproach | null;
  created_from?: ColonyProjectCreatedFrom | null;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
}

export interface ColonyProjectInput {
  system_id64: number;
  system_name: string;
  project_name: string;
  build_plan_placements: SimulateBuildPlacement[];
  declared_roles?: DeclaredColonyRole[];
  target_archetype: string;
  notes: string;
  status?: ColonyProjectStatus;
  objective?: ColonyProjectObjective | null;
  start_approach?: ColonyProjectStartApproach | null;
  created_from?: ColonyProjectCreatedFrom | null;
}

type DeclaredRolePlanSnapshot = Pick<
  DeclaredColonyRole,
  'id' | 'body_id' | 'role_id' | 'source' | 'label' | 'confidence'
>;

export function buildColonyProject(
  input: ColonyProjectInput,
  existing: ColonyProject | null,
  now: string,
  projectId: string,
): ColonyProject {
  return {
    id: existing?.id ?? projectId,
    system_id64: input.system_id64,
    system_name: input.system_name,
    project_name: input.project_name.trim() || `${input.system_name || 'Colony'} project`,
    build_plan_placements: cloneColonyProjectPlacements(input.build_plan_placements),
    selected_body_assignments: colonyProjectBodyAssignments(input.build_plan_placements),
    declared_roles: normaliseDeclaredRoles(input.declared_roles ?? existing?.declared_roles ?? []),
    target_archetype: input.target_archetype,
    notes: input.notes,
    status: input.status ?? existing?.status ?? 'draft',
    objective: input.objective ?? existing?.objective ?? null,
    start_approach: input.start_approach ?? existing?.start_approach ?? null,
    created_from: input.created_from ?? existing?.created_from ?? null,
    created_at: existing?.created_at ?? now,
    updated_at: now,
    archived_at: null,
  };
}

export function duplicateColonyProject(
  source: ColonyProject,
  now: string,
  projectId: string,
): ColonyProject {
  return {
    ...source,
    id: projectId,
    project_name: `${source.project_name} - Copy`,
    build_plan_placements: cloneColonyProjectPlacements(source.build_plan_placements),
    selected_body_assignments: { ...source.selected_body_assignments },
    declared_roles: normaliseDeclaredRoles(source.declared_roles),
    status: 'draft',
    created_at: now,
    updated_at: now,
    archived_at: null,
  };
}

export function activeProjectsForSystem(
  projects: ColonyProject[],
  systemId64: number,
): ColonyProject[] {
  return projects
    .filter((project) => project.system_id64 === systemId64 && !project.archived_at)
    .map((project) => normaliseColonyProject(project))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

export function projectMatchesSnapshot(
  project: ColonyProject | null,
  placements: SimulateBuildPlacement[],
  targetArchetype: string,
  notes: string,
  name: string,
  declaredRoles: DeclaredColonyRole[] = [],
): boolean {
  if (!project) return placements.length === 0 && notes.trim() === '' && declaredRoles.length === 0;
  return project.project_name === name
    && project.target_archetype === targetArchetype
    && project.notes === notes
    && JSON.stringify(project.build_plan_placements) === JSON.stringify(cloneColonyProjectPlacements(placements))
    && JSON.stringify(declaredRolesPlanSnapshot(project.declared_roles)) === JSON.stringify(declaredRolesPlanSnapshot(declaredRoles));
}

function declaredRolesPlanSnapshot(
  roles: DeclaredColonyRole[],
): DeclaredRolePlanSnapshot[] {
  return normaliseDeclaredRoles(roles).map((role) => ({
    id: role.id,
    body_id: role.body_id,
    role_id: role.role_id,
    source: role.source,
    label: role.label,
    confidence: role.confidence,
  }));
}

export function cloneColonyProjectPlacements(
  placements: SimulateBuildPlacement[],
): SimulateBuildPlacement[] {
  if (!Array.isArray(placements)) return [];
  return placements
    .map<SimulateBuildPlacement | null>((placement, index) => {
      if (!placement || typeof placement !== 'object') return null;
      const candidate = placement as Partial<SimulateBuildPlacement>;
      if (typeof candidate.facility_template_id !== 'string' || !candidate.facility_template_id.trim()) return null;
      const buildOrder = typeof candidate.build_order === 'number' && Number.isFinite(candidate.build_order)
        ? candidate.build_order
        : index + 1;
      return {
        facility_template_id: candidate.facility_template_id,
        local_body_id: candidate.local_body_id == null ? null : String(candidate.local_body_id),
        is_primary_port: Boolean(candidate.is_primary_port),
        build_order: buildOrder,
      };
    })
    .filter((placement): placement is SimulateBuildPlacement => Boolean(placement));
}

export function colonyProjectBodyAssignments(
  placements: SimulateBuildPlacement[],
): Record<number, string | null> {
  return placements.reduce<Record<number, string | null>>((assignments, placement, index) => {
    assignments[index] = placement.local_body_id ?? null;
    return assignments;
  }, {});
}

/**
 * Legacy React/Zustand migration adapter. The V3 Svelte persistence codec is
 * the canonical lossless store boundary and deliberately does not call this
 * numeric compatibility helper.
 */
export function normaliseLegacyReactColonyProjectRecord(
  projects: unknown,
): Record<string, ColonyProject> {
  const entries = Array.isArray(projects)
    ? projects
    : projects && typeof projects === 'object'
      ? Object.values(projects)
      : [];

  return entries.reduce<Record<string, ColonyProject>>((record, project) => {
    if (!project || typeof project !== 'object') return record;
    const candidate = project as ColonyProject;
    if (!candidate.id) return record;
    record[candidate.id] = normaliseColonyProject(candidate);
    return record;
  }, {});
}

export function normaliseColonyProjectStatus(status: unknown): ColonyProjectStatus {
  if (status === 'ready_to_build' || status === 'building' || status === 'established') {
    return status;
  }
  return 'draft';
}

export function createColonyProjectId(systemId64: number): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `colony-${systemId64}-${random}`;
}

function normaliseColonyProject(project: ColonyProject): ColonyProject {
  return {
    ...project,
    build_plan_placements: cloneColonyProjectPlacements(project.build_plan_placements),
    selected_body_assignments: project.selected_body_assignments && typeof project.selected_body_assignments === 'object'
      ? project.selected_body_assignments
      : {},
    declared_roles: normaliseDeclaredRoles(project.declared_roles),
    objective: project.objective ?? null,
    start_approach: project.start_approach ?? null,
    created_from: project.created_from ?? null,
    status: normaliseColonyProjectStatus(project.status),
    archived_at: project.archived_at ?? null,
  };
}
