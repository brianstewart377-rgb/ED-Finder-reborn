import type { SystemCameraState } from './contracts';
import type { SystemBodyLayout } from './system-scene';

/**
 * Always-visible body names for the System Map.
 *
 * The map used to show a name only while the pointer rested on a glyph. This
 * module projects each body to the canvas the same way the Babylon system
 * camera does and chooses a readable, non-overlapping subset to name. It is
 * pure: the canvas draws the result as an inert DOM overlay (Svelte owns the
 * DOM, Babylon owns the GPU), and every name stays available through the hover
 * card, the legend and the Inspect Bodies list.
 *
 * The camera maths mirror `applySystemCamera` in `babylon/adapter.ts`; a unit
 * test pins this projection to Babylon's own `Vector3.Project`, so a change to
 * either side fails loudly rather than drifting labels off their bodies.
 */

/** Babylon's default `FreeCamera.fov`: the system camera never overrides it. */
export const SYSTEM_CAMERA_FOV_RAD = 0.8;

export type SystemLabelViewport = Readonly<{ width: number; height: number }>;

/** `other` is a body with no known parent: it has no honest place in the tree. */
export type SystemLabelKind = 'star' | 'planet' | 'moon' | 'other';

export type SystemBodyLabel = Readonly<{
  bodyId: number;
  text: string;
  kind: SystemLabelKind;
  selected: boolean;
  /** Left edge of the text for `side: 'right'`, right edge for `'left'`. */
  x: number;
  /** Vertical centre of the text. */
  y: number;
  side: 'right' | 'left';
}>;

export type SystemLabelOptions = Readonly<{
  /** Most labels drawn at once; the selected body always counts first. */
  maxLabels?: number;
  /** Estimated text advance in CSS pixels, used only to avoid collisions. */
  charWidth?: number;
  labelHeight?: number;
  /** Space kept between a glyph's edge and its name. */
  gap?: number;
  /** A moon is only named once its own glyph is at least this many pixels. */
  moonMinRadiusPx?: number;
}>;

/**
 * The radius the renderer actually draws for a body, in scene units. A body
 * with no known position is a diamond with a minimum radius, a barycentre is a
 * small cross, and a ringed body carries a torus out to its outer edge; each
 * is larger than the sphere radius. This mirrors `createSystemBodyMesh` and
 * the ring torus in `babylon/adapter.ts` (and the footprint in
 * `system-scene.ts`), so a name clears what is really on screen.
 */
export function systemMarkerRadius(layout: SystemBodyLayout): number {
  const { body } = layout;
  const glyph =
    layout.placement === 'unplaced'
      ? Math.max(0.55, body.displayRadius)
      : body.class?.value === 'Barycentre'
        ? 0.45
        : body.displayRadius;
  if (body.rings?.state !== 'PRESENT') return glyph;
  // Torus: diameter 3.2 x radius, tube thickness max(0.06, 0.18 x radius).
  const ringOuter =
    body.displayRadius * 1.6 + Math.max(0.06, body.displayRadius * 0.18) / 2;
  return Math.max(glyph, ringOuter);
}

/** Glyphs smaller than this (in pixels) never keep a name away. */
const OBSTACLE_MIN_RADIUS_PX = 4;

const DEFAULTS = {
  maxLabels: 22,
  charWidth: 6.4,
  labelHeight: 16,
  gap: 6,
  moonMinRadiusPx: 4,
} as const;

type Vec = readonly [number, number, number];

const dot = (a: Vec, b: Vec): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec, b: Vec): Vec => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const normalise = (a: Vec): Vec => {
  const length = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / length, a[1] / length, a[2] / length];
};

export type SystemLabelProjector = (
  x: number,
  y: number,
  z: number,
) => Readonly<{ x: number; y: number; depth: number }> | null;

/**
 * A projector for the camera `applySystemCamera` places for `camera`, looking
 * at `target`. Babylon is left-handed, so `right = up × forward`.
 */
