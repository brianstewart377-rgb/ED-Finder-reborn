import { describe, expect, it } from 'vitest';
import solBox from './fixtures/map-systems-sol-box.json';
import findNearSol from './fixtures/find-sol-first-60.json';
import {
  boxAround,
  chosenFromFind,
  detailsFor,
  JOURNEYS_ASKED_FOR,
  JOURNEYS_COLOUR,
  JOURNEYS_GROUP,
  mapContent,
  OPENING_DISTANCE_LY,
  RANGE_LY,
  rangeSphere,
  showingInWords,
  starClassInBox,
  starGroup,
  starGroups,
  whatToAdd,
  starsInRange,
  STARS_ASKED_FOR,
  type BoxSystem,
  type Journey,
  type MapSystem,
  type Showing,
} from './map-content';

// The live site's real answer for the 30 ly box around Sol on 13 Nov 2026
// (see the file's own note), with its fields named as the API client names
// them.
const SOL_BOX: BoxSystem[] = solBox.response.systems.map((system) => ({
  id64: String(system.id64),
  name: system.name,
  x: system.x,
  y: system.y,
  z: system.z,
  mainStarClass: system.main_star_class,
}));
const SOL: MapSystem = {
  id64: '10477373803',
  name: 'Sol',
  x: 0,
  y: 0,
  z: 0,
  star: 'G2',
};
// The first row Find gave for "within 500 ly of Sol" that day.
const WREGOE_ROW = findNearSol.results[0];
const WREGOE: MapSystem = {
  id64: String(WREGOE_ROW.id64),
  name: WREGOE_ROW.name,
  ...WREGOE_ROW.coords,
  star: WREGOE_ROW.main_star_type,
};

const solShowing = (changes: Partial<Showing> = {}): Showing => ({
  chosen: SOL,
  facts: null,
  stars: starsInRange(SOL_BOX, SOL),
  starsState: 'ready',
  starsCutShort: false,
  ...changes,
});

// Real systems of the box, with visits that are this test's own: nobody's
// journal is kept in the repository.
const ALPHA = SOL_BOX.find((system) => system.name === 'Alpha Centauri')!;
const FAR = SOL_BOX.find((system) => system.name === 'LHS 449')!;
const visit = (
  system: BoxSystem,
  visits: number,
  firstVisit: string,
  lastVisit: string,
): Journey => ({
  id64: system.id64,
  name: system.name,
  x: system.x,
  y: system.y,
  z: system.z,
  visits,
  firstVisit,
  lastVisit,
});
const JOURNEYS: Journey[] = [
  visit(ALPHA, 3, '2026-01-02T10:00:00Z', '2026-03-04T12:00:00Z'),
  // A system outside every showing: Find's first result, 400 ly from Sol.
  {
    id64: WREGOE.id64,
    name: WREGOE.name,
    x: WREGOE.x,
    y: WREGOE.y,
    z: WREGOE.z,
    visits: 1,
    firstVisit: '2026-02-01T00:00:00Z',
    lastVisit: '2026-02-01T00:00:00Z',
  },
];

