import { describe, expect, it } from 'vitest';

import type { BodyVisualDescriptor, SystemCameraState } from './contracts';
import {
  layoutSystemBodyLabels,
  systemCameraProjector,
  systemGlyphRadiusPx,
  systemLabelTexts,
  systemMarkerRadius,
} from './system-labels';
import { solLikeSystemDetail } from './system-scene-fixtures';
import { buildSystemScene, systemBodyLayout } from './system-scene';

function body(
  bodyId: number,
  name: string,
  parentId: number | null,
  distance: number,
  bodyClass = 'Planet',
  displayRadius = bodyClass === 'Star' ? 2.2 : 0.68,
): BodyVisualDescriptor {
  return {
    ref: { systemId64: '42', bodyId },
    name,
    ...(parentId === null
      ? {}
      : { parent: { systemId64: '42', bodyId: parentId } }),
    class: { value: bodyClass, representation: 'AUTHORITATIVE' },
    distanceFromArrivalLs: { value: distance, representation: 'AUTHORITATIVE' },
    displayRadius,
    orbital: { placement: 'DETERMINISTIC_SCHEMATIC' },
    rings: { state: 'UNKNOWN', bands: [] },
  };
}

const camera = (patch: Partial<SystemCameraState> = {}): SystemCameraState => ({
  systemId64: '42',
  focus: { kind: 'system', systemId64: '42' },
  semanticDistance: 60,
  bearingRad: -0.85,
  pitchRad: 0.58,
  revision: 1,
  ...patch,
});

describe('System Map label projection', () => {
  it('refuses to project into a canvas with no usable size', () => {
    for (const viewport of [
      { width: 0, height: 0 },
      { width: 1, height: 1 },
      { width: Number.NaN, height: 400 },
    ]) {
      expect(
        systemCameraProjector(camera(), { x: 0, y: 0, z: 0 }, viewport),
      ).toBeNull();
    }
  });

  it('drops points behind the camera', () => {
    const project = systemCameraProjector(
      camera({ semanticDistance: 30 }),
      { x: 0, y: 0, z: 0 },
      { width: 800, height: 600 },
    )!;
    // The camera sits above and behind the origin; this is further behind it.
    const behind = project(
      Math.sin(-0.85) * 500,
      Math.sin(0.58) * 500,
      -Math.cos(-0.85) * 500,
    );
    expect(behind).toBeNull();
  });
});

describe('System Map label text', () => {
  const layoutOf = (bodies: readonly BodyVisualDescriptor[]) =>
    systemBodyLayout(bodies);

  it('strips the words every body shares, but never from a root star', () => {
    const texts = systemLabelTexts(
      layoutOf([
        body(0, 'Hyades Sector AB-W b2-3 A', null, 0, 'Star'),
        body(1, 'Hyades Sector AB-W b2-3 A 1', 0, 300),
        body(2, 'Hyades Sector AB-W b2-3 A 1 a', 1, 301, 'Planet'),
        body(3, 'Hyades Sector AB-W b2-3 A 2', 0, 900),
      ]),
    );
    expect(texts.get(0)).toBe('Hyades Sector AB-W b2-3 A');
    expect(texts.get(1)).toBe('1');
    expect(texts.get(2)).toBe('1 a');
    expect(texts.get(3)).toBe('2');
  });

  it('keeps names whole when bodies share no words, or there is only one', () => {
    const solar = systemLabelTexts(
      layoutOf([
        body(0, 'Sun', null, 0, 'Star'),
        body(1, 'Earth', 0, 500),
        body(2, 'Mars', 0, 800),
      ]),
    );
    expect([...solar.values()]).toEqual(['Sun', 'Earth', 'Mars']);
    const alone = systemLabelTexts(
      layoutOf([body(0, 'Lonely Star', null, 0, 'Star')]),
    );
    expect(alone.get(0)).toBe('Lonely Star');
  });

  it('never reduces a name to nothing, and skips blank names', () => {
    const texts = systemLabelTexts(
      layoutOf([
        body(0, 'Foo', null, 0, 'Star'),
        body(1, 'Foo', 0, 500),
        body(2, '   ', 0, 700),
      ]),
    );
    expect(texts.get(1)).toBe('Foo');
    expect(texts.has(2)).toBe(false);
  });
});