export function systemCameraProjector(
  camera: Pick<
    SystemCameraState,
    'semanticDistance' | 'bearingRad' | 'pitchRad'
  >,
  target: Readonly<{ x: number; y: number; z: number }>,
  viewport: SystemLabelViewport,
): SystemLabelProjector | null {
  if (
    !Number.isFinite(viewport.width) ||
    !Number.isFinite(viewport.height) ||
    viewport.width < 2 ||
    viewport.height < 2
  ) {
    return null;
  }
  const distance = Number.isFinite(camera.semanticDistance)
    ? Math.max(6, Math.min(Number.MAX_SAFE_INTEGER, camera.semanticDistance))
    : 38;
  const pitch = Math.max(0.08, Math.min(1.42, camera.pitchRad));
  const horizontal = Math.cos(pitch) * distance;
  const eye: Vec = [
    target.x + Math.sin(camera.bearingRad) * horizontal,
    target.y + Math.sin(pitch) * distance,
    target.z - Math.cos(camera.bearingRad) * horizontal,
  ];
  const forward = normalise([
    target.x - eye[0],
    target.y - eye[1],
    target.z - eye[2],
  ]);
  const right = normalise(cross([0, 1, 0], forward));
  const up = cross(forward, right);
  const tanHalf = Math.tan(SYSTEM_CAMERA_FOV_RAD / 2);
  const aspect = viewport.width / viewport.height;
  return (x, y, z) => {
    const offset: Vec = [x - eye[0], y - eye[1], z - eye[2]];
    const depth = dot(offset, forward);
    if (!(depth > 0)) return null;
    const ndcX = dot(offset, right) / (depth * tanHalf * aspect);
    const ndcY = dot(offset, up) / (depth * tanHalf);
    return {
      x: (ndcX + 1) * 0.5 * viewport.width,
      y: (1 - ndcY) * 0.5 * viewport.height,
      depth,
    };
  };
}

/** A glyph's radius on screen, in pixels, at the given depth. */
export function systemGlyphRadiusPx(
  displayRadius: number,
  depth: number,
  viewport: SystemLabelViewport,
): number {
  const tanHalf = Math.tan(SYSTEM_CAMERA_FOV_RAD / 2);
  return (displayRadius / (depth * tanHalf)) * (viewport.height / 2);
}

/**
 * Names are shortened by the whole words every body shares: the planets of
 * `Hyades Sector AB-W b2-3` read `A 1`, `A 2`, … rather than repeating the
 * system name on each. The root stars keep their full names so the map still
 * says which system it is, and a name is never reduced to nothing.
 */
export function systemLabelTexts(
  layouts: readonly SystemBodyLayout[],
): ReadonlyMap<number, string> {
  const words = (name: string) => name.trim().split(/\s+/);
  const named = layouts.filter((layout) => layout.body.name.trim().length > 0);
  let shared: string[] | null = null;
  for (const { body } of named) {
    const parts = words(body.name);
    if (shared === null) {
      shared = parts;
      continue;
    }
    let keep = 0;
    while (
      keep < shared.length &&
      keep < parts.length &&
      shared[keep] === parts[keep]
    ) {
      keep += 1;
    }
    shared.length = keep;
  }
  const prefix = named.length > 1 ? (shared ?? []) : [];
  const texts = new Map<number, string>();
  for (const layout of named) {
    const parts = words(layout.body.name);
    const isRoot = layout.group === 'root';
    const stripped =
      isRoot || prefix.length === 0 || parts.length <= prefix.length
        ? parts
        : parts.slice(prefix.length);
    texts.set(layout.body.ref.bodyId, stripped.join(' '));
  }
  return texts;
}

/** A star, or a root: the bodies other bodies orbit directly. */
function isStarLike(layout: SystemBodyLayout): boolean {
  return layout.group === 'root' || layout.body.class?.value === 'Star';
}

function labelKind(
  layout: SystemBodyLayout,
  byId: ReadonlyMap<number, SystemBodyLayout>,
): SystemLabelKind {
  if (layout.group === 'unattached') return 'other';
  if (isStarLike(layout)) return 'star';
  const parent =
    layout.parentBodyId === null ? undefined : byId.get(layout.parentBodyId);
  // Tree depth is not enough: in a barycentre -> star -> planet system the
  // stars are not roots, so a planet's parent can itself be a star. A moon is
  // a body orbiting something that is neither a star nor a barycentre.
  if (
    !parent ||
    isStarLike(parent) ||
    parent.body.class?.value === 'Barycentre'
  )
    return 'planet';
  return 'moon';
}

const TIER: Readonly<Record<SystemLabelKind, number>> = {
  star: 1,
  planet: 2,
  moon: 3,
  other: 4,
};

type Rect = Readonly<{
  left: number;
  top: number;
  right: number;
  bottom: number;
}>;

const overlaps = (a: Rect, b: Rect): boolean =>
  a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;

type Glyph = Readonly<{ bodyId: number; x: number; y: number; radius: number }>;

/** Does a label rectangle land on a body's drawn glyph? */
const coversGlyph = (rect: Rect, glyph: Glyph): boolean => {
  const dx = glyph.x - Math.max(rect.left, Math.min(glyph.x, rect.right));
  const dy = glyph.y - Math.max(rect.top, Math.min(glyph.y, rect.bottom));
  return dx * dx + dy * dy < glyph.radius * glyph.radius;
};

/**
 * Choose which bodies to name and where. The selected body always keeps its
 * name; then stars, then planets, then moons (only once they are big enough to
 * be worth reading), nearest first within a tier. A name that would land on one
 * already placed, or run off the canvas, is skipped, so the result never
 * overlaps and is bounded by `maxLabels`. A name also keeps off every other
 * body's glyph (a planet beside a large star is named on its far side), except
 * the selected body's, which is always named.
 */
