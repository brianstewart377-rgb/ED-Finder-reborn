/**
 * Where everything goes on the System Map, in the manner of the game's own:
 * each star heads a row, its planets follow along a line, each planet's moons
 * hang below it, and a moon's own moons run to its right. Stars are stacked
 * when there is more than one. Pure arithmetic: no drawing here.
 *
 * Sizes are for reading, not to scale. A real scale would make every moon a
 * dot beside its planet and every planet a dot beside its star.
 *
 * Ported from the System Map trial (scripts/almanac-trial/src/system-map/
 * layout.mjs).
 */
import {
  ringKind,
  type MapNode,
  type MapRing,
  type MapTree,
  type PlacedStation,
} from './tree';

const KM_PER_LS = 299792.458;
const GAP = 16;
const LABEL_CHAR = 5.5;
const LABEL_H = 11;
/** The smaller words under a name, saying what kind a planet's rings or a belt are. */
const CAPTION_CHAR = 4.3;
const CAPTION_H = 8.5;
/** The small mark beside a body for what is on its ground. */
const MARK_CHAR = 4.8;
const STATION = 11;
/** How far a star's glow reaches, as a multiple of its radius. */
export const GLOW = 1.3;

export type Box = { x0: number; x1: number; y0: number; y1: number };

export type GroundMark = Readonly<{
  built: number | null;
  slots: number | null;
  x: number;
  y: number;
  w: number;
}>;

export type BodyItem = Readonly<{
  kind: 'body';
  id: string;
  x: number;
  y: number;
  r: number;
  node: MapNode;
  mark: GroundMark | null;
}>;
export type StationItem = Readonly<{
  kind: 'station';
  x: number;
  y: number;
  size: number;
  station: PlacedStation;
}>;
export type BeltItem = Readonly<{
  kind: 'belt';
  x: number;
  y: number;
  w: number;
  h: number;
  belt: MapRing;
}>;
export type LayoutItem = BodyItem | StationItem | BeltItem;

export type LayoutLine = Readonly<{
  kind: 'line' | 'pair';
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}>;

export type LayoutLabel = Readonly<{
  x: number;
  y: number;
  text: string;
  anchor: 'middle' | 'end';
  small?: boolean;
}>;

export type Layout = {
  items: LayoutItem[];
  lines: LayoutLine[];
  labels: LayoutLabel[];
  box: Box;
};

type Part = Layout;
type Pieces = Omit<Layout, 'box'>;

type Entry =
  | { kind: 'station'; station: PlacedStation }
  | { kind: 'belt'; belt: MapRing; at: number }
  | { kind: 'body'; node: MapNode; pair: string | null };

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** How big a body is drawn, in the map's own units, from its real radius. */
export function drawnRadius(node: Pick<MapNode, 'type' | 'radiusKm'>): number {
  if (node.type === 'star') {
    if (!node.radiusKm) return 40;
    return clamp(46 + 16 * Math.log10(node.radiusKm / 695700), 26, 78);
  }
  if (!node.radiusKm) return 5;
  return clamp(0.16 * node.radiusKm ** 0.45, 3.5, 30);
}

function labelWidth(text: string): number {
  return text.length * LABEL_CHAR;
}

function captionWidth(text: string | null): number {
  return text ? text.length * CAPTION_CHAR : 0;
}

function grow(box: Box, other: Box, dx = 0, dy = 0): void {
  box.x0 = Math.min(box.x0, other.x0 + dx);
  box.x1 = Math.max(box.x1, other.x1 + dx);
  box.y0 = Math.min(box.y0, other.y0 + dy);
  box.y1 = Math.max(box.y1, other.y1 + dy);
}

/** Half the width a body takes, with its rings, or with its glow if it is a star. */
function halfWidth(node: MapNode, r: number): number {
  if (node.type === 'star') return r * GLOW;
  return node.rings.length > 0 ? r * 2.15 : r;
}

/** How far above its centre a body's name sits: clear of a star's glow. */
function nameRise(node: MapNode, r: number): number {
  return (node.type === 'star' ? r * GLOW : r) + 5;
}

