import type { SystemBody } from '@ed-finder/api-client/types';

export type RoleSource = 'inferred' | 'declared' | 'observed';
export type RoleConfidence = 'tentative' | 'likely' | 'strong';

export type DeclaredColonyRoleId =
  | 'colony_anchor'
  | 'main_station_body'
  | 'primary_port_body'
  | 'industrial_core'
  | 'refinery_core'
  | 'extraction_support'
  | 'tourism_agriculture_body'
  | 'security_military_body'
  | 'support_body'
  | 'expansion_reserve';

export interface ColonyBodyRole {
  id: string;
  body_id: string;
  role_id: DeclaredColonyRoleId;
  source: RoleSource;
  confidence?: RoleConfidence;
  label: string;
  created_at?: string;
  updated_at?: string;
}

export interface DeclaredColonyRole extends ColonyBodyRole {
  source: 'declared';
}

export const DECLARED_COLONY_ROLE_OPTIONS: Array<{
  id: DeclaredColonyRoleId;
  label: string;
  compactLabel: string;
}> = [
  { id: 'colony_anchor', label: 'Colony Anchor', compactLabel: 'Anchor' },
  { id: 'main_station_body', label: 'Main Station Body', compactLabel: 'Main Station' },
  { id: 'primary_port_body', label: 'Primary Port Body', compactLabel: 'Primary Port' },
  { id: 'industrial_core', label: 'Industrial Core', compactLabel: 'Industrial' },
  { id: 'refinery_core', label: 'Refinery Core', compactLabel: 'Refinery' },
  { id: 'extraction_support', label: 'Extraction Support', compactLabel: 'Extraction' },
  { id: 'tourism_agriculture_body', label: 'Tourism / Agriculture Body', compactLabel: 'Tourism / Agri' },
  { id: 'security_military_body', label: 'Security / Military Body', compactLabel: 'Security' },
  { id: 'support_body', label: 'Support Body', compactLabel: 'Support' },
  { id: 'expansion_reserve', label: 'Expansion Reserve', compactLabel: 'Reserve' },
];

const OPTIONS_BY_ID = new Map(DECLARED_COLONY_ROLE_OPTIONS.map((option) => [option.id, option]));

export function roleLabel(roleId: DeclaredColonyRoleId): string {
  return OPTIONS_BY_ID.get(roleId)?.label ?? roleId;
}

export function roleCompactLabel(roleId: DeclaredColonyRoleId): string {
  return OPTIONS_BY_ID.get(roleId)?.compactLabel ?? roleLabel(roleId);
}

export function rolesForBody(roles: DeclaredColonyRole[], bodyId: string | null): DeclaredColonyRole[] {
  if (!bodyId) return [];
  return roles.filter((role) => role.body_id === bodyId);
}

export function normaliseDeclaredRoles(input: unknown): DeclaredColonyRole[] {
  if (!Array.isArray(input)) return [];
  const roles: DeclaredColonyRole[] = [];
  for (const item of input) {
    if (!item || typeof item !== 'object') continue;
    const raw = item as Partial<DeclaredColonyRole>;
    if (!raw.body_id || typeof raw.body_id !== 'string') continue;
    if (!raw.role_id || !OPTIONS_BY_ID.has(raw.role_id)) continue;
    const now = raw.created_at ?? new Date().toISOString();
    roles.push({
      id: typeof raw.id === 'string' && raw.id ? raw.id : declaredRoleKey(raw.body_id, raw.role_id),
      body_id: raw.body_id,
      role_id: raw.role_id,
      source: 'declared',
      label: roleLabel(raw.role_id),
      confidence: raw.confidence,
      created_at: now,
      updated_at: raw.updated_at ?? now,
    });
  }
  return dedupeDeclaredRoles(roles);
}

export function addDeclaredRole(
  roles: DeclaredColonyRole[],
  body: SystemBody,
  roleId: DeclaredColonyRoleId,
): DeclaredColonyRole[] {
  const bodyId = body.id != null ? String(body.id) : '';
  if (!bodyId) return roles;
  if (roles.some((role) => role.body_id === bodyId && role.role_id === roleId)) return roles;
  const now = new Date().toISOString();
  return [
    ...roles,
    {
      id: declaredRoleKey(bodyId, roleId),
      body_id: bodyId,
      role_id: roleId,
      source: 'declared',
      label: roleLabel(roleId),
      confidence: 'strong',
      created_at: now,
      updated_at: now,
    },
  ];
}

export function removeDeclaredRole(
  roles: DeclaredColonyRole[],
  bodyId: string,
  roleId: DeclaredColonyRoleId,
): DeclaredColonyRole[] {
  return roles.filter((role) => !(role.body_id === bodyId && role.role_id === roleId));
}

export function declaredRoleConflicts(roles: DeclaredColonyRole[]): string[] {
  const byBody = new Map<string, Set<DeclaredColonyRoleId>>();
  for (const role of roles) {
    const set = byBody.get(role.body_id) ?? new Set<DeclaredColonyRoleId>();
    set.add(role.role_id);
    byBody.set(role.body_id, set);
  }
  const conflicts = new Set<string>();
  for (const set of byBody.values()) {
    if ((set.has('industrial_core') || set.has('refinery_core')) && set.has('tourism_agriculture_body')) {
      conflicts.add('Role conflict: Tourism + Heavy Industrial');
    }
    if (set.has('main_station_body') && set.has('expansion_reserve')) {
      conflicts.add('Role conflict: Main Station + Expansion Reserve');
    }
    if (set.has('primary_port_body') && set.has('support_body')) {
      conflicts.add('Strategic tradeoff: Primary Port + Support Body');
    }
  }
  return Array.from(conflicts);
}

export function projectRoleSummary(roles: DeclaredColonyRole[]): string {
  if (roles.length === 0) return 'No declared roles yet';
  const bodies = new Set(roles.map((role) => role.body_id)).size;
  return `${roles.length} declared role${roles.length === 1 ? '' : 's'} across ${bodies} bod${bodies === 1 ? 'y' : 'ies'}`;
}

function dedupeDeclaredRoles(roles: DeclaredColonyRole[]): DeclaredColonyRole[] {
  const seen = new Set<string>();
  return roles.filter((role) => {
    const key = declaredRoleKey(role.body_id, role.role_id);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function declaredRoleKey(bodyId: string, roleId: DeclaredColonyRoleId): string {
  return `declared:${bodyId}:${roleId}`;
}
