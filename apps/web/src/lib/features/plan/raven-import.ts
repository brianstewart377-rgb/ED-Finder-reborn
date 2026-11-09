export type RavenImportResult = {
  ok: boolean;
  errors: { code: string; detail: string }[];
  system: { id64: string; name: string } | null;
  sites: {
    raven_site_id: string;
    raven_build_type: string;
    name: string;
    facility_id: string;
    slot_kind: 'orbital' | 'surface';
    status: 'planned' | 'building' | 'complete' | 'demolished';
    raven_body_name: string | null;
  }[];
  slots: {
    raven_body_name: string;
    orbital_slots: number | null;
    surface_slots: number | null;
  }[];
  warnings: { code: string; detail: string }[];
};

export type RavenBodyMatchResult = {
  siteBodyRefs: (string | null)[];
  slotBodyRefs: (string | null)[];
  warnings: { code: string; detail: string }[];
};

// Source: Copy of Colonization Construction v3 (By DaftMav).xlsx,
// sheet Lists, rows 48–159; owner-supplied layout names, independent of Raven code.
export const BUILD_TYPES: ReadonlyMap<
  string,
  [facility_id: string, slot_kind: 'orbital' | 'surface']
> = new Map([
  ['no_truss', ['coriolis_station', 'orbital']],
  ['dual_truss', ['coriolis_station', 'orbital']],
  ['quad_truss', ['coriolis_station', 'orbital']],
  ['ice', ['asteroid_base', 'orbital']],
  ['metal', ['asteroid_base', 'orbital']],
  ['rock', ['asteroid_base', 'orbital']],
  ['ocellus', ['ocellus_station', 'orbital']],
  ['apollo', ['orbis_t3', 'orbital']],
  ['artemis', ['orbis_t3', 'orbital']],
  ['no_truss_dodo', ['dodecahedron_t3', 'orbital']],
  ['quint_truss', ['dodecahedron_t3', 'orbital']],
  ['dec_truss', ['dodecahedron_t3', 'orbital']],
  ['plutus', ['orbital_outpost_commercial', 'orbital']],
  ['vulcan', ['orbital_outpost_industrial', 'orbital']],
  ['dysnomia', ['orbital_outpost_criminal', 'orbital']],
  ['vesta', ['orbital_outpost_civilian', 'orbital']],
  ['prometheus', ['orbital_outpost_scientific', 'orbital']],
  ['nemesis', ['orbital_outpost_military', 'orbital']],
  ['hermes', ['orbital_installation_satellite', 'orbital']],
  ['angelia', ['orbital_installation_satellite', 'orbital']],
  ['eirene', ['orbital_installation_satellite', 'orbital']],
  ['pistis', ['orbital_installation_comm_station', 'orbital']],
  ['soter', ['orbital_installation_comm_station', 'orbital']],
  ['aletheia', ['orbital_installation_comm_station', 'orbital']],
  ['demeter', ['orbital_installation_space_farm', 'orbital']],
  ['apate', ['orbital_installation_pirate_base', 'orbital']],
  ['laverna', ['orbital_installation_pirate_base', 'orbital']],
  ['euthenia', ['orbital_installation_mining_outpost', 'orbital']],
  ['phorcys', ['orbital_installation_mining_outpost', 'orbital']],
  ['enodia', ['orbital_installation_relay_station', 'orbital']],
  ['ichnaea', ['orbital_installation_relay_station', 'orbital']],
  ['vacuna', ['orbital_installation_military', 'orbital']],
  ['alastor', ['orbital_installation_military', 'orbital']],
  ['dicaeosyne', ['orbital_installation_security_station', 'orbital']],
  ['poena', ['orbital_installation_security_station', 'orbital']],
  ['eunomia', ['orbital_installation_security_station', 'orbital']],
  ['nomos', ['orbital_installation_security_station', 'orbital']],
  ['harmonia', ['orbital_installation_government', 'orbital']],
  ['asclepius', ['orbital_installation_medical', 'orbital']],
  ['eupraxia', ['orbital_installation_medical', 'orbital']],
  ['astraeus', ['orbital_installation_research_station', 'orbital']],
  ['coeus', ['orbital_installation_research_station', 'orbital']],
  ['dodona', ['orbital_installation_research_station', 'orbital']],
  ['dione', ['orbital_installation_research_station', 'orbital']],
  ['hedone', ['tourism_installation', 'orbital']],
  ['opora', ['tourism_installation', 'orbital']],
  ['pasithea', ['tourism_installation', 'orbital']],
  ['dionysus', ['orbital_installation_space_bar', 'orbital']],
  ['bacchus', ['orbital_installation_space_bar', 'orbital']],
  ['hestia', ['surface_planetary_port_outpost_civilian', 'surface']],
  ['decima', ['surface_planetary_port_outpost_civilian', 'surface']],
  ['atropos', ['surface_planetary_port_outpost_civilian', 'surface']],
  ['nona', ['surface_planetary_port_outpost_civilian', 'surface']],
  ['lachesis', ['surface_planetary_port_outpost_civilian', 'surface']],
  ['clotho', ['surface_planetary_port_outpost_civilian', 'surface']],
  ['hephaestus', ['surface_planetary_port_outpost_industrial', 'surface']],
  ['opis', ['surface_planetary_port_outpost_industrial', 'surface']],
  ['ponos', ['surface_planetary_port_outpost_industrial', 'surface']],
  ['tethys', ['surface_planetary_port_outpost_industrial', 'surface']],
  ['bia', ['surface_planetary_port_outpost_industrial', 'surface']],
  ['mefitis', ['surface_planetary_port_outpost_industrial', 'surface']],
  ['necessitas', ['surface_planetary_port_outpost_scientific', 'surface']],
  ['ananke', ['surface_planetary_port_outpost_scientific', 'surface']],
  ['fauna', ['surface_planetary_port_outpost_scientific', 'surface']],
  ['providentia', ['surface_planetary_port_outpost_scientific', 'surface']],
  ['antevorta', ['surface_planetary_port_outpost_scientific', 'surface']],
  ['porrima', ['surface_planetary_port_outpost_scientific', 'surface']],
  ['zeus', ['planetary_port', 'surface']],
  ['hera', ['planetary_port', 'surface']],
  ['poseidon', ['planetary_port', 'surface']],
  ['aphrodite', ['planetary_port', 'surface']],
  ['consus', ['surface_settlement_agriculture_t1_s', 'surface']],
  ['picumnus', ['surface_settlement_agriculture_t1_m', 'surface']],
  ['annona', ['surface_settlement_agriculture_t1_m', 'surface']],
  ['ceres', ['surface_settlement_agriculture_t2_l', 'surface']],
  ['fornax', ['surface_settlement_agriculture_t2_l', 'surface']],
  ['ourea', ['surface_settlement_extraction_t1_s', 'surface']],
  ['mantus', ['surface_settlement_extraction_t1_m', 'surface']],
  ['orcus', ['surface_settlement_extraction_t1_m', 'surface']],
  ['erebus', ['surface_settlement_extraction_t2_l', 'surface']],
  ['aerecura', ['surface_settlement_extraction_t2_l', 'surface']],
  ['fontus', ['surface_settlement_industrial_t1_s', 'surface']],
  ['meteope', ['surface_settlement_industrial_t1_m', 'surface']],
  ['palici', ['surface_settlement_industrial_t1_m', 'surface']],
  ['minthe', ['surface_settlement_industrial_t1_m', 'surface']],
  ['gaea', ['surface_settlement_industrial_t2_l', 'surface']],
  ['ioke', ['surface_settlement_military_t1_s', 'surface']],
  ['bellona', ['surface_settlement_military_t1_m', 'surface']],
  ['enyo', ['surface_settlement_military_t1_m', 'surface']],
  ['polemos', ['surface_settlement_military_t1_m', 'surface']],
  ['minerva', ['surface_settlement_military_t2_l', 'surface']],
  ['pheobe', ['surface_settlement_research_bio_t1_s', 'surface']],
  ['asteria', ['surface_settlement_research_bio_t1_m', 'surface']],
  ['caerus', ['surface_settlement_research_bio_t1_m', 'surface']],
  ['chronos', ['surface_settlement_research_bio_t2_l', 'surface']],
  ['aergia', ['surface_settlement_tourism_t1_s', 'surface']],
  ['comos', ['surface_settlement_tourism_t1_m', 'surface']],
  ['gelos', ['surface_settlement_tourism_t1_m', 'surface']],
  ['fufluns', ['surface_settlement_tourism_t2_l', 'surface']],
  ['tartarus', ['extraction_facility', 'surface']],
  ['aegle', ['surface_hub_civilian', 'surface']],
  ['tellus_a', ['surface_hub_exploration', 'surface']],
  ['io', ['surface_hub_outpost', 'surface']],
  ['athena', ['surface_hub_scientific', 'surface']],
  ['caelus', ['surface_hub_scientific', 'surface']],
  ['alala', ['military_installation', 'surface']],
  ['ares', ['military_installation', 'surface']],
  ['silenus', ['refinery', 'surface']],
  ['janus', ['hightech_research', 'surface']],
  ['molae', ['industrial_facility', 'surface']],
  ['tellus_b', ['industrial_facility', 'surface']],
  ['eunostus', ['industrial_facility', 'surface']],
]);