describe('a star’s group and colour', () => {
  // Star names as Find gives them, read from the live site on 13 Nov 2026.
  it.each([
    ['M (Red dwarf) Star', 'M class star'],
    ['K (Yellow-Orange) Star', 'K class star'],
    ['G (White-Yellow) Star', 'G class star'],
    ['F (White) Star', 'F class star'],
    ['A (Blue-White) Star', 'A class star'],
    ['B (Blue-White) Star', 'B class star'],
    ['L (Brown dwarf) Star', 'L brown dwarf'],
    ['T (Brown dwarf) Star', 'Y / T brown dwarf'],
    ['T Tauri Star', 'T Tauri / AeBe Herbig'],
    ['M (Red giant) Star', 'M / K giant or supergiant'],
    ['Neutron Star', 'Neutron star'],
    ['Black Hole', 'Black hole'],
    ['White Dwarf (DA) Star', 'White dwarf'],
  ])('%s is drawn as %s, in a colour bright enough to see', (star, group) => {
    const found = starGroup(star);
    expect(found.name).toBe(group);
    expect(found.color).toHaveLength(3);
    for (const part of found.color) {
      expect(Number.isInteger(part)).toBe(true);
      expect(part).toBeGreaterThanOrEqual(0);
      expect(part).toBeLessThanOrEqual(255);
    }
    expect(Math.max(...found.color)).toBeGreaterThanOrEqual(100);
  });

  it('takes the colour the rest of the site draws the class in', () => {
    // $lib/spatial/stellar-presentation: an M class core is [1, 0.2, 0.06]
    // and a G class core [1, 0.98, 0.66]. A black hole's core is black, so
    // it takes its ring's colour, [0.56, 0.43, 0.34].
    expect(starGroup('M (Red dwarf) Star').color).toEqual([255, 51, 15]);
    expect(starGroup('G2').color).toEqual([255, 250, 168]);
    expect(starGroup('Black Hole').color).toEqual([143, 110, 87]);
    expect(starGroup(null).name).toBe('Unknown star class');
  });

  it('puts every star class of the real box in a group of its own class', () => {
    for (const system of SOL_BOX) {
      const group = starGroup(system.mainStarClass).name;
      if (system.mainStarClass == null)
        expect(group).toBe('Unknown star class');
      else expect(group, system.mainStarClass).not.toBe('Unknown star class');
    }
    // Different classes are different groups in different colours.
    expect(starGroup('G2').name).toBe('G class star');
    expect(starGroup('D5').name).toBe('White dwarf');
    expect(starGroup('Y0').name).toBe('Y / T brown dwarf');
    expect(starGroup('G2').color).not.toEqual(starGroup('M3').color);
  });

  it('lists the groups among some systems commonest first, each once', () => {
    const systems = ['M3', 'K1', 'M5', null].map((star) => ({ star }));
    expect(starGroups(systems).map((group) => group.name)).toEqual([
      'M class star',
      'K class star',
      'Unknown star class',
    ]);
    expect(starGroups([])).toEqual([]);
  });
});

describe('the real stars in range of a chosen system', () => {
  it('asks the site for the box around it, widened to whole light years', () => {
    expect(RANGE_LY).toBe(15);
    expect(boxAround(SOL)).toEqual({
      minX: -15,
      maxX: 15,
      minY: -15,
      maxY: 15,
      minZ: -15,
      maxZ: 15,
      limit: STARS_ASKED_FOR,
    });
    expect(boxAround(WREGOE)).toMatchObject({
      minX: 293,
      maxX: 324,
      minY: -39,
      maxY: -8,
      minZ: -269,
      maxZ: -238,
    });
  });

  it('keeps those within 15 ly, nearest first, without the chosen system', () => {
    const stars = starsInRange(SOL_BOX, SOL);
    // The box holds 84 systems. 33 sit in its corners, further than 15 ly;
    // Sol is the chosen one.
    expect(SOL_BOX).toHaveLength(84);
    expect(stars).toHaveLength(50);
    expect(
      stars.every((star) => star.distance <= 15 && star.id64 !== SOL.id64),
    ).toBe(true);
    for (let index = 1; index < stars.length; index += 1)
      expect(stars[index - 1].distance).toBeLessThanOrEqual(
        stars[index].distance,
      );
    // Our data holds a system named "Test" one light year from Sol, with no
    // star class.
    expect(stars[0]).toEqual({
      id64: '7780433924818',
      name: 'Test',
      x: -1,
      y: 0,
      z: 0,
      star: null,
      distance: 1,
    });
    expect(stars[1].name).toBe('Alpha Centauri');
    expect(stars[1].distance.toFixed(2)).toBe('4.38');
    expect(stars.at(-1)!.name).toBe('LHS 449');
    expect(starsInRange(SOL_BOX, SOL, 5)).toHaveLength(2);
  });

  it('reads the chosen system’s own star class from the same answer', () => {
    expect(starClassInBox(SOL_BOX, SOL.id64)).toBe('G2');
    expect(starClassInBox(SOL_BOX, WREGOE.id64)).toBeNull();
  });
});