/**
 * Puts a body's name above it, and under the name the kind of its rings when
 * it has any. Returns nothing; the part's labels and box take them in.
 */
function nameAbove(part: Part, node: MapNode, r: number): void {
  const rise = nameRise(node, r);
  const caption = node.ringWords;
  const lift = caption ? CAPTION_H : 0;
  part.labels.push({
    x: 0,
    y: -rise - lift,
    text: node.short,
    anchor: 'middle',
  });
  if (caption)
    part.labels.push({
      x: 0,
      y: -rise,
      text: caption,
      anchor: 'middle',
      small: true,
    });
  const width = Math.max(labelWidth(node.short), captionWidth(caption)) / 2;
  grow(part.box, { x0: -width, x1: width, y0: -rise - lift - LABEL_H, y1: -r });
}

function moved(part: Pieces, dx: number, dy: number): Pieces {
  return {
    items: part.items.map((item) => ({
      ...item,
      x: item.x + dx,
      y: item.y + dy,
    })),
    lines: part.lines.map((line) => ({
      ...line,
      x1: line.x1 + dx,
      y1: line.y1 + dy,
      x2: line.x2 + dx,
      y2: line.y2 + dy,
    })),
    labels: part.labels.map((label) => ({
      ...label,
      x: label.x + dx,
      y: label.y + dy,
    })),
  };
}

function take(into: Pieces, part: Pieces): void {
  into.items.push(...part.items);
  into.lines.push(...part.lines);
  into.labels.push(...part.labels);
}

/** How far out something is from the body it hangs from, in km, to put belts among planets. */
function orbitKm(child: MapNode, parent: MapNode): number {
  if (child.axisKm != null) return child.axisKm;
  return (
    Math.max(0, (child.arrivalLs ?? 0) - (parent.arrivalLs ?? 0)) * KM_PER_LS
  );
}

/**
 * What hangs from a node, in drawing order: first what is built in orbit
 * around it, then its children going outward with its belts among them. A
 * pair is not drawn itself: its two members take its place, marked as a pair.
 */
export function entriesOf(node: MapNode): Entry[] {
  const entries: Entry[] = node.orbit.map((station) => ({
    kind: 'station' as const,
    station,
  }));
  const belts = node.belts
    .map((belt) => ({
      kind: 'belt' as const,
      belt,
      at: ((belt.innerKm ?? 0) + (belt.outerKm ?? belt.innerKm ?? 0)) / 2,
    }))
    .sort((one, other) => one.at - other.at);
  const addBody = (child: MapNode, pair: string | null) => {
    if (child.type === 'barycentre') {
      for (const member of child.children) addBody(member, pair ?? child.id);
      return;
    }
    entries.push({ kind: 'body', node: child, pair: pair ?? null });
  };
  for (const child of node.children) {
    while (belts.length > 0 && belts[0].at <= orbitKm(child, node))
      entries.push(belts.shift() as (typeof belts)[number]);
    addBody(child, null);
  }
  entries.push(...belts);
  return entries;
}

/**
 * The small mark beside a body for the ground: how many sites our data
 * holds there (`built`), and how many ground slots the site predicts
 * (`slots`). Either may be absent, as null; the mark itself is null when
 * both are. With it, where it sits from the body's centre and how wide it is.
 */
export function groundMarkOf(
  node: MapNode,
  r = drawnRadius(node),
): GroundMark | null {
  const built = node.ground.length > 0 ? node.ground.length : null;
  const slots = node.groundSlots?.count ?? null;
  if (built === null && slots === null) return null;
  const letters =
    (built === null ? 0 : 1 + String(built).length) +
    (slots === null ? 0 : 1 + String(slots).length);
  const w = letters * MARK_CHAR + (built !== null && slots !== null ? 3 : 0);
  return { built, slots, x: r * 0.72 + 2, y: Math.max(r * 0.72 + 8, 12.5), w };
}

