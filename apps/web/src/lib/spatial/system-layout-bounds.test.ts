import { describe, expect, it } from 'vitest';

import { systemDetailFromV3, type BodyV1 } from '$lib/api/client';
import { v3Body, v3Ring, v3System } from '$lib/api/v3-system-fixtures';

import {
  buildSystemScene,
  systemBodyLayout,
  type SystemBodyLayout,
} from './system-scene';
import { solLikeSystemDetail } from './system-scene-fixtures';

const bodyKey = (id: number): string => String(10_000 + id);

function body(id: number, parentId: number, axisKm: number): BodyV1 {
  return v3Body({
    body_id: bodyKey(id),
    frontier_body_id: String(id),
    direct_parent_body_id: bodyKey(parentId),
    name: `Body ${id}`,
    body_type: 'planet',
    is_main_star: false,
    spectral_class: null,
    luminosity_class: null,
    radius_km: 6_400,
    semi_major_axis_km: axisKm,
    distance_from_arrival_ls: axisKm / 299_792.458,
  });
}

const star = () =>
  v3Body({ body_id: bodyKey(0), frontier_body_id: '0', name: 'Sol-like' });

function denseSystem() {
  return buildSystemScene(
    systemDetailFromV3(
      v3System(),
      [
        star(),
        ...Array.from({ length: 9 }, (_, index) =>
          body(index + 1, 0, 50_000_000 * 2 ** index),
        ),
        body(31, 3, 384_400),
        body(32, 3, 600_000),
        body(311, 31, 12_000),
      ],
      [v3Ring({ body_id: bodyKey(3) })],
      [],
    ),
    1,
  );
}

const separation = (left: SystemBodyLayout, right: SystemBodyLayout) =>
  Math.hypot(
    left.position.x - right.position.x,
    left.position.y - right.position.y,
    left.position.z - right.position.z,
  );

// The current torus has diameter 3.2r and thickness 0.18r (minimum 0.06).
// Assert against the visible geometry, including its outer edge.
function glyphRadius(layout: SystemBodyLayout): number {
  const radius = layout.body.displayRadius;
  return layout.body.rings?.state === 'PRESENT'
    ? radius * 1.6 + Math.max(0.06, radius * 0.18) / 2
    : radius;
}