describe('a system chosen in Find', () => {
  it('is taken with its name, position and star, as Find holds them', () => {
    expect(
      chosenFromFind({ ...WREGOE_ROW, id64: String(WREGOE_ROW.id64) }),
    ).toEqual(WREGOE);
    expect(
      chosenFromFind({ id64: '42', name: '  ', coords: { x: 1, y: 2, z: 3 } }),
    ).toEqual({ id64: '42', name: 'System 42', x: 1, y: 2, z: 3, star: null });
  });

  it('is not shown without a position, and never on top of Sol', () => {
    expect(chosenFromFind(null)).toBeNull();
    expect(chosenFromFind({ id64: '42', name: 'Somewhere' })).toBeNull();
    expect(
      chosenFromFind({ id64: '42', coords: { x: 1, y: null, z: 3 } }),
    ).toBeNull();
    expect(
      chosenFromFind({ id64: '42', coords: { x: 1, y: Number.NaN, z: 3 } }),
    ).toBeNull();
    // Our data stores an unknown position as 0, 0, 0: only Sol is there.
    expect(
      chosenFromFind({ id64: '42', coords: { x: 0, y: 0, z: 0 } }),
    ).toBeNull();
    expect(
      chosenFromFind({
        id64: '10477373803',
        name: 'Sol',
        coords: { x: 0, y: 0, z: 0 },
      }),
    ).toMatchObject({ name: 'Sol', x: 0, y: 0, z: 0 });
  });
});

describe('what the map is handed', () => {
  it('gets the chosen system first, then every star, each in its own star’s group', () => {
    const content = mapContent(solShowing(), null);
    expect(content.records).toHaveLength(51);
    expect(content.records[0]).toEqual({
      name: 'Sol',
      id64: '10477373803',
      coords: { x: 0, y: 0, z: 0 },
      categories: ['G class star'],
      primaryStar: 'G2',
    });
    expect(content.records[2]).toEqual({
      name: 'Alpha Centauri',
      id64: '1178708478315',
      coords: { x: 3.03125, y: -0.09375, z: 3.15625 },
      categories: ['G class star'],
      primaryStar: 'G2',
    });
    // Sol's own class first, then the others, the commonest first.
    expect(content.groups.map((group) => group.name)).toEqual([
      'G class star',
      'M class star',
      'Y / T brown dwarf',
      'K class star',
      'F class star',
      'A class star',
      'L brown dwarf',
      'Unknown star class',
      'White dwarf',
    ]);
    // Star classes differ on the map: no two groups share a colour.
    const colours = content.groups.map((group) => group.color.join());
    expect(new Set(colours).size).toBe(colours.length);
    // Every record's group is one the map was told about.
    const told = new Set(content.groups.map((group) => group.name));
    for (const made of content.records)
      expect(told.has(made.categories[0]), made.name).toBe(true);
    // No system is on the map twice.
    expect(new Set(content.records.map((made) => made.id64)).size).toBe(51);
  });

  it('draws the claim range as a faint 15 ly ball around the chosen system', () => {
    expect(mapContent(solShowing(), null).spheres).toEqual([
      {
        name: '15 ly around Sol',
        position: [0, 0, 0],
        radius: 15,
        color: [255, 170, 80],
        opacity: 0.1,
      },
    ]);
    expect(rangeSphere(WREGOE).position).toEqual([308.4375, -23.25, -253.0625]);
    expect(OPENING_DISTANCE_LY).toBeGreaterThan(2 * RANGE_LY);
  });

  it('shows the chosen system alone until the stars have been found', () => {
    const content = mapContent(
      solShowing({ stars: [], starsState: 'loading' }),
      null,
    );
    expect(content.records.map((made) => made.name)).toEqual(['Sol']);
    expect(content.spheres).toHaveLength(1);
  });

  it('is empty with nothing chosen and My journeys off', () => {
    expect(mapContent(null, null)).toEqual({
      groups: [],
      records: [],
      spheres: [],
    });
  });

  it('adds the visited systems as one quiet group, each system once', () => {
    const content = mapContent(solShowing(), JOURNEYS);
    // First of the groups: it stays while systems are chosen in Find.
    expect(content.groups[0]).toMatchObject({
      name: JOURNEYS_GROUP,
      color: JOURNEYS_COLOUR,
      markerStyle: 'disc',
    });
    expect(content.groups[1].name).toBe('G class star');
    // Alpha Centauri is a star in range and visited: one marker, in its
    // star's colour, counted in both groups.
    const alpha = content.records.filter((made) => made.id64 === ALPHA.id64);
    expect(alpha).toHaveLength(1);
    expect(alpha[0].categories).toEqual(['G class star', JOURNEYS_GROUP]);
    // The system visited far from Sol is a marker of the journeys alone.
    expect(content.records.at(-1)).toEqual({
      name: WREGOE.name,
      id64: WREGOE.id64,
      coords: { x: 308.4375, y: -23.25, z: -253.0625 },
      categories: [JOURNEYS_GROUP],
    });
    expect(content.records).toHaveLength(52);
    expect(new Set(content.records.map((made) => made.id64)).size).toBe(52);
  });

  it('shows the visited systems with nothing chosen, and no range', () => {
    const content = mapContent(null, JOURNEYS);
    expect(content.groups.map((group) => group.name)).toEqual([JOURNEYS_GROUP]);
    expect(content.records.map((made) => made.name)).toEqual([
      'Alpha Centauri',
      WREGOE.name,
    ]);
    expect(content.spheres).toEqual([]);
    // Switched on with no visits: the group is there, empty.
    expect(mapContent(null, []).groups).toHaveLength(1);
    expect(JOURNEYS_ASKED_FOR).toBe(20_000);
  });
});