/** A body with its rings or glow, and the room its ground mark takes. */
function bodyPart(node: MapNode, r: number): Part {
  const half = halfWidth(node, r);
  const item: BodyItem = {
    kind: 'body',
    id: node.id,
    x: 0,
    y: 0,
    r,
    node,
    mark: groundMarkOf(node, r),
  };
  const part: Part = {
    items: [item],
    lines: [],
    labels: [],
    box: { x0: -half, x1: half, y0: -r, y1: r },
  };
  if (item.mark)
    grow(part.box, {
      x0: 0,
      x1: item.mark.x + item.mark.w,
      y0: 0,
      y1: item.mark.y + 2,
    });
  return part;
}

function leaf(entry: Exclude<Entry, { kind: 'body' }>): Part {
  if (entry.kind === 'station') {
    const half = STATION / 2;
    return {
      items: [
        { kind: 'station', x: 0, y: 0, size: STATION, station: entry.station },
      ],
      lines: [],
      labels: [],
      box: { x0: -half, x1: half, y0: -half, y1: half },
    };
  }
  // A belt says what it is in two short lines above it: "rocky" over "belt".
  const kind = ringKind(entry.belt);
  const lines = kind ? [kind, 'belt'] : ['belt'];
  const half = Math.max(9, ...lines.map((text) => captionWidth(text) / 2));
  return {
    items: [{ kind: 'belt', x: 0, y: 0, w: 14, h: 46, belt: entry.belt }],
    lines: [],
    labels: lines.map((text, index) => ({
      x: 0,
      y: -27 - (lines.length - 1 - index) * CAPTION_H,
      text,
      anchor: 'middle' as const,
      small: true,
    })),
    box: { x0: -half, x1: half, y0: -27 - lines.length * CAPTION_H, y1: 23 },
  };
}

type Placed = { pair: string | null; at: number; top?: number; side?: number };

/** Joins the members of each pair with a bracket on the far side from the list. */
function brackets(placed: readonly Placed[], across: boolean): LayoutLine[] {
  const lines: LayoutLine[] = [];
  const pairs = new Map<string, Placed[]>();
  for (const one of placed) {
    if (!one.pair) continue;
    const members = pairs.get(one.pair) ?? [];
    members.push(one);
    pairs.set(one.pair, members);
  }
  for (const members of pairs.values()) {
    if (members.length < 2) continue;
    const first = members[0];
    const last = members[members.length - 1];
    if (across) {
      const y = Math.min(...members.map((one) => one.top ?? 0)) - 8;
      lines.push({ kind: 'pair', x1: first.at, y1: y, x2: last.at, y2: y });
      for (const one of members)
        lines.push({
          kind: 'pair',
          x1: one.at,
          y1: y,
          x2: one.at,
          y2: (one.top ?? 0) - 2,
        });
    } else {
      const x = Math.max(...members.map((one) => one.side ?? 0)) + 8;
      lines.push({ kind: 'pair', x1: x, y1: first.at, x2: x, y2: last.at });
      for (const one of members)
        lines.push({
          kind: 'pair',
          x1: x,
          y1: one.at,
          x2: (one.side ?? 0) + 2,
          y2: one.at,
        });
    }
  }
  return lines;
}