export function layoutSystemBodyLabels(
  layouts: readonly SystemBodyLayout[],
  camera: SystemCameraState,
  viewport: SystemLabelViewport,
  options: SystemLabelOptions = {},
): readonly SystemBodyLabel[] {
  const { maxLabels, charWidth, labelHeight, gap, moonMinRadiusPx } = {
    ...DEFAULTS,
    ...options,
  };
  if (layouts.length === 0 || maxLabels < 1) return [];
  const byId = new Map(
    layouts.map((layout) => [layout.body.ref.bodyId, layout]),
  );
  const focusId = camera.focus.kind === 'body' ? camera.focus.ref.bodyId : null;
  const focused = focusId === null ? undefined : byId.get(focusId);
  const target = focused?.position ?? { x: 0, y: 0, z: 0 };
  const project = systemCameraProjector(camera, target, viewport);
  if (!project) return [];
  const texts = systemLabelTexts(layouts);

  type Candidate = {
    layout: SystemBodyLayout;
    kind: SystemLabelKind;
    selected: boolean;
    text: string;
    x: number;
    y: number;
    depth: number;
    radiusPx: number;
  };
  const candidates: Candidate[] = [];
  const glyphs: Glyph[] = [];
  for (const layout of layouts) {
    const { body } = layout;
    const point = project(
      layout.position.x,
      layout.position.y,
      layout.position.z,
    );
    if (!point) continue;
    const radiusPx = systemGlyphRadiusPx(
      systemMarkerRadius(layout),
      point.depth,
      viewport,
    );
    // A body big enough to read as a shape is an obstacle for names, named or
    // not. Pin-prick moons are not: a name may cross one, and treating them as
    // walls would leave the planets they orbit unnamed.
    if (radiusPx >= OBSTACLE_MIN_RADIUS_PX) {
      glyphs.push({
        bodyId: body.ref.bodyId,
        x: point.x,
        y: point.y,
        radius: radiusPx,
      });
    }
    // Barycentres are construction points, not bodies a commander can visit.
    if (body.class?.value === 'Barycentre') continue;
    const text = texts.get(body.ref.bodyId);
    if (!text) continue;
    const selected = body.ref.bodyId === focusId;
    const kind = labelKind(layout, byId);
    if (!selected && (kind === 'moon' || kind === 'other')) {
      if (radiusPx < moonMinRadiusPx) continue;
    }
    candidates.push({ layout, kind, selected, text, ...point, radiusPx });
  }
  candidates.sort(
    (a, b) =>
      Number(b.selected) - Number(a.selected) ||
      TIER[a.kind] - TIER[b.kind] ||
      a.depth - b.depth ||
      a.layout.body.ref.bodyId - b.layout.body.ref.bodyId,
  );

  const placed: Rect[] = [];
  const labels: SystemBodyLabel[] = [];
  const edge = 6;
  for (const candidate of candidates) {
    if (labels.length >= maxLabels) break;
    const width = candidate.text.length * charWidth + 8;
    // A glyph larger than the canvas would push its name off-screen: keep the
    // name beside the visible part of it instead.
    const reach = Math.min(candidate.radiusPx, viewport.width * 0.25) + gap;
    const y = candidate.y;
    const top = y - labelHeight / 2;
    const bottom = y + labelHeight / 2;
    let chosen: SystemBodyLabel | null = null;
    // Prefer a clear side; the selected body falls back to its right even over
    // another glyph, because losing its name would be worse than a crowded one.
    const attempts = candidate.selected
      ? (['right', 'left', 'right'] as const)
      : (['right', 'left'] as const);
    for (const [attempt, side] of attempts.entries()) {
      const lastResort = candidate.selected && attempt === attempts.length - 1;
      const x = side === 'right' ? candidate.x + reach : candidate.x - reach;
      const left = side === 'right' ? x : x - width;
      const right = side === 'right' ? x + width : x;
      const rect: Rect = { left, top, right, bottom };
      const onCanvas =
        left >= edge &&
        right <= viewport.width - edge &&
        top >= edge &&
        bottom <= viewport.height - edge;
      if (!onCanvas && !candidate.selected) continue;
      if (placed.some((other) => overlaps(rect, other))) continue;
      if (
        !lastResort &&
        glyphs.some(
          (glyph) =>
            glyph.bodyId !== candidate.layout.body.ref.bodyId &&
            coversGlyph(rect, glyph),
        )
      ) {
        continue;
      }
      placed.push(rect);
      chosen = {
        bodyId: candidate.layout.body.ref.bodyId,
        text: candidate.text,
        kind: candidate.kind,
        selected: candidate.selected,
        x: Math.round(x),
        y: Math.round(y),
        side,
      };
      break;
    }
    if (chosen) labels.push(chosen);
  }
  return labels;
}
