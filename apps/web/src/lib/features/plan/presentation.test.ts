import { describe, expect, it } from 'vitest';
import { previewReasonLabel } from './presentation';

describe('facility preview reason labels', () => {
  it.each([
    [
      'INSUFFICIENT_T2',
      'There are not enough Tier 2 construction points for this step.',
    ],
    [
      'INSUFFICIENT_T3',
      'There are not enough Tier 3 construction points for this step.',
    ],
    [
      'PREREQUISITE_MISSING',
      'A required facility must be completed earlier in the build order.',
    ],
    ['NOT_PLACED', 'Choose a body or belt for this build.'],
    ['SLOTS_UNKNOWN', 'Enter the available slot count for this location.'],
    ['NO_FREE_SLOT', 'No slot of the required kind is free at this location.'],
    ['NOT_LANDABLE', 'This surface facility needs a landable body.'],
    ['LANDABLE_UNKNOWN', 'It is not yet known whether this body is landable.'],
    [
      'NOT_RINGED',
      'This facility needs a location with rings or an asteroid belt.',
    ],
    ['RINGED_UNKNOWN', 'It is not yet known whether this location has rings.'],
  ])('explains %s using the existing plan wording', (reason, label) => {
    expect(previewReasonLabel(reason)).toBe(label);
  });

  it.each(['FUTURE_REASON', 'A supplied explanation.', 'toString', ''])(
    'preserves unknown reason %j verbatim',
    (reason) => {
      expect(previewReasonLabel(reason)).toBe(reason);
    },
  );
});