describe('hierarchical system layout region bounds', () => {
  it('frames a 41-body Sol-like hierarchy within a semantic range of 60', () => {
    const detail = solLikeSystemDetail();
    const scene = buildSystemScene(detail, 1);
    const layouts = systemBodyLayout(scene.bodies);
    expect(detail.bodies).toHaveLength(41);
    expect(scene.bodies).toHaveLength(41);
    expect(layouts.every((layout) => layout.placement === 'placed')).toBe(true);
    expect(scene.camera.semanticDistance).toBeGreaterThanOrEqual(38);
    expect(scene.camera.semanticDistance).toBeLessThanOrEqual(60);
  });

  it('preserves legacy planet legibility and a visible minimum for smaller moons', () => {
    const scene = buildSystemScene(solLikeSystemDetail(), 1);
    const byId = new Map(scene.bodies.map((body) => [body.ref.bodyId, body]));
    for (let planetId = 1; planetId <= 8; planetId++) {
      const planet = byId.get(planetId)!;
      expect(
        planet.displayRadius / scene.camera.semanticDistance,
        `${planet.name} is smaller on screen than the legacy overview`,
      ).toBeGreaterThanOrEqual(0.68 / 38 - 1e-9);
    }
    const star = byId.get(0)!;
    expect(
      star.displayRadius / scene.camera.semanticDistance,
    ).toBeGreaterThanOrEqual(2.2 / 38 - 1e-9);
    const moons = scene.bodies.filter((body) => body.ref.bodyId >= 100);
    expect(moons).toHaveLength(32);
    for (const moon of moons) {
      const parent = byId.get(moon.parent!.bodyId)!;
      // At range 60 this policy is approximately a three-pixel diameter on
      // the 900px reference viewport; picking has a separate larger target.
      expect(
        moon.displayRadius / scene.camera.semanticDistance,
      ).toBeGreaterThanOrEqual(0.075 / 60 - 1e-9);
      expect(moon.displayRadius).toBeLessThan(parent.displayRadius);
    }
  });

  it('spreads each group of at least three placed siblings beyond every half-plane', () => {
    const scene = buildSystemScene(solLikeSystemDetail(), 1);
    const layouts = systemBodyLayout(scene.bodies);
    const byId = new Map(
      layouts.map((layout) => [layout.body.ref.bodyId, layout]),
    );
    const childrenByParent = new Map<number, SystemBodyLayout[]>();
    for (const layout of layouts) {
      if (layout.parentBodyId === null || layout.placement !== 'placed')
        continue;
      const children = childrenByParent.get(layout.parentBodyId) ?? [];
      children.push(layout);
      childrenByParent.set(layout.parentBodyId, children);
    }
    let checkedGroups = 0;
    for (const [parentId, children] of childrenByParent) {
      if (children.length < 3) continue;
      checkedGroups++;
      const parent = byId.get(parentId)!;
      const angles = children
        .map((child) =>
          Math.atan2(
            child.position.z - parent.position.z,
            child.position.x - parent.position.x,
          ),
        )
        .sort((a, b) => a - b);
      const angularGaps = angles.map((angle, index) =>
        index + 1 < angles.length
          ? angles[index + 1]! - angle
          : angles[0]! + Math.PI * 2 - angle,
      );
      // A gap of pi or more means every child fits in the remaining half-plane.
      expect(
        Math.max(...angularGaps),
        `${parent.body.name}'s children occupy only one side`,
      ).toBeLessThan(Math.PI);
    }
    expect(checkedGroups).toBe(5);
  });

  it('keeps Sol-like moon regions within their parent bands and outside parent glyphs', () => {
    const scene = buildSystemScene(solLikeSystemDetail(), 1);
    const layouts = systemBodyLayout(scene.bodies);
    const byId = new Map(
      layouts.map((layout) => [layout.body.ref.bodyId, layout]),
    );
    for (const layout of layouts) {
      expect(layout.regionRadius, layout.body.name).toBeGreaterThanOrEqual(
        glyphRadius(layout),
      );
      if (layout.parentBodyId === null) continue;
      const parent = byId.get(layout.parentBodyId)!;
      const distance = separation(layout, parent);
      expect(
        distance + layout.regionRadius,
        `${layout.body.name} exceeds ${parent.body.name}'s region`,
      ).toBeLessThanOrEqual(parent.regionRadius + 1e-9);
      expect(
        distance - layout.regionRadius,
        `${layout.body.name}'s region intersects ${parent.body.name}'s glyph`,
      ).toBeGreaterThan(glyphRadius(parent));
      const siblings = layouts.filter(
        (sibling) =>
          sibling.parentBodyId === layout.parentBodyId &&
          sibling.body.ref.bodyId !== layout.body.ref.bodyId,
      );
      for (const sibling of siblings) {
        expect(
          Math.abs(distance - separation(sibling, parent)),
          `${layout.body.name} and ${sibling.body.name} share a radial band`,
        ).toBeGreaterThan(layout.regionRadius + sibling.regionRadius);
      }
    }
  });

  it('separates rendered bodies and rings in a dense planetary system with nested moons', () => {
    const layouts = systemBodyLayout(denseSystem().bodies);
    for (let left = 0; left < layouts.length; left++) {
      for (let right = left + 1; right < layouts.length; right++) {
        const first = layouts[left]!;
        const second = layouts[right]!;
        expect(
          separation(first, second),
          `${first.body.name} overlaps ${second.body.name}`,
        ).toBeGreaterThan(glyphRadius(first) + glyphRadius(second));
      }
    }
  });

  it('contains every visible body and descendant region within its allocated parent region', () => {
    const layouts = systemBodyLayout(denseSystem().bodies);
    const byId = new Map(
      layouts.map((layout) => [layout.body.ref.bodyId, layout]),
    );
    for (const layout of layouts) {
      expect(layout.regionRadius, layout.body.name).toBeGreaterThanOrEqual(
        glyphRadius(layout),
      );
      if (layout.parentBodyId === null) continue;
      const parent = byId.get(layout.parentBodyId)!;
      expect(
        separation(layout, parent) + layout.regionRadius,
        `${layout.body.name} exceeds ${parent.body.name}'s region`,
      ).toBeLessThanOrEqual(parent.regionRadius + 1e-9);
    }
  });

  it('keeps all moon descendants away from every sibling planet radial band', () => {
    const layouts = systemBodyLayout(denseSystem().bodies);
    const byId = new Map(
      layouts.map((layout) => [layout.body.ref.bodyId, layout]),
    );
    const origin = byId.get(0)!;
    const siblings = layouts.filter(
      (layout) => layout.parentBodyId === 0 && layout.body.ref.bodyId !== 3,
    );
    for (const moonId of [31, 32, 311]) {
      const moon = byId.get(moonId)!;
      const moonDistance = separation(moon, origin);
      for (const sibling of siblings) {
        const siblingDistance = separation(sibling, origin);
        expect(
          Math.abs(moonDistance - siblingDistance),
          `${moon.body.name} crosses ${sibling.body.name}'s radial band`,
        ).toBeGreaterThan(moon.regionRadius + sibling.regionRadius);
      }
    }
  });

  it('keeps finite, deterministic positions when a large forest is shuffled', () => {
    const bodies = [star()];
    for (let planet = 1; planet <= 256; planet++) {
      bodies.push(body(planet, 0, 100_000 * 1.05 ** planet));
      for (let moon = 1; moon <= 3; moon++)
        bodies.push(body(1000 + planet * 3 + moon, planet, moon * 200_000));
    }
    const scene = buildSystemScene(
      systemDetailFromV3(v3System(), bodies, [], []),
      1,
    );
    const expected = systemBodyLayout(scene.bodies);
    const shuffled = [
      ...scene.bodies.filter((_, index) => index % 2 === 1).reverse(),
      ...scene.bodies.filter((_, index) => index % 2 === 0).reverse(),
    ];
    const sorted = (layouts: readonly SystemBodyLayout[]) =>
      [...layouts].sort(
        (left, right) => left.body.ref.bodyId - right.body.ref.bodyId,
      );
    expect(sorted(systemBodyLayout(shuffled))).toEqual(sorted(expected));
    for (const layout of expected) {
      expect(Number.isFinite(layout.position.x)).toBe(true);
      expect(Number.isFinite(layout.position.z)).toBe(true);
      expect(layout.regionRadius).toBeGreaterThanOrEqual(glyphRadius(layout));
    }
  });
});
