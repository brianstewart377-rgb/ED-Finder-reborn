import { NullEngine } from '@babylonjs/core/Engines/nullEngine.js';

/** Test-only engine factory; keep out of product imports and barrel exports. */
export function createHeadlessEngine(): NullEngine {
  return new NullEngine();
}