describe('System Map label layout', () => {
  const viewport = { width: 1200, height: 684 };
  const sol = () => {
    const contract = buildSystemScene(solLikeSystemDetail(), 1);
    return { contract, layouts: systemBodyLayout(contract.bodies) };
  };

  it('names the stars and planets of an overview without overlapping any', () => {
    const { contract, layouts } = sol();
    const labels = layoutSystemBodyLabels(layouts, contract.camera, viewport);
    expect(labels.length).toBeGreaterThan(5);
    expect(labels.length).toBeLessThanOrEqual(22);
    const names = labels.map((label) => label.text);
    expect(names).toContain('Sol-like');
    expect(names).toContain('Earth');
    expect(names).toContain('Jupiter');
    expect(new Set(labels.map((label) => label.bodyId)).size).toBe(
      labels.length,
    );
    // Every name sits on the canvas.
    for (const label of labels) {
      expect(label.x).toBeGreaterThanOrEqual(0);
      expect(label.x).toBeLessThanOrEqual(viewport.width);
      expect(label.y).toBeGreaterThanOrEqual(0);
      expect(label.y).toBeLessThanOrEqual(viewport.height);
    }
    // Approximate no-overlap check, with the same estimate the layout uses.
    const rect = (label: (typeof labels)[number]) => {
      const width = label.text.length * 6.4 + 8;
      const left = label.side === 'right' ? label.x : label.x - width;
      return {
        left,
        right: left + width,
        top: label.y - 8,
        bottom: label.y + 8,
      };
    };
    for (let i = 0; i < labels.length; i += 1) {
      for (let j = i + 1; j < labels.length; j += 1) {
        const a = rect(labels[i]);
        const b = rect(labels[j]);
        const apart =
          a.right <= b.left + 1 ||
          b.right <= a.left + 1 ||
          a.bottom <= b.top + 1 ||
          b.bottom <= a.top + 1;
        expect(apart, `${labels[i].text} / ${labels[j].text}`).toBe(true);
      }
    }
  });

  it('keeps every name off the glyphs of the other bodies', () => {
    const { contract, layouts } = sol();
    const byId = new Map(
      layouts.map((layout) => [layout.body.ref.bodyId, layout]),
    );
    for (const size of [
      { width: 1200, height: 684 },
      { width: 420, height: 606 },
    ]) {
      const project = systemCameraProjector(
        contract.camera,
        { x: 0, y: 0, z: 0 },
        size,
      )!;
      const glyphs = layouts
        .map((layout) => {
          const at = project(
            layout.position.x,
            layout.position.y,
            layout.position.z,
          )!;
          return {
            id: layout.body.ref.bodyId,
            x: at.x,
            y: at.y,
            radius: systemGlyphRadiusPx(
              layout.body.displayRadius,
              at.depth,
              size,
            ),
          };
        })
        // Pin-prick moons never keep a name away; only visible shapes do.
        .filter((glyph) => glyph.radius >= 4);
      const labels = layoutSystemBodyLabels(layouts, contract.camera, size);
      expect(labels.length).toBeGreaterThan(3);
      for (const label of labels) {
        const width = label.text.length * 6.4 + 8;
        const left = label.side === 'right' ? label.x : label.x - width;
        const rect = {
          left,
          right: left + width,
          top: label.y - 8,
          bottom: label.y + 8,
        };
        for (const glyph of glyphs) {
          if (glyph.id === label.bodyId) continue;
          const dx =
            glyph.x - Math.max(rect.left, Math.min(glyph.x, rect.right));
          const dy =
            glyph.y - Math.max(rect.top, Math.min(glyph.y, rect.bottom));
          // Rounding the label position to whole pixels may shave a pixel.
          expect(
            Math.hypot(dx, dy),
            `${label.text} over ${byId.get(glyph.id)!.body.name}`,
          ).toBeGreaterThanOrEqual(glyph.radius - 1);
        }
      }
    }
  });

  it("puts a planet beside a large star on the star's far side", () => {
    const { contract, layouts } = sol();
    const labels = layoutSystemBodyLabels(layouts, contract.camera, viewport);
    const mercury = labels.find((label) => label.text === 'Mercury');
    const star = labels.find((label) => label.kind === 'star');
    expect(mercury).toBeDefined();
    expect(star).toBeDefined();
    expect(mercury!.side).toBe('left');
  });

  it('is deterministic and respects the label cap', () => {
    const { contract, layouts } = sol();
    const first = layoutSystemBodyLabels(layouts, contract.camera, viewport);
    expect(layoutSystemBodyLabels(layouts, contract.camera, viewport)).toEqual(
      first,
    );
    const capped = layoutSystemBodyLabels(layouts, contract.camera, viewport, {
      maxLabels: 3,
    });
    expect(capped).toHaveLength(3);
    expect(capped.map((label) => label.bodyId)).toEqual(
      first.slice(0, 3).map((label) => label.bodyId),
    );
    expect(
      layoutSystemBodyLabels(layouts, contract.camera, viewport, {
        maxLabels: 0,
      }),
    ).toEqual([]);
  });

  it('names stars before planets, and planets before moons', () => {
    const { contract, layouts } = sol();
    const labels = layoutSystemBodyLabels(layouts, contract.camera, viewport, {
      moonMinRadiusPx: 0,
    });
    const tier = { star: 1, planet: 2, moon: 3, other: 4 } as const;
    const kinds = labels.map((label) => tier[label.kind]);
    expect(kinds).toEqual([...kinds].sort((a, b) => a - b));
    expect(labels[0].kind).toBe('star');
  });

  it('only names a moon once its own glyph is large enough to read', () => {
    const { contract, layouts } = sol();
    const moons = new Set(
      layouts
        .filter(({ parentBodyId }) => {
          const parent = layouts.find(
            ({ body }) => body.ref.bodyId === parentBodyId,
          );
          return parent !== undefined && parent.parentBodyId !== null;
        })
        .map(({ body }) => body.ref.bodyId),
    );
    expect(moons.size).toBeGreaterThan(0);
    const overview = layoutSystemBodyLabels(layouts, contract.camera, viewport);
    expect(overview.some((label) => moons.has(label.bodyId))).toBe(false);
    const everything = layoutSystemBodyLabels(
      layouts,
      contract.camera,
      viewport,
      {
        moonMinRadiusPx: 0,
        maxLabels: 200,
      },
    );
    expect(everything.some((label) => moons.has(label.bodyId))).toBe(true);
  });

  it('always names the selected body, first, even among many', () => {
    const { contract, layouts } = sol();
    const neptune = layouts.find(({ body }) => body.name === 'Neptune');
    expect(neptune).toBeDefined();
    const state = {
      ...contract.camera,
      focus: { kind: 'body' as const, ref: neptune!.body.ref },
    };
    const labels = layoutSystemBodyLabels(layouts, state, viewport, {
      maxLabels: 1,
    });
    expect(labels).toHaveLength(1);
    expect(labels[0]).toMatchObject({
      text: 'Neptune',
      selected: true,
    });
    // The selected body is the camera target, so it is drawn at the centre.
    expect(labels[0].y).toBe(Math.round(viewport.height / 2));
    expect(Math.abs(labels[0].x - viewport.width / 2)).toBeLessThan(80);
  });

  it('does not name barycentres, and names a parentless body last and only when it is big enough', () => {
    const bodies = [
      body(0, 'Binary barycentre', null, 0, 'Barycentre'),
      body(1, 'Binary A', 0, 5, 'Star'),
      body(2, 'Binary B', 0, 10, 'Star'),
      body(3, 'Lost planet', 999, 20),
    ];
    const layouts = systemBodyLayout(bodies);
    expect(layouts.find(({ body }) => body.ref.bodyId === 3)?.group).toBe(
      'unattached',
    );
    const near = layoutSystemBodyLabels(layouts, camera(), viewport);
    const names = near.map((label) => label.text);
    expect(names).not.toContain('Binary barycentre');
    expect(names.slice(0, 2).sort()).toEqual(['Binary A', 'Binary B']);
    expect(near.at(-1)).toMatchObject({ text: 'Lost planet', kind: 'other' });
    const far = layoutSystemBodyLabels(
      layouts,
      camera({ semanticDistance: 900 }),
      viewport,
    );
    expect(far.map((label) => label.text)).not.toContain('Lost planet');
  });

  it('flips a name to the left when it would run off the right edge', () => {
    const bodies = [
      body(0, 'Alpha', null, 0, 'Star'),
      body(1, 'A very long planet name indeed', 0, 400),
    ];
    const layouts = systemBodyLayout(bodies);
    const narrow = { width: 360, height: 640 };
    const labels = layoutSystemBodyLabels(
      layouts,
      camera({ semanticDistance: 38 }),
      narrow,
    );
    for (const label of labels) {
      const width = label.text.length * 6.4 + 8;
      const left = label.side === 'right' ? label.x : label.x - width;
      expect(left).toBeGreaterThanOrEqual(0);
      expect(left + width).toBeLessThanOrEqual(narrow.width);
    }
  });

  it('returns nothing for an empty scene or a canvas with no size', () => {
    const { contract, layouts } = sol();
    expect(layoutSystemBodyLabels([], contract.camera, viewport)).toEqual([]);
    expect(
      layoutSystemBodyLabels(layouts, contract.camera, { width: 0, height: 0 }),
    ).toEqual([]);
  });
});