const STATUSES = new Map<string, RavenImportResult['sites'][number]['status']>([
  ['plan', 'planned'],
  ['build', 'building'],
  ['complete', 'complete'],
  ['demolish', 'demolished'],
]);

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object') return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function emptyResult(): RavenImportResult {
  return {
    ok: false,
    errors: [],
    system: null,
    sites: [],
    slots: [],
    warnings: [],
  };
}

function bodyNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
    ? value
    : null;
}

function slotCount(value: unknown): number | null {
  return typeof value === 'number' &&
    Number.isInteger(value) &&
    value >= 0 &&
    value <= 64
    ? value
    : null;
}

function parseLayout(input: unknown): RavenImportResult {
  const result = emptyResult();
  const error = (code: string, detail: string) =>
    result.errors.push({ code, detail });
  const warn = (code: string, detail: string) =>
    result.warnings.push({ code, detail });

  if (!isPlainObject(input)) {
    error('invalid_input', 'The layout must be a plain object.');
    return result;
  }

  // Inspect only consumed fields: extensions to the file format are ignored.
  const checkStrings = (
    record: Record<string, unknown>,
    fields: string[],
    path: string,
  ) => {
    for (const field of fields) {
      if (typeof record[field] === 'string' && record[field].length > 256) {
        error('invalid_string', `${path}.${field} exceeds 256 characters.`);
      }
    }
  };
  checkStrings(input, ['v', 'id64', 'name'], 'layout');
  if (input.v !== '6' && input.v !== 6) {
    error('unsupported_version', 'Only Raven layout version 6 is supported.');
  }

  // String inputs preserve all digits; numeric inputs retain the value provided
  // by the caller (rounding during JSON decoding cannot be recovered here).
  const id64 =
    typeof input.id64 === 'string'
      ? input.id64
      : typeof input.id64 === 'number' && Number.isInteger(input.id64)
        ? String(input.id64)
        : '';
  if (!/^\d{1,19}$/.test(id64)) {
    error(
      'invalid_id64',
      'id64 must be 1–19 digits (use a string for large id64 values).',
    );
  }

  const bodies = Array.isArray(input.bodies) ? input.bodies : [];
  const sites = Array.isArray(input.sites) ? input.sites : [];
  if (bodies.length > 1000)
    error('too_many_bodies', 'A layout may contain at most 1000 bodies.');
  if (sites.length > 300)
    error('too_many_sites', 'A layout may contain at most 300 sites.');
  if (result.errors.length) return result;

  for (const [index, body] of bodies.entries()) {
    if (isPlainObject(body))
      checkStrings(body, ['name', 'num'], `bodies[${index}]`);
  }
  for (const [index, site] of sites.entries()) {
    if (isPlainObject(site)) {
      checkStrings(
        site,
        ['id', 'name', 'bodyNum', 'buildType', 'status'],
        `sites[${index}]`,
      );
    }
  }
  const slots = isPlainObject(input.slots) ? Object.entries(input.slots) : [];
  for (const [key, counts] of slots) {
    if (key.length > 256)
      error('invalid_string', 'A slots key exceeds 256 characters.');
    if (Array.isArray(counts)) {
      // Only the two defined counts are consumed.
      if (
        counts
          .slice(0, 2)
          .some((value) => typeof value === 'string' && value.length > 256)
      ) {
        error(
          'invalid_string',
          `slots[${key.slice(0, 64)}] contains a string exceeding 256 characters.`,
        );
      }
    } else if (typeof counts === 'string' && counts.length > 256) {
      error(
        'invalid_string',
        `slots[${key.slice(0, 64)}] exceeds 256 characters.`,
      );
    }
  }
  if (result.errors.length) return result;

  const name = (value: string, context: string): string => {
    const characters = Array.from(value.trim());
    if (characters.length > 64)
      warn('name_truncated', `${context} was truncated to 64 characters.`);
    return characters.slice(0, 64).join('');
  };
  result.system = {
    id64,
    name: typeof input.name === 'string' ? name(input.name, 'System name') : '',
  };
  if (typeof input.name !== 'string' || !result.system.name) {
    warn('invalid_name', 'The system name is missing or invalid.');
  }
  if (input.bodies !== undefined && !Array.isArray(input.bodies)) {
    warn('invalid_bodies', 'bodies must be an array; no bodies were imported.');
  }
  if (input.sites !== undefined && !Array.isArray(input.sites)) {
    warn('invalid_sites', 'sites must be an array; no sites were imported.');
  }
  if (input.slots !== undefined && !isPlainObject(input.slots)) {
    warn(
      'invalid_slots',
      'slots must be an object; no slot counts were imported.',
    );
  }

  const bodyNames = new Map<number, string>();
  for (const [index, body] of bodies.entries()) {
    if (
      !isPlainObject(body) ||
      bodyNumber(body.num) === null ||
      typeof body.name !== 'string' ||
      !body.name.trim()
    ) {
      warn(
        'invalid_body',
        `bodies[${index}] has no valid body number and name; skipped.`,
      );
      continue;
    }
    const num = body.num as number;
    if (bodyNames.has(num)) {
      warn(
        'duplicate_body',
        `bodies[${index}] repeats body number ${num}; skipped.`,
      );
      continue;
    }
    bodyNames.set(num, name(body.name, `bodies[${index}] name`));
  }

  for (const [index, site] of sites.entries()) {
    if (!isPlainObject(site)) {
      warn('invalid_site', `sites[${index}] must be an object; skipped.`);
      continue;
    }
    const label =
      typeof site.name === 'string' ? site.name.trim() : `sites[${index}]`;
    const build =
      typeof site.buildType === 'string'
        ? BUILD_TYPES.get(site.buildType)
        : undefined;
    if (!build) {
      const buildType =
        typeof site.buildType === 'string'
          ? site.buildType
          : '(missing or invalid)';
      warn(
        'unknown_build_type',
        `Site "${label}" has unknown buildType "${buildType}"; skipped.`,
      );
      continue;
    }
    const status =
      typeof site.status === 'string' ? STATUSES.get(site.status) : undefined;
    if (!status) {
      const statusLabel =
        typeof site.status === 'string' ? site.status : '(missing or invalid)';
      warn(
        'unknown_status',
        `Site "${label}" has unknown status "${statusLabel}"; skipped.`,
      );
      continue;
    }
    if (
      typeof site.id !== 'string' ||
      !site.id.trim() ||
      typeof site.name !== 'string'
    ) {
      warn(
        'invalid_site',
        `Site "${label}" has no valid id and name; skipped.`,
      );
      continue;
    }
    const num = bodyNumber(site.bodyNum);
    const bodyName = num === null ? null : (bodyNames.get(num) ?? null);
    if (bodyName === null) {
      warn(
        'unknown_body',
        `Site "${label}" refers to an unknown body number ${num ?? '(missing or invalid)'}.`,
      );
    }
    result.sites.push({
      raven_site_id: site.id,
      raven_build_type: site.buildType as string,
      name: name(site.name, `Site "${label}" name`),
      facility_id: build[0],
      slot_kind: build[1],
      status,
      raven_body_name: bodyName,
    });
  }

  for (const [key, counts] of slots) {
    const num = /^\d+$/.test(key) ? bodyNumber(Number(key)) : null;
    const bodyName = num === null ? undefined : bodyNames.get(num);
    if (bodyName === undefined) {
      warn(
        'unknown_slot_body',
        `Slot counts refer to unknown body number "${key}"; skipped.`,
      );
      continue;
    }
    const orbital = Array.isArray(counts) ? slotCount(counts[0]) : null;
    const surface = Array.isArray(counts) ? slotCount(counts[1]) : null;
    // Raven writes -1 for "unknown / not applicable": that is a normal value,
    // so it becomes null without a warning. Anything else invalid is reported.
    const known = (value: unknown, count: number | null) =>
      count !== null || value === -1;
    if (
      !Array.isArray(counts) ||
      counts.length !== 2 ||
      !known(counts[0], orbital) ||
      !known(counts[1], surface)
    ) {
      warn(
        'invalid_slots',
        `Slot counts for "${bodyName}" are incomplete or invalid; unknown counts are null.`,
      );
    }
    result.slots.push({
      raven_body_name: bodyName,
      orbital_slots: orbital,
      surface_slots: surface,
    });
  }
  result.ok = true;
  return result;
}