/** A node with what hangs from it running to the right. Its own name goes `nameAt`: above it, or to its left. */
function rowOf(node: MapNode, nameAt: 'above' | 'left'): Part {
  const r = drawnRadius(node);
  const half = halfWidth(node, r);
  const part = bodyPart(node, r);
  if (nameAt === 'above') {
    nameAbove(part, node, r);
    grow(part.box, {
      x0: 0,
      x1: 0,
      y0: 0,
      y1: r * (node.type === 'star' ? GLOW : 1),
    });
  } else {
    const caption = node.ringWords;
    part.labels.push({ x: -half - 5, y: 3.5, text: node.short, anchor: 'end' });
    if (caption)
      part.labels.push({
        x: -half - 5,
        y: 3.5 + CAPTION_H,
        text: caption,
        anchor: 'end',
        small: true,
      });
    grow(part.box, {
      x0: -half - 5 - Math.max(labelWidth(node.short), captionWidth(caption)),
      x1: -half,
      y0: -LABEL_H / 2,
      y1: LABEL_H / 2 + (caption ? CAPTION_H : 0),
    });
  }
  let x = half + GAP;
  let last: number | null = null;
  const placed: Placed[] = [];
  for (const entry of entriesOf(node)) {
    const sub = entry.kind === 'body' ? columnOf(entry.node) : leaf(entry);
    const cx = x - sub.box.x0;
    take(part, moved(sub, cx, 0));
    grow(part.box, sub.box, cx, 0);
    if (entry.kind === 'body')
      placed.push({ pair: entry.pair, at: cx, top: sub.box.y0 });
    x = cx + sub.box.x1 + GAP;
    last = cx;
  }
  if (last !== null)
    part.lines.unshift({ kind: 'line', x1: r, y1: 0, x2: last, y2: 0 });
  const pairLines = brackets(placed, true);
  part.lines.push(...pairLines);
  for (const line of pairLines)
    grow(part.box, {
      x0: line.x1,
      x1: line.x2,
      y0: Math.min(line.y1, line.y2),
      y1: Math.max(line.y1, line.y2),
    });
  return part;
}

/** A node with what hangs from it running downward. Its own name goes above it. */
function columnOf(node: MapNode): Part {
  const r = drawnRadius(node);
  const part = bodyPart(node, r);
  nameAbove(part, node, r);
  let y = r + GAP * 0.75;
  let last: number | null = null;
  const placed: Placed[] = [];
  for (const entry of entriesOf(node)) {
    const sub = entry.kind === 'body' ? rowOf(entry.node, 'left') : leaf(entry);
    const cy = y - sub.box.y0;
    take(part, moved(sub, 0, cy));
    grow(part.box, sub.box, 0, cy);
    if (entry.kind === 'body')
      placed.push({ pair: entry.pair, at: cy, side: sub.box.x1 });
    y = cy + sub.box.y1 + GAP * 0.6;
    last = cy;
  }
  if (last !== null)
    part.lines.unshift({ kind: 'line', x1: 0, y1: r, x2: 0, y2: last });
  const pairLines = brackets(placed, false);
  part.lines.push(...pairLines);
  for (const line of pairLines)
    grow(part.box, {
      x0: Math.min(line.x1, line.x2),
      x1: Math.max(line.x1, line.x2),
      y0: line.y1,
      y1: line.y2,
    });
  return part;
}

/**
 * The whole system laid out: every body, belt and station with its place,
 * the lines between them, the names, and the box it all fits in. Stars are
 * stacked, their centres in one column, joined on the left when there are
 * two or more.
 */
export function layoutOf(tree: MapTree): Layout {
  const out: Pieces = { items: [], lines: [], labels: [] };
  let box: Box | null = null;
  let y = 0;
  const heads: { y: number; r: number }[] = [];
  for (const star of tree.heads) {
    const row = rowOf(star, 'above');
    const cy = y - row.box.y0;
    take(out, moved(row, 0, cy));
    const placedBox = {
      x0: row.box.x0,
      x1: row.box.x1,
      y0: row.box.y0 + cy,
      y1: row.box.y1 + cy,
    };
    if (box === null) box = placedBox;
    else grow(box, placedBox);
    heads.push({ y: cy, r: drawnRadius(star) });
    y = cy + row.box.y1 + GAP * 2.5;
  }
  const whole: Box = box ?? { x0: 0, x1: 0, y0: 0, y1: 0 };
  if (heads.length > 1) {
    const x = whole.x0 - 14;
    out.lines.push({
      kind: 'pair',
      x1: x,
      y1: heads[0].y,
      x2: x,
      y2: heads[heads.length - 1].y,
    });
    for (const head of heads)
      out.lines.push({
        kind: 'pair',
        x1: x,
        y1: head.y,
        x2: -head.r * GLOW,
        y2: head.y,
      });
    whole.x0 = x - 4;
  }
  return { ...out, box: whole };
}