describe('System Map label kinds and marker size', () => {
  const withoutDistance = (
    source: BodyVisualDescriptor,
  ): BodyVisualDescriptor => {
    const copy = { ...source };
    delete copy.distanceFromArrivalLs;
    return copy;
  };
  const vp = { width: 640, height: 360 };

  it('treats a planet of a star that orbits a barycentre as a planet, not a moon', () => {
    // barycentre -> star -> planet: the stars are not roots, so tree depth
    // alone would call the stars planets and their planets moons.
    const bodies = [
      body(0, 'Binary barycentre', null, 0, 'Barycentre'),
      body(1, 'Binary A', 0, 5, 'Star'),
      body(2, 'Binary B', 0, 10, 'Star'),
      body(3, 'Binary A 1', 1, 40, 'Planet', 0.2),
      body(4, 'Binary A 1 a', 3, 41, 'Planet', 0.2),
    ];
    const layouts = systemBodyLayout(bodies);
    // Far enough that a 0.2-radius glyph is well under the moon threshold.
    const labels = layoutSystemBodyLabels(
      layouts,
      camera({ semanticDistance: 60 }),
      vp,
      { maxLabels: 50 },
    );
    const kinds = new Map(labels.map((label) => [label.bodyId, label.kind]));
    expect(kinds.get(1)).toBe('star');
    expect(kinds.get(2)).toBe('star');
    // The planet keeps its name at a size where a moon would be dropped.
    expect(kinds.get(3)).toBe('planet');
    // Its own moon is still treated as a moon and dropped.
    expect(kinds.has(4)).toBe(false);
  });

  it('mirrors the marker size the renderer draws', () => {
    const bodies = [
      body(0, 'Alpha', null, 0, 'Star'),
      body(1, 'Beta', 0, 20),
      body(2, 'Binary barycentre', null, 0, 'Barycentre'),
    ];
    const layouts = systemBodyLayout(bodies);
    const lost = body(9, 'Lost moon', 1, 5, 'Planet', 0.075);
    const noDistance = withoutDistance(lost);
    const unplaced = systemBodyLayout([...bodies, noDistance]).find(
      ({ body }) => body.ref.bodyId === 9,
    )!;
    expect(unplaced.placement).toBe('unplaced');
    expect(systemMarkerRadius(unplaced)).toBe(0.55);
    const placed = layouts.find(({ body }) => body.ref.bodyId === 1)!;
    expect(systemMarkerRadius(placed)).toBe(placed.body.displayRadius);
    const bary = layouts.find(({ body }) => body.ref.bodyId === 2)!;
    expect(systemMarkerRadius(bary)).toBe(0.45);
  });

  it('keeps the name of a position-unknown body clear of its diamond', () => {
    const lost = body(9, 'Lost moon', 1, 5, 'Planet', 0.075);
    const noDistance = withoutDistance(lost);
    const layouts = systemBodyLayout([
      body(0, 'Alpha', null, 0, 'Star'),
      body(1, 'Beta', 0, 20),
      noDistance,
    ]);
    const target = layouts.find(({ body }) => body.ref.bodyId === 9)!;
    const state = camera({
      semanticDistance: 6,
      focus: { kind: 'body', ref: target.body.ref },
    });
    const labels = layoutSystemBodyLabels(layouts, state, vp);
    const label = labels.find(({ bodyId }) => bodyId === 9)!;
    const project = systemCameraProjector(state, target.position, vp)!;
    const centre = project(
      target.position.x,
      target.position.y,
      target.position.z,
    )!;
    const diamond = systemGlyphRadiusPx(
      0.55, // the renderer's minimum diamond radius, written out on purpose
      centre.depth,
      vp,
    );
    // The text starts beyond the diamond's edge on whichever side it sits,
    // not inside it.
    if (label.side === 'right') {
      expect(label.x).toBeGreaterThanOrEqual(centre.x + diamond);
    } else {
      expect(label.x).toBeLessThanOrEqual(centre.x - diamond);
    }
  });

  it('includes the ring torus in the marker size of a ringed body', () => {
    const ringed = (rings: boolean): BodyVisualDescriptor => ({
      ...body(1, 'Saturnian', 0, 20, 'Planet', 0.9),
      rings: rings
        ? { state: 'PRESENT', bands: [] }
        : { state: 'UNKNOWN', bands: [] },
    });
    const layoutOf = (rings: boolean) =>
      systemBodyLayout([body(0, 'Alpha', null, 0, 'Star'), ringed(rings)]).find(
        ({ body }) => body.ref.bodyId === 1,
      )!;
    expect(systemMarkerRadius(layoutOf(false))).toBe(0.9);
    // 1.6 x 0.9 + max(0.06, 0.162) / 2: the outer edge of the torus.
    expect(systemMarkerRadius(layoutOf(true))).toBeCloseTo(1.521, 3);
  });

  it('keeps a name clear of its own ring on whichever side it sits', () => {
    const layouts = systemBodyLayout([
      body(0, 'Alpha', null, 0, 'Star'),
      {
        ...body(1, 'Saturnian', 0, 20, 'Planet', 0.9),
        rings: { state: 'PRESENT', bands: [] },
      },
    ]);
    const target = layouts.find(({ body }) => body.ref.bodyId === 1)!;
    const state = camera({
      semanticDistance: 18,
      focus: { kind: 'body', ref: target.body.ref },
    });
    const label = layoutSystemBodyLabels(layouts, state, vp).find(
      ({ bodyId }) => bodyId === 1,
    )!;
    const project = systemCameraProjector(state, target.position, vp)!;
    const centre = project(
      target.position.x,
      target.position.y,
      target.position.z,
    )!;
    const outer = systemGlyphRadiusPx(
      1.521, // 1.6 x 0.9 + max(0.06, 0.162) / 2, written out on purpose
      centre.depth,
      vp,
    );
    if (label.side === 'right') {
      expect(label.x).toBeGreaterThanOrEqual(centre.x + outer);
    } else {
      expect(label.x).toBeLessThanOrEqual(centre.x - outer);
    }
  });
});
