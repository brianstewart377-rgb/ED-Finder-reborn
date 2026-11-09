import { describe, expect, it } from 'vitest';
import {
  architectObservationGuidance,
  architectPrimaryPortFlagLabel,
  architectSlotCountLabel,
  architectSurveyLabel,
  normalizeArchitectObservation,
} from './architectObservation';

describe('architectObservationUtils', () => {
  it('defaults Architect survey and primary-port flag to unknown planning context', () => {
    const status = normalizeArchitectObservation();

    expect(status.surveyState).toBe('not_observed');
    expect(status.primaryPortFlag.state).toBe('unknown');
    expect(architectSurveyLabel(status)).toBe('Architect survey: not observed');
    expect(architectPrimaryPortFlagLabel(status)).toBe('Primary-port flag: unknown');
    expect(architectSlotCountLabel('Orbital slots', status.orbitalSlotCount)).toBe('Orbital slots: unknown');
    expect(architectObservationGuidance(status)).toContain('Primary-port flag is unknown until observed in Architect Mode.');
  });

  it('renders observed Architect survey status without inventing slot data', () => {
    const status = normalizeArchitectObservation({
      surveyState: 'observed',
      orbitalSlotCount: 4.8,
      groundSlotCount: -1,
      primaryPortFlag: {
        state: 'observed',
        bodyName: 'A 2',
        slotLabel: 'Orbital slot 1',
      },
    });

    expect(status.surveyState).toBe('observed');
    expect(status.orbitalSlotCount).toBe(4);
    expect(status.groundSlotCount).toBeNull();
    expect(architectSurveyLabel(status)).toBe('Architect survey: observed');
    expect(architectPrimaryPortFlagLabel(status)).toBe('Primary-port flag: observed on A 2 / Orbital slot 1');
    expect(architectObservationGuidance(status)).toContain('If the flagged primary-port slot is inconvenient, consider an outpost there and place the main station elsewhere.');
  });
});
