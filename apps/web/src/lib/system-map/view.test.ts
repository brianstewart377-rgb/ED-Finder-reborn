import { describe, expect, it } from 'vitest';
import { layoutOf } from './layout';
import { treeOf } from './recorded-systems';
import {
  LARGEST_SCALE,
  NARROWEST,
  READABLE_WIDTH,
  centredOn,
  openingView,
  pannedView,
  resizedView,
  scaleOf,
  shows,
  wholeOf,
  wholeView,
  zoomedView,
} from './view';

const SCREEN = { width: 1000, height: 560 };
const sol = wholeOf(layoutOf(treeOf('sol')).box);
const polaris = wholeOf(layoutOf(treeOf('polaris')).box);
const altair = wholeOf(layoutOf(treeOf('altair')).box);

describe('which part of the map is on screen', () => {
  it('opens a long system at its left end, at a size that can be read', () => {
    // Sol's row of planets is far wider than the screen.
    expect(sol.w).toBeGreaterThan(READABLE_WIDTH * 1.5);
    const view = openingView(sol, SCREEN);
    expect(view.x).toBe(sol.x);
    expect(scaleOf(view, SCREEN)).toBeCloseTo(SCREEN.width / READABLE_WIDTH);
  });

  it('opens a small system whole and in the middle, never blown up past the largest size', () => {
    const view = openingView(altair, SCREEN);
    expect(scaleOf(view, SCREEN)).toBeLessThanOrEqual(LARGEST_SCALE);
    expect(view.x + view.w / 2).toBeCloseTo(altair.x + altair.w / 2);
    expect(view.y + view.h / 2).toBeCloseTo(altair.y + altair.h / 2);
    expect(view.w).toBeGreaterThanOrEqual(altair.w);
    expect(view.h).toBeGreaterThanOrEqual(altair.h);
  });

  it('opens a tall system, two stars stacked, at its top and in the middle across', () => {
    const view = openingView(polaris, SCREEN);
    expect(polaris.h).toBeGreaterThan(view.h);
    expect(view.y).toBe(polaris.y);
    expect(view.x + view.w / 2).toBeCloseTo(polaris.x + polaris.w / 2);
  });

  it('keeps the screen’s own shape, so the map is never stretched', () => {
    for (const view of [
      openingView(sol, SCREEN),
      wholeView(sol, SCREEN),
      openingView(polaris, SCREEN),
    ])
      expect(view.w / view.h).toBeCloseTo(SCREEN.width / SCREEN.height);
  });

  it('shows the whole system when asked', () => {
    const view = wholeView(sol, SCREEN);
    expect(view.x).toBeLessThanOrEqual(sol.x);
    expect(view.x + view.w).toBeGreaterThanOrEqual(sol.x + sol.w - 1e-9);
    expect(view.y).toBeLessThanOrEqual(sol.y);
    expect(view.y + view.h).toBeGreaterThanOrEqual(sol.y + sol.h - 1e-9);
  });

  it('zooms about the pointer, so the point under it stays where it is', () => {
    const view = openingView(sol, SCREEN);
    const at = { x: 0.3, y: 0.6 };
    const before = { x: view.x + view.w * at.x, y: view.y + view.h * at.y };
    const zoomed = zoomedView(view, 1 / 1.18, sol, SCREEN, at);
    expect(zoomed.w).toBeLessThan(view.w);
    expect(zoomed.x + zoomed.w * at.x).toBeCloseTo(before.x);
    expect(zoomed.y + zoomed.h * at.y).toBeCloseTo(before.y);
  });

  it('zooms no closer than a few bodies across, and no further out than twice the system', () => {
    let view = openingView(polaris, SCREEN);
    for (let step = 0; step < 60; step += 1)
      view = zoomedView(view, 1 / 1.4, polaris, SCREEN);
    expect(view.w).toBeCloseTo(NARROWEST);
    for (let step = 0; step < 60; step += 1)
      view = zoomedView(view, 1.4, polaris, SCREEN);
    expect(view.w).toBeCloseTo(
      Math.max(polaris.w, (polaris.h * SCREEN.width) / SCREEN.height) * 2,
    );
  });

  it('moves with a drag, the map following the pointer', () => {
    const view = openingView(sol, SCREEN);
    const moved = pannedView(view, 100, -40, SCREEN);
    const perPixel = view.w / SCREEN.width;
    expect(moved.x).toBeCloseTo(view.x - 100 * perPixel);
    expect(moved.y).toBeCloseTo(view.y + 40 * perPixel);
    expect(moved.w).toBe(view.w);
  });

  it('keeps its middle and its size when the screen area changes size', () => {
    const view = openingView(sol, SCREEN);
    const bigger = { width: 1400, height: 700 };
    const after = resizedView(view, SCREEN, bigger);
    expect(scaleOf(after, bigger)).toBeCloseTo(scaleOf(view, SCREEN));
    expect(after.x + after.w / 2).toBeCloseTo(view.x + view.w / 2);
    expect(after.y + after.h / 2).toBeCloseTo(view.y + view.h / 2);
  });

  it('brings a body reached off screen into the middle', () => {
    const view = openingView(sol, SCREEN);
    const far = { x: sol.x + sol.w - 40, y: sol.y + sol.h / 2 };
    expect(shows(view, far.x, far.y)).toBe(false);
    const moved = centredOn(view, far.x, far.y);
    expect(shows(moved, far.x, far.y)).toBe(true);
    expect(moved.w).toBe(view.w);
  });
});
