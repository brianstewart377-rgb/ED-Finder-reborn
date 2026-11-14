/**
 * Which part of the System Map is on screen: a box in the map's own units,
 * always of the same shape as the screen area it fills, so one map unit is
 * the same size across and down. Pure arithmetic.
 */
import type { Box } from './layout';

export type View = Readonly<{ x: number; y: number; w: number; h: number }>;
export type Size = Readonly<{ width: number; height: number }>;

/** How much of a long system's width fits the screen when it opens, in the map's units. */
export const READABLE_WIDTH = 760;
/** The most a small system is enlarged when it opens: screen pixels for each map unit. */
export const LARGEST_SCALE = 1.9;
/** The margin kept around what is drawn, in map units. */
export const MARGIN = 28;
/** The closest the map zooms in: this many map units across. */
export const NARROWEST = 60;

/** The whole drawing with its margin. */
export function wholeOf(box: Box, margin = MARGIN): View {
  return {
    x: box.x0 - margin,
    y: box.y0 - margin,
    w: box.x1 - box.x0 + margin * 2,
    h: box.y1 - box.y0 + margin * 2,
  };
}

/** Screen pixels for each map unit. */
export function scaleOf(view: View, size: Size): number {
  return size.width / view.w;
}

/**
 * The view a system opens on, as the game's own map does it: bodies at a
 * size that can be read. A small system is shown whole, in the middle, and
 * never blown up past that size. A long one starts at its left end and runs
 * off to the right; dragging reaches the rest.
 */
export function openingView(whole: View, size: Size): View {
  const fits = Math.min(size.width / whole.w, size.height / whole.h);
  const scale = Math.min(
    LARGEST_SCALE,
    Math.max(fits, size.width / READABLE_WIDTH),
  );
  const w = size.width / scale;
  const h = size.height / scale;
  return {
    x: whole.w <= w ? whole.x - (w - whole.w) / 2 : whole.x,
    y: whole.h <= h ? whole.y - (h - whole.h) / 2 : whole.y,
    w,
    h,
  };
}

/** The whole system, as large as fits, in the middle. */
export function wholeView(whole: View, size: Size): View {
  const scale = Math.min(size.width / whole.w, size.height / whole.h);
  const w = size.width / scale;
  const h = size.height / scale;
  return {
    x: whole.x - (w - whole.w) / 2,
    y: whole.y - (h - whole.h) / 2,
    w,
    h,
  };
}

/** The same middle and scale on a screen area of a new size. */
export function resizedView(view: View, before: Size, after: Size): View {
  const scale = scaleOf(view, before);
  const w = after.width / scale;
  const h = after.height / scale;
  return { x: view.x + (view.w - w) / 2, y: view.y + (view.h - h) / 2, w, h };
}

/**
 * Zoomed by `factor` (above 1 shows more of the map) about a point given as
 * a share of the screen across and down, so that point stays where it is.
 * Never closer than NARROWEST units across, nor further out than twice the
 * whole system.
 */
export function zoomedView(
  view: View,
  factor: number,
  whole: View,
  size: Size,
  at = { x: 0.5, y: 0.5 },
): View {
  const widest = Math.max(whole.w, (whole.h * size.width) / size.height) * 2;
  const w = Math.min(widest, Math.max(NARROWEST, view.w * factor));
  const ratio = w / view.w;
  return {
    x: view.x + view.w * at.x * (1 - ratio),
    y: view.y + view.h * at.y * (1 - ratio),
    w,
    h: view.h * ratio,
  };
}

/** Moved by a distance in screen pixels. */
export function pannedView(
  view: View,
  dx: number,
  dy: number,
  size: Size,
): View {
  const perPixel = view.w / size.width;
  return { ...view, x: view.x - dx * perPixel, y: view.y - dy * perPixel };
}

/** Whether a point of the map is on screen, at least `inset` units inside the edge. */
export function shows(view: View, x: number, y: number, inset = 0): boolean {
  return (
    x >= view.x + inset &&
    x <= view.x + view.w - inset &&
    y >= view.y + inset &&
    y <= view.y + view.h - inset
  );
}

/** Whether a circle of the map is at least partly on screen. */
export function touches(view: View, x: number, y: number, r: number): boolean {
  return (
    x + r >= view.x &&
    x - r <= view.x + view.w &&
    y + r >= view.y &&
    y - r <= view.y + view.h
  );
}

/** The view moved, not zoomed, so a point is in its middle. */
export function centredOn(view: View, x: number, y: number): View {
  return { ...view, x: x - view.w / 2, y: y - view.h / 2 };
}
