// Small checks on single values the API sends. Shared by the files of this
// folder; not part of what the folder offers the rest of the app.
import { parseId64, type Id64 } from '@ed-finder/api-client/id64';

/** A system's id, which must have come through as text so no digit is lost. */
export function losslessId64(value: unknown): Id64 {
  if (typeof value !== 'string') {
    throw new TypeError('API id64 did not pass through the lossless JSON lane');
  }
  return parseId64(value);
}

/** A whole-number score from 0 to 100. */
export const isScore = (value: unknown): value is number =>
  typeof value === 'number' &&
  Number.isInteger(value) &&
  value >= 0 &&
  value <= 100;

/** A share from 0 to 1. */
export const isUnitInterval = (value: unknown): value is number =>
  typeof value === 'number' &&
  Number.isFinite(value) &&
  value >= 0 &&
  value <= 1;
