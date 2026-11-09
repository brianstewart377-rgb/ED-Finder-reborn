import { describe, expect, it } from 'vitest';
import type { SystemDetail } from '@ed-finder/api-client/types';
import {
  classifyExistingStationLane,
  isTransientStationForColonyPlanning,
  resolveExistingInfrastructure,
} from './existingInfrastructure';

const baseSystem = {
  id64: 42,
  name: 'Occupied Test',
  bodies: [
    { id: 1, name: 'Occupied Test A', body_type: 'Star', distance_from_star: 0 },
    { id: 2, name: 'Occupied Test A 1', body_type: 'Planet', is_landable: true, distance_from_star: 120 },
    { id: 3, name: 'Occupied Test A 2', body_type: 'Planet', is_landable: true, distance_from_star: 240 },
  ],
  stations: [],
} as unknown as SystemDetail;

describe('existing infrastructure resolver', () => {
  it('maps legacy exact body-name fallbacks as inferred orbital station associations', () => {
    const resolution = resolveExistingInfrastructure({
      ...baseSystem,
      stations: [
        {
          id: 1001,
          market_id: 1001,
          name: 'Holden Orbital',
          station_type: 'Coriolis',
          body_name: 'Occupied Test A 1',
          primary_economy: 'Refinery',
        },
      ],
    } as unknown as SystemDetail);

    expect(resolution.mapped).toHaveLength(1);
    expect(resolution.mapped[0]).toEqual(expect.objectContaining({
      source: 'existing',
      body_id: '2',
      body_match_confidence: 'inferred',
      body_match_reason: 'Matched station body name; verify against backend resolver metadata.',
      lane: 'orbital',
      association_status: 'inferred',
      association_confidence: 'strong_inference',
      association_source: 'frontend_body_name_fallback',
      economy: 'Refinery',
    }));
  });

  it('uses backend association metadata as the planner source of truth', () => {
    const resolution = resolveExistingInfrastructure({
      ...baseSystem,
      stations: [
        {
          id: 1004,
          market_id: 1004,
          name: 'Linked Orbital',
          station_type: 'Coriolis',
          body_name: 'Occupied Test A 2',
          station_body_name: 'Raw Body Name',
          body_id: 3,
          lane: 'orbital',
          association_status: 'confirmed',
          association_confidence: 'exact',
          association_source: 'resolver_body_id',
          resolver_notes: 'Matched exact body id.',
        },
      ],
    } as unknown as SystemDetail);

    expect(resolution.mapped[0]).toEqual(expect.objectContaining({
      body_id: '3',
      body_name: 'Occupied Test A 2',
      association_status: 'confirmed',
      association_source: 'resolver_body_id',
      body_match_reason: 'Matched exact body id.',
    }));
  });

  it('keeps backend unresolved permanent associations out of occupied capacity and separates transients', () => {
    const resolution = resolveExistingInfrastructure({
      ...baseSystem,
      stations: [
        {
          id: 1005,
          name: 'Unresolved Megaship',
          station_type: 'MegaShip',
          body_name: 'Occupied Test A 1',
          body_id: 2,
          lane: 'unknown',
          association_status: 'confirmed',
          association_confidence: 'exact',
          association_source: 'resolver_body_name',
          resolver_notes: 'MegaShip is not treated as permanent colony-slot infrastructure.',
        },
        {
          id: 1006,
          name: 'Unresolved Station',
          station_type: 'Outpost',
          lane: 'orbital',
          association_status: 'unresolved',
          association_confidence: 'unresolved',
          association_source: 'unknown',
        },
      ],
    } as unknown as SystemDetail);

    expect(resolution.mapped).toHaveLength(0);
    expect(resolution.transient.map((item) => item.name)).toEqual(['Unresolved Megaship']);
    expect(resolution.unresolved.map((item) => item.name)).toEqual(['Unresolved Station']);
  });

  it('treats carrier callsigns without confirmed permanent links as transient non-slot rows', () => {
    const callsignStation = {
      id: 1007,
      name: 'T9J-99T',
      station_type: 'Unknown',
      association_status: 'unresolved',
      association_confidence: 'unresolved',
      association_source: 'unknown',
    };
    const resolution = resolveExistingInfrastructure({
      ...baseSystem,
      stations: [callsignStation],
    } as unknown as SystemDetail);

    expect(isTransientStationForColonyPlanning(callsignStation)).toBe(true);
    expect(resolution.mapped).toHaveLength(0);
    expect(resolution.unresolved).toHaveLength(0);
    expect(resolution.transient[0]).toEqual(expect.objectContaining({
      name: 'T9J-99T',
      transient: true,
      transient_reason: 'Fleet Carrier callsign / transient / ignored for colony planning',
    }));
  });

  it('does not classify callsign-like confirmed permanent station links as transient', () => {
    const resolution = resolveExistingInfrastructure({
      ...baseSystem,
      stations: [
        {
          id: 1008,
          name: 'ABC-123',
          station_type: 'Outpost',
          body_id: 2,
          body_name: 'Occupied Test A 1',
          lane: 'orbital',
          association_status: 'confirmed',
          association_confidence: 'exact',
          association_source: 'manual',
        },
      ],
    } as unknown as SystemDetail);

    expect(resolution.transient).toHaveLength(0);
    expect(resolution.unresolved).toHaveLength(0);
    expect(resolution.mapped[0]).toEqual(expect.objectContaining({
      name: 'ABC-123',
      body_id: '2',
      lane: 'orbital',
    }));
  });

  it('prefers exact body ids when present', () => {
    const resolution = resolveExistingInfrastructure({
      ...baseSystem,
      stations: [
        {
          id: 1002,
          name: 'Surface Depot',
          station_type: 'PlanetaryPort',
          body_name: 'Wrong Body Name',
          body_id: 3,
        },
      ],
    } as unknown as SystemDetail);

    expect(resolution.mapped[0]).toEqual(expect.objectContaining({
      body_id: '3',
      body_match_confidence: 'exact',
      lane: 'surface',
    }));
  });

  it('keeps ambiguous distance-only associations unresolved', () => {
    const resolution = resolveExistingInfrastructure({
      ...baseSystem,
      bodies: [
        ...(baseSystem.bodies ?? []),
        { id: 4, name: 'Occupied Test A 3', body_type: 'Planet', distance_from_star: 120 },
      ],
      stations: [
        {
          id: 1003,
          name: 'Ambiguous Station',
          station_type: 'Outpost',
          distance_from_star: 120,
        },
      ],
    } as unknown as SystemDetail);

    expect(resolution.mapped).toHaveLength(0);
    expect(resolution.unresolved[0]).toEqual(expect.objectContaining({
      body_match_confidence: 'unresolved',
      unresolved_reason: 'Distance-from-star match is ambiguous.',
    }));
  });

  it('does not force unknown station types into a slot lane', () => {
    expect(classifyExistingStationLane('Coriolis')).toBe('orbital');
    expect(classifyExistingStationLane('PlanetaryOutpost')).toBe('surface');
    expect(classifyExistingStationLane('MegaShip')).toBe('unknown');
    expect(classifyExistingStationLane('Unknown')).toBe('unknown');
  });
});
