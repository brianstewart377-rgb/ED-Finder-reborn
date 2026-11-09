import { describe, expect, it } from 'vitest';
import { parseId64 } from '$lib/domain/id64';

import { createCommanderHistoryContribution } from './commander-history';

describe('commander history contribution', () => {
  it('keeps journal visits in a private owner layer separate from catalogue density', () => {
    const contribution = createCommanderHistoryContribution(
      'markers',
      [
        {
          kind: 'marker',
          systemId64: parseId64('9007199254740993'),
          systemName: 'Remembered Reach',
          x: 10,
          y: -3,
          z: 45,
          visitCount: 4,
          firstVisitedAt: '2025-01-01T00:00:00Z',
          lastVisitedAt: '2026-09-14T00:00:00Z',
        },
      ],
      12,
      false,
    );

    expect(contribution).toMatchObject({
      owner: 'COMMANDER_HISTORY',
      layers: [
        {
          id: 'commander-history-heatmap',
          representation: 'DERIVED',
          targetCount: 1,
          payload: {
            source: 'journal-log',
            mode: 'markers',
            points: [
              {
                systemId64: '9007199254740993',
                positionLy: { x: 10, y: -3, z: 45 },
                visitCount: 4,
              },
            ],
          },
        },
      ],
    });
    expect(
      contribution.layers.some(({ id }) => id === 'catalogue-density'),
    ).toBe(false);
  });
});