describe('what to add to the map when nothing has to come off it', () => {
  const alone = solShowing({ stars: [], starsState: 'loading' });

  it('adds a system’s stars to it when they arrive: new groups at the end, new systems, the same ball', () => {
    const drawn = mapContent(alone, null);
    const next = mapContent(solShowing(), null);
    const add = whatToAdd(drawn, next)!;
    expect(add.groups.map((group) => group.name)).toEqual(
      next.groups.slice(1).map((group) => group.name),
    );
    expect(add.groups).toHaveLength(8);
    // Sol is on the map already, and unchanged.
    expect(add.records).toHaveLength(50);
    expect(add.records.map((made) => made.name)).not.toContain('Sol');
    expect(add.spheres).toBe(false);
    // Adding them gives the same groups, in the same order, as starting again.
    expect([...drawn.groups, ...add.groups]).toEqual(next.groups);
  });

  it('adds nothing when the same answer comes again', () => {
    expect(
      whatToAdd(
        mapContent(solShowing(), JOURNEYS),
        mapContent(solShowing(), [
          ...JOURNEYS.map((journey) => ({ ...journey })),
        ]),
      ),
    ).toEqual({ groups: [], records: [], spheres: false });
  });

  it('adds a system chosen while only the journeys were on the map, with its ball', () => {
    const add = whatToAdd(
      mapContent(null, JOURNEYS),
      mapContent(solShowing(), JOURNEYS),
    )!;
    expect(add.groups[0].name).toBe('G class star');
    expect(add.spheres).toBe(true);
    // Alpha Centauri was a visited system; it is now also a star in range,
    // so it is given again, in its star's group first.
    expect(
      add.records.find((made) => made.id64 === ALPHA.id64)!.categories,
    ).toEqual(['G class star', JOURNEYS_GROUP]);
    expect(add.records.map((made) => made.name)).not.toContain(WREGOE.name);
  });

  it('cannot add when something has to come off: another system chosen, or the journeys switched off', () => {
    const wregoe: Showing = { ...alone, chosen: WREGOE };
    expect(
      whatToAdd(mapContent(solShowing(), null), mapContent(wregoe, null)),
    ).toBeNull();
    expect(
      whatToAdd(
        mapContent(solShowing(), JOURNEYS),
        mapContent(solShowing(), null),
      ),
    ).toBeNull();
    expect(
      whatToAdd(mapContent(alone, null), mapContent(null, null)),
    ).toBeNull();
  });

  it('cannot add the journeys once stars are on the map: their group goes before the star classes', () => {
    expect(
      whatToAdd(
        mapContent(solShowing(), null),
        mapContent(solShowing(), JOURNEYS),
      ),
    ).toBeNull();
    // With only a system and no stars yet, the same holds.
    expect(
      whatToAdd(mapContent(alone, null), mapContent(alone, JOURNEYS)),
    ).toBeNull();
  });
});