/** Decode already-parsed commander data without I/O or mutations. Fatal results contain no partial plan. */
export function parseRavenLayout(input: unknown): RavenImportResult {
  try {
    return parseLayout(input);
  } catch {
    // Non-JSON values such as throwing accessors must still obey the no-throw contract.
    const result = emptyResult();
    result.errors.push({
      code: 'invalid_input',
      detail: 'The layout could not be read as plain data.',
    });
    return result;
  }
}

export function normalizeRavenBodyName(name: string): string {
  return name.trim().toLowerCase();
}

/** Match names only; do not infer body identity from Raven's body numbers. */
export function matchRavenBodies(
  result: RavenImportResult,
  bodies: { body_id: string; name: string }[],
): RavenBodyMatchResult {
  const warnings: RavenBodyMatchResult['warnings'] = [];
  const refs = new Map<string, string | null>();
  for (const body of bodies) {
    const key = normalizeRavenBodyName(body.name);
    // Ambiguous names cannot safely identify a body.
    refs.set(
      key,
      refs.has(key) && refs.get(key) !== body.body_id ? null : body.body_id,
    );
  }
  const match = (bodyName: string | null, context: string): string | null => {
    if (bodyName === null) {
      warnings.push({
        code: 'unmatched_body',
        detail: `${context} has no Raven body name to match.`,
      });
      return null;
    }
    const ref = refs.get(normalizeRavenBodyName(bodyName));
    if (ref === undefined || ref === null) {
      warnings.push({
        code: ref === null ? 'ambiguous_body' : 'unmatched_body',
        detail: `${context}: body "${bodyName}" ${ref === null ? 'matches multiple bodies' : 'was not found'}.`,
      });
      return null;
    }
    return ref;
  };
  return {
    siteBodyRefs: result.sites.map((site, index) =>
      match(site.raven_body_name, `Site ${index + 1}`),
    ),
    slotBodyRefs: result.slots.map((slot, index) =>
      match(slot.raven_body_name, `Slots ${index + 1}`),
    ),
    warnings,
  };
}
