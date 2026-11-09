import { get } from 'svelte/store';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/client';
import { parseId64 } from '$lib/domain/id64';
import {
  PROFILE_SYNC_KEYS,
  PROFILE_SYNC_SECURITY_DENYLIST,
  applyProfileBlob,
  createProfileSyncCommandService,
  gatherProfileBlob,
  type ProfileSyncTransport,
} from './profile-sync';
import { PERSISTENCE_KEYS } from './storage';
import {
  colonyProjects,
  compare,
  expansionPlans,
  fcRoute,
  legacyColonyPassthrough,
  myWork,
  pins,
  profileSyncLast,
} from './stores';

const VALID_KEY = 'profile-sync-key-1234567890';
const EXPORTED_AT = '2026-09-05T10:00:00.000Z';

const emptyTransport = (): ProfileSyncTransport => ({
  pull: async () => {
    throw new Error('Unexpected pull');
  },
  push: async () => {
    throw new Error('Unexpected push');
  },
});

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  for (const store of [
    pins,
    compare,
    fcRoute,
    myWork,
    colonyProjects,
    expansionPlans,
    legacyColonyPassthrough,
    profileSyncLast,
  ])
    store.clear();
});

describe('profile sync persistence helpers', () => {
  it('gathers only the closed compatibility allowlist and explicit metadata', () => {
    localStorage.setItem(
      PERSISTENCE_KEYS.pins,
      JSON.stringify([{ id64: 42, name: 'Pin', pinned_at: EXPORTED_AT }]),
    );
    localStorage.setItem(
      PERSISTENCE_KEYS.compare,
      JSON.stringify([{ id64: 43, name: 'Compare' }]),
    );
    localStorage.setItem(
      PERSISTENCE_KEYS.legacyColony,
      JSON.stringify([{ arbitrary: { future: true }, id64: 44 }]),
    );
    localStorage.setItem(
      PERSISTENCE_KEYS.fcRoute,
      JSON.stringify({ waypoints: [], config: { jump_range_ly: 400 } }),
    );
    localStorage.setItem(
      PERSISTENCE_KEYS.myWork,
      JSON.stringify({ state: { systems: {} }, version: 1 }),
    );
    localStorage.setItem(
      PERSISTENCE_KEYS.colonyProjects,
      JSON.stringify({ state: { projects: {} }, version: 3 }),
    );
    localStorage.setItem(
      PERSISTENCE_KEYS.expansionPlans,
      JSON.stringify({ state: { plans: {} }, version: 1 }),
    );

    // Even maliciously copied credential/session values are outside the
    // closed profile allowlist.
    localStorage.setItem(
      PERSISTENCE_KEYS.syncKey,
      JSON.stringify({ state: { syncKey: 'watch-list-key-1234' }, version: 0 }),
    );
    localStorage.setItem(PERSISTENCE_KEYS.adminToken, 'must-not-sync');
    localStorage.setItem(PERSISTENCE_KEYS.profileSyncKey, VALID_KEY);
    localStorage.setItem(PERSISTENCE_KEYS.profileSyncLast, EXPORTED_AT);
    localStorage.setItem(PERSISTENCE_KEYS.density, 'compact');

    const blob = gatherProfileBlob(undefined, EXPORTED_AT);

    expect(Object.keys(blob).sort()).toEqual(
      ['version', 'exported_at', ...PROFILE_SYNC_KEYS].sort(),
    );
    expect(blob.ed_pinned).toEqual([
      { id64: '42', name: 'Pin', pinned_at: EXPORTED_AT },
    ]);
    expect(blob.ed_colony_v2).toEqual([
      { arbitrary: { future: true }, id64: '44' },
    ]);
    expect(PROFILE_SYNC_SECURITY_DENYLIST).toEqual([
      PERSISTENCE_KEYS.adminToken,
      PERSISTENCE_KEYS.syncKey,
    ]);
    for (const denied of PROFILE_SYNC_SECURITY_DENYLIST)
      expect(blob).not.toHaveProperty(denied);
  });

  it('omits absent and corrupt values instead of exporting in-memory defaults', () => {
    localStorage.setItem(PERSISTENCE_KEYS.compare, '{broken');
    expect(gatherProfileBlob(undefined, EXPORTED_AT)).toEqual({
      version: 1,
      exported_at: EXPORTED_AT,
    });
  });

  it('applies a key-level merge without clearing missing or null datasets', () => {
    pins.set([
      {
        id64: parseId64('42'),
        name: 'Local pin',
        pinned_at: EXPORTED_AT,
      },
    ]);

    const receipt = applyProfileBlob({
      version: 1,
      exported_at: EXPORTED_AT,
      ed_pinned: null,
      ed_compare_v2: [{ id64: '43', name: 'Pulled compare' }],
    });

    expect(receipt).toEqual({
      applied: [PERSISTENCE_KEYS.compare],
      rejected: [],
    });
    expect(get(pins).value[0]?.name).toBe('Local pin');
    expect(get(compare).value).toEqual([
      { id64: '43', name: 'Pulled compare' },
    ]);
  });

  it('round-trips the unowned legacy colony payload as opaque JSON', () => {
    const first = [
      {
        arbitrary: ['kept', { nested: true }],
        id64: '18446744073709551615',
      },
    ];
    expect(legacyColonyPassthrough.setFromJsonValue(first)).toBe(true);
    expect(gatherProfileBlob(undefined, EXPORTED_AT).ed_colony_v2).toEqual(
      first,
    );

    const second = { future_shape: { remains: 'opaque' } };
    expect(
      applyProfileBlob({
        version: 1,
        exported_at: EXPORTED_AT,
        ed_colony_v2: second,
      }),
    ).toEqual({
      applied: [PERSISTENCE_KEYS.legacyColony],
      rejected: [],
    });
    expect(legacyColonyPassthrough.readStored().state.value).toEqual(second);
  });
});