describe('what the map’s panel says about a system', () => {
  it('says how far a star is from the chosen system', () => {
    expect(detailsFor(solShowing(), null, ALPHA.id64)).toEqual({
      kind: 'star',
      description: 'A real star within 15 ly of Sol.',
      values: [
        { label: 'FROM SOL', value: '4.4 ly' },
        { label: 'ID', value: '1178708478315', copy: '1178708478315' },
      ],
    });
    expect(detailsFor(solShowing(), null, 'no such id')).toBeNull();
    expect(detailsFor(null, null, ALPHA.id64)).toBeNull();
  });

  it('says what Find said of the chosen system, and how many stars are in range', () => {
    const showing: Showing = {
      chosen: WREGOE,
      facts: {
        potential: WREGOE_ROW.archetype_score,
        primary: WREGOE_ROW.primary_archetype,
        secondary: WREGOE_ROW.secondary_archetype,
        claim: WREGOE_ROW.claim_status,
      },
      stars: [],
      starsState: 'ready',
      starsCutShort: false,
    };
    const details = detailsFor(showing, null, WREGOE.id64)!;
    expect(details.kind).toBe('chosen');
    expect(details.values).toEqual([
      { label: 'BEST COLONY POTENTIAL', value: '100' },
      { label: 'BEST AS', value: 'flexible' },
      { label: 'ALSO SUITS', value: 'stronghold' },
      { label: 'CLAIM', value: 'free' },
      { label: 'STARS IN RANGE', value: '0' },
      { label: 'ID', value: '3068561197395', copy: '3068561197395' },
    ]);
    expect(details.description).toContain('15 ly around it');
    // The map's panel takes at most 12 values.
    expect(details.values.length).toBeLessThanOrEqual(12);
    // A system Find said nothing of has only the count and its id.
    expect(detailsFor(solShowing(), null, SOL.id64)!.values).toEqual([
      { label: 'STARS IN RANGE', value: '50' },
      { label: 'ID', value: '10477373803', copy: '10477373803' },
    ]);
  });

  it('never passes off a count it does not have', () => {
    const count = (changes: Partial<Showing>) =>
      detailsFor(solShowing(changes), null, SOL.id64)!.values[0].value;
    expect(count({ stars: [], starsState: 'loading' })).toBe('looking…');
    expect(count({ stars: [], starsState: 'failed' })).toBe('did not load');
    expect(count({ starsCutShort: true })).toBe('50 or more');
  });

  it('says when and how often the player visited a system', () => {
    expect(detailsFor(null, JOURNEYS, ALPHA.id64)).toEqual({
      kind: 'journey',
      description: 'A system you have visited, from your imported journals.',
      values: [
        { label: 'YOUR VISITS', value: '3' },
        { label: 'FIRST VISIT', value: '2 Jan 2026' },
        { label: 'LAST VISIT', value: '4 Mar 2026' },
        { label: 'ID', value: ALPHA.id64, copy: ALPHA.id64 },
      ],
    });
    // One visit: one date, not the same date twice.
    expect(
      detailsFor(null, JOURNEYS, WREGOE.id64)!.values.map(
        (value) => value.label,
      ),
    ).toEqual(['YOUR VISITS', 'FIRST VISIT', 'ID']);
    // A visited star in range says both things.
    expect(
      detailsFor(solShowing(), JOURNEYS, ALPHA.id64)!.values.map(
        (value) => value.label,
      ),
    ).toEqual(['FROM SOL', 'YOUR VISITS', 'FIRST VISIT', 'LAST VISIT', 'ID']);
    // A system nobody visited and nothing shows is not on the map.
    expect(detailsFor(null, JOURNEYS, FAR.id64)).toBeNull();
  });
});

describe('the showing in words, for every reader', () => {
  it('says what is on the map, and what is still on its way', () => {
    expect(showingInWords(null)).toBe('Select a result to show it on the map.');
    expect(showingInWords(solShowing())).toBe(
      'Sol and the 50 real stars within 15 ly of it.',
    );
    expect(
      showingInWords(solShowing({ stars: [], starsState: 'loading' })),
    ).toBe('Flying to Sol. Looking for the real stars within 15 ly of it…');
    expect(
      showingInWords(solShowing({ stars: [], starsState: 'failed' })),
    ).toBe('Sol is on the map. The stars within 15 ly of it did not load.');
    expect(showingInWords(solShowing({ starsCutShort: true }))).toBe(
      'Sol and 50 of the real stars within 15 ly of it: more than the site returns at once.',
    );
    expect(
      showingInWords(solShowing({ stars: solShowing().stars.slice(0, 1) })),
    ).toBe('Sol and the 1 real star within 15 ly of it.');
  });
});
