import { describe, expect, it } from 'vitest';
import { referenceRatingExplanation } from '$lib/api/v3-system-fixtures';
import {
  adjustmentSummary,
  byPotential,
  contributionRole,
  evidenceLabel,
  hotspotLabel,
  hotspotSummary,
  isFleetCarrier,
  percent,
  ratingEconomyLabel,
  ruleLabel,
  specialisationLabel,
  titleFromCode,
} from './system-detail-format';

const agriculture = () => {
  const explanation = referenceRatingExplanation();
  if (explanation.kind !== 'explained') throw new Error('fixture is explained');
  const economy = explanation.economies.find(
    (row) => row.economy === 'Agriculture',
  );
  if (!economy) throw new Error('fixture has Agriculture');
  return economy;
};

describe('Inspect display wording', () => {
  it('words governed catalogue codes without inventing values', () => {
    expect(titleFromCode('metal_rich')).toBe('Metal Rich');
    expect(titleFromCode('high_tech')).toBe('High Tech');
    expect(titleFromCode('coriolis_starport')).toBe('Coriolis Starport');
    expect(titleFromCode('')).toBeNull();
    expect(titleFromCode(null)).toBeNull();
  });

  it('names every governed hotspot and falls back for an unmapped code', () => {
    expect(hotspotLabel('lowtemperaturediamond')).toBe(
      'Low Temperature Diamond',
    );
    expect(hotspotLabel('opal')).toBe('Void Opal');
    expect(hotspotLabel('painite')).toBe('Painite');
    expect(hotspotLabel('new_mineral')).toBe('New Mineral');
    expect(
      hotspotSummary([
        { signalType: 'platinum', count: 2 },
        { signalType: 'painite', count: 1 },
      ]),
    ).toBe('2 Platinum · 1 Painite');
    expect(hotspotSummary([])).toBeNull();
  });

  it('recognises fleet carriers by their governed station-type code', () => {
    expect(isFleetCarrier('fleet_carrier')).toBe(true);
    expect(isFleetCarrier('drake_class_carrier')).toBe(true);
    expect(isFleetCarrier('coriolis_starport')).toBe(false);
    expect(isFleetCarrier('megaship')).toBe(false);
    expect(isFleetCarrier('space_construction_depot')).toBe(false);
    expect(isFleetCarrier('carriers_rest')).toBe(false);
    expect(isFleetCarrier(null)).toBe(false);
  });

  it('formats ratings and orders them by potential, ties in scorer order', () => {
    expect(ratingEconomyLabel('HighTech')).toBe('High Tech');
    expect(ratingEconomyLabel('Extraction')).toBe('Extraction');
    expect(percent(0.72)).toBe('72%');
    expect(percent(0.005)).toBe('1%');
    const rating = (economy: string, potential: number) => ({
      economy,
      potential,
      specialisation: null,
      specialisationMin: 0,
      specialisationMax: 0,
      completeness: 1,
      confidence: 1,
    });
    expect(
      byPotential([
        rating('Agriculture', 10),
        rating('Refinery', 50),
        rating('Military', 50),
        rating('Industrial', 0),
      ]).map((row) => row.economy),
    ).toEqual(['Refinery', 'Military', 'Agriculture', 'Industrial']);
  });

  it('shows a resolved specialisation or its unresolved range', () => {
    const resolved = agriculture();
    expect(specialisationLabel(resolved)).toBe('60');
    expect(
      specialisationLabel({
        ...resolved,
        specialisation: null,
        specialisationMin: 0,
        specialisationMax: 40,
      }),
    ).toBe('unresolved, 0–40');
  });

  it('words scorer rules, roles and evidence', () => {
    expect(ruleLabel('elw-agriculture')).toBe('Earth-like world');
    expect(ruleLabel('water-world-tourism')).toBe('Water world');
    expect(ruleLabel('tidal-lock-agriculture')).toBe('Tidally locked');
    expect(ruleLabel('reserves-pristine')).toBe('Pristine reserves');
    expect(ruleLabel('volcanism-extraction')).toBe('Volcanism');
    expect(ruleLabel('future-rule-industrial')).toBe('Future rule');
    expect(evidenceLabel('reserve_level')).toBe('reserve level');
    expect(evidenceLabel('body_inventory')).toBe('complete body list');
    expect(evidenceLabel('surface_gravity')).toBe('surface gravity');

    const [planet, moon] = agriculture().contributions;
    if (!planet || !moon) throw new Error('fixture has two contributions');
    expect(contributionRole(planet)).toBe('Native (water world)');
    expect(contributionRole(moon)).toBe('Modifier');
    expect(contributionRole({ ...planet, modifier: true })).toBe(
      'Native and modifier (water world)',
    );
  });

  it('reports the scorer’s capped totals rather than summing rules itself', () => {
    const [planet, moon] = agriculture().contributions;
    if (!planet || !moon) throw new Error('fixture has two contributions');
    expect(adjustmentSummary(planet)).toBe(
      '+20 Biological signals, Water world',
    );
    expect(adjustmentSummary(moon)).toBe(
      '+10 Biological signals; −10 Icy body',
    );
    // Three links still report the scorer's capped +25.
    expect(
      adjustmentSummary({
        ...planet,
        positive: 25,
        positiveRules: [
          'elw-agriculture',
          'terraformable-agriculture',
          'biologicals-agriculture',
        ],
      }),
    ).toBe('+25 Earth-like world, Terraformable, Biological signals');
    expect(
      adjustmentSummary({
        ...planet,
        positive: 0,
        positiveRules: [],
      }),
    ).toBeNull();
  });
});