describe('profile sync command service', () => {
  it('is explicit and performs no command during construction', () => {
    const transport = {
      pull: vi.fn(emptyTransport().pull),
      push: vi.fn(emptyTransport().push),
    };
    createProfileSyncCommandService({ transport });
    expect(transport.pull).not.toHaveBeenCalled();
    expect(transport.push).not.toHaveBeenCalled();
  });

  it('fans a successful pull into every current Svelte store without synthetic events', async () => {
    const dispatch = vi.spyOn(window, 'dispatchEvent');
    const transport: ProfileSyncTransport = {
      pull: async () => ({
        blob: {
          version: 1,
          exported_at: EXPORTED_AT,
          ed_pinned: [
            { id64: '42', name: 'Pulled pin', pinned_at: EXPORTED_AT },
          ],
          ed_compare_v2: [{ id64: '43', name: 'Pulled compare' }],
          ed_colony_v2: [{ id64: '44', arbitrary: true }],
          ed_fc_v2: {
            waypoints: [{ id: 'wp', name: 'Achenar', id64: '45' }],
            config: { jump_range_ly: 420 },
          },
          ed_my_work_v1: {
            state: {
              systems: { remote: { id64: '46', name: 'Saved system' } },
            },
            version: 1,
          },
          ed_colony_projects_v1: {
            state: {
              projects: {
                project: {
                  id: 'project',
                  system_id64: '47',
                  build_plan_placements: [],
                },
              },
            },
            version: 3,
          },
          ed_expansion_plans_v1: {
            state: {
              plans: {
                plan: {
                  id: 'plan',
                  anchor_system_id64: '48',
                  slots: [{ slot_index: 0, system_id64: '49' }],
                },
              },
            },
            version: 1,
          },
        },
        updated_at: '2026-09-05T11:00:00+00:00',
        blob_bytes: 512,
      }),
      push: emptyTransport().push,
    };
    const service = createProfileSyncCommandService({ transport });

    await expect(service.pull(VALID_KEY)).resolves.toMatchObject({
      kind: 'pulled',
      apply: { applied: PROFILE_SYNC_KEYS, rejected: [] },
    });

    expect(get(pins).value[0]?.id64).toBe('42');
    expect(get(compare).value[0]?.id64).toBe('43');
    expect(legacyColonyPassthrough.readStored().state.value).toEqual([
      { id64: '44', arbitrary: true },
    ]);
    expect(get(fcRoute).value).toMatchObject({
      waypoints: [{ id64: '45' }],
      config: { jump_range_ly: 420, cargo_t: 25_000 },
    });
    expect(get(myWork).value.state.systems['46']?.name).toBe('Saved system');
    expect(get(colonyProjects).value.state.projects.project?.system_id64).toBe(
      '47',
    );
    expect(
      get(expansionPlans).value.state.plans.plan?.slots[0]?.system_id64,
    ).toBe('49');
    expect(dispatch).not.toHaveBeenCalled();
  });

  it('records the exact successful push receipt as a raw local value', async () => {
    pins.set([
      {
        id64: parseId64('18446744073709551615'),
        name: 'Max pin',
        pinned_at: EXPORTED_AT,
      },
    ]);
    const push = vi.fn(async () => ({
      updated_at: '2026-09-05T12:00:00+00:00',
      blob_bytes: 1024,
    }));
    const service = createProfileSyncCommandService({
      transport: { pull: emptyTransport().pull, push },
      now: () => EXPORTED_AT,
    });

    const result = await service.push(VALID_KEY);

    expect(push).toHaveBeenCalledWith(
      VALID_KEY,
      expect.objectContaining({
        version: 1,
        exported_at: EXPORTED_AT,
        ed_pinned: [expect.objectContaining({ id64: '18446744073709551615' })],
      }),
      undefined,
    );
    expect(result).toMatchObject({
      kind: 'pushed',
      receipt_recorded: true,
      updated_at: '2026-09-05T12:00:00+00:00',
    });
    expect(localStorage.getItem(PERSISTENCE_KEYS.profileSyncLast)).toBe(
      '2026-09-05T12:00:00+00:00',
    );
    expect(get(profileSyncLast).value).toBe('2026-09-05T12:00:00+00:00');
  });

  it('treats a 404 pull as an empty slot without clearing local data', async () => {
    pins.set([
      {
        id64: parseId64('42'),
        name: 'Keep me',
        pinned_at: EXPORTED_AT,
      },
    ]);
    const missing = new ApiError(
      404,
      `/api/profile/sync/${VALID_KEY}`,
      { detail: 'No profile slot for sync_key (run a Push first)' },
      'No profile slot for sync_key (run a Push first)',
    );
    const service = createProfileSyncCommandService({
      transport: {
        pull: async () => {
          throw missing;
        },
        push: emptyTransport().push,
      },
    });

    await expect(service.pull(VALID_KEY)).resolves.toEqual({
      kind: 'empty',
      apply: { applied: [], rejected: [] },
    });
    expect(get(pins).value[0]?.name).toBe('Keep me');
  });

  it('preserves a structured 413 error and does not record a push receipt', async () => {
    const body = {
      type: 'about:blank',
      title: 'Profile blob too large',
      status: 413,
      detail: 'Profile blob too large: 1048577 bytes (max 1048576).',
      instance: `/api/profile/sync/${VALID_KEY}`,
    };
    const oversized = new ApiError(
      413,
      `/api/profile/sync/${VALID_KEY}`,
      body,
      body.detail,
    );
    const service = createProfileSyncCommandService({
      transport: {
        pull: emptyTransport().pull,
        push: async () => {
          throw oversized;
        },
      },
      now: () => EXPORTED_AT,
    });

    const error = await service
      .push(VALID_KEY)
      .catch((reason: unknown) => reason);
    expect(error).toBe(oversized);
    expect(error).toMatchObject({ status: 413, body });
    expect(localStorage.getItem(PERSISTENCE_KEYS.profileSyncLast)).toBeNull();
  });
});
