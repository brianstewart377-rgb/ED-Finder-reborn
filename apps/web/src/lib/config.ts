/**
 * Central build-time application configuration.
 *
 * Every feature gate is a fail-closed read of an exact Vite build variable:
 * anything other than the literal string `'1'` (absent, empty, `'true'`,
 * `'yes'`, whitespace) leaves the feature hidden. Ordinary and production
 * builds set nothing and therefore default to hidden; fixture / Product E2E
 * builds opt in explicitly at their own bundle step
 * (docs/superpowers/specs/2026-10-10-v3-finder-f4-archetype-ui-design.md,
 * sections 3 and 7).
 */

export type FeatureEnvironment = Readonly<
  Record<string, string | boolean | undefined>
>;

/** Exact opt-in value every build feature gate requires. */
export const FEATURE_GATE_ENABLED_VALUE = '1';

/** Pure gate reader so tests can prove the default-off contract explicitly. */
export function featureGateEnabled(
  env: FeatureEnvironment,
  name: string,
): boolean {
  return env[name] === FEATURE_GATE_ENABLED_VALUE;
}

export const FINDER_F4_ENV_NAME = 'VITE_FINDER_F4_ENABLED';

/** Pure Finder F4 gate: `true` only for the exact `'1'` opt-in. */
export function isFinderF4Enabled(env: FeatureEnvironment): boolean {
  return featureGateEnabled(env, FINDER_F4_ENV_NAME);
}

/**
 * The Finder F4 archetype UI gate for this build. It wraps the complete F4
 * behaviour boundary — URL hydration, request selection, URL/history effects,
 * selected-point behaviour and rendering — not only the DOM.
 */
export const FINDER_F4_ENABLED: boolean = isFinderF4Enabled(
  import.meta.env as FeatureEnvironment,
);
