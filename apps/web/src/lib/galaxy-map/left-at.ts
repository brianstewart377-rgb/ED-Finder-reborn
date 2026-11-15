/**
 * Where the galaxy map was when Explore was last left, kept for the visit
 * to the site.
 *
 * Explore's own state is made anew each time the page is opened. Without
 * this, the map would open on the whole galaxy and fly in again every time
 * someone came back from Inspect to the system they were looking at.
 */
import type { MapResume } from './GalaxyMap.svelte';

let leftAt: MapResume | null = null;

/** Remember where the map was as the page is left. */
export function rememberMapLeftAt(resume: MapResume): void {
  leftAt = resume;
}

/**
 * The view to open on, if the map is to show the system it showed when it
 * was left (or, as then, none). For any other system it opens on the whole
 * galaxy and flies there, as it does the first time.
 */
export function mapLeftAtFor(shownId: string | null): MapResume | null {
  return leftAt && leftAt.shownId === shownId ? leftAt : null;
}

/** Forget it: for the tests, which each start as a new visit. */
export function forgetMapLeftAt(): void {
  leftAt = null;
}
