// Sol, the star every distance is measured from when no other is chosen.
// Offered to the app through ./client, like everything in this folder.
import { parseId64 } from '@ed-finder/api-client/id64';
import type { Id64 } from '@ed-finder/api-client/id64';

/**
 * Sol's lossless id64 (`apps/api/src/helpers.py` `SOL_ID64`), mirrored so an
 * Any search anchored at Sol normalizes to the same `sol` distance reference
 * the selected-archetype ranking measures from.
 */
export const SOL_ID64: Id64 = parseId64('10477373803');
export const SOL_NAME = 'Sol';
