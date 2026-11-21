// What a visitor does on the site, start to finish, in a real browser.
//
// The API's answers are played back from files: real answers the live site
// gave to a signed-out visitor on 15 November 2026 (cypress/fixtures/journeys).
// So these run with no API and no database, and the same on every machine.
// The system looked at, LP 634-1, is a real one near Sol with five bodies and
// no stations, so no player's fleet carrier is named in a fixture.
//
// Any question the page asks the API that is not listed here fails the
// journey: a page that starts asking for something new must be given a real
// answer for it here, not left to fail quietly.
//
// The galaxy map is not tested here. It needs a real graphics card and is
// recorded with Playwright on one (CLAUDE.md, "Tests: one job per tool"). Each
// journey runs as on a device whose graphics cannot draw it, which the site
// promises to cope with: "Find, Inspect and Plan work without it."

const SOL = '10477373803';
const LP_634_1 = '6131166483162';

/** Questions the page asked that no journey has an answer for. */
let unanswered: string[] = [];

/** The browser cannot draw the maps: every request for WebGL is refused. */
function withoutGraphics(win: Cypress.AUTWindow): void {
  const canvas = win.HTMLCanvasElement.prototype;
  const ask = canvas.getContext;
  canvas.getContext = function (
    this: HTMLCanvasElement,
    kind: string,
    ...rest: unknown[]
  ) {
    if (kind.startsWith('webgl') || kind === 'webgpu') return null;
    return (ask as (...args: unknown[]) => unknown).call(this, kind, ...rest);
  } as typeof canvas.getContext;
}

function visit(path: string): void {
  cy.visit(path, { onBeforeLoad: withoutGraphics });
}

/** The answers every journey needs: nobody is signed in, and nothing is watched. */
function aSignedOutVisitor(): void {
  // Defined first, so it is asked last: Cypress tries the newest rule first.
  cy.intercept({ url: '/api/**' }, (request) => {
    unanswered.push(`${request.method} ${new URL(request.url).pathname}`);
    request.reply({
      statusCode: 501,
      body: { detail: 'no journey answers this' },
    });
  });
  cy.intercept('GET', '/api/v1/auth/session', {
    fixture: 'journeys/session-guest.json',
  }).as('session');
  // Each browser makes up its own watchlist key. A new key's list is empty,
  // which is what the live site answers.
  cy.intercept(
    'GET',
    /^\/api\/v2\/watchlist\/[A-Za-z0-9_-]{16,128}$/u,
    (request) => {
      const key = new URL(request.url).pathname.split('/').pop();
      request.reply({ sync_key: key, watchlist: [] });
    },
  );
}

/** Suggestions are asked for from the second letter typed: "So", then "Sol". */
function suggestionsForSol(): void {
  // Sol is selected when it is searched near; the bar above the page asks
  // for its name (recorded from the live site on 20 November 2026).
  cy.intercept('GET', `/api/v1/systems/${SOL}`, {
    fixture: 'journeys/sol-system.json',
  });
  cy.intercept('GET', '/api/local/autocomplete?q=So&limit=10', {
    fixture: 'journeys/autocomplete-so.json',
  });
  cy.intercept('GET', '/api/local/autocomplete?q=Sol&limit=10', {
    fixture: 'journeys/autocomplete-sol.json',
  }).as('suggestions');
}

function findAnswersNearSol(): void {
  suggestionsForSol();
  cy.intercept('POST', '/api/local/search', {
    fixture: 'journeys/find-near-sol.json',
  }).as('search');
}

function theSystemsPageAnswers(): void {
  const answers: Array<[string, string]> = [
    ['/api/v1/generation', 'generation'],
    [`/api/v1/systems/${LP_634_1}`, 'system'],
    [`/api/v1/systems/${LP_634_1}/rings`, 'rings'],
    [`/api/v1/systems/${LP_634_1}/bodies`, 'bodies'],
    [`/api/v1/systems/${LP_634_1}/stations`, 'stations'],
    [`/api/ratings/v4/systems/${LP_634_1}`, 'ratings'],
    [`/api/v1/plan/systems/${LP_634_1}/bodies`, 'plan-bodies'],
  ];
  for (const [path, file] of answers) {
    cy.intercept('GET', path, { fixture: `journeys/lp-634-1/${file}.json` });
  }
}

function searchNearSol(): void {
  cy.get('#system-search').type('Sol');
  cy.wait('@suggestions');
  cy.contains('[role=option]', /^Sol\s*10477373803$/u).click();
}

describe('What a visitor does', () => {
  beforeEach(() => {
    unanswered = [];
    cy.viewport(1440, 900);
    aSignedOutVisitor();
  });

  afterEach(() => {
    cy.then(() => {
      expect(
        unanswered,
        'questions the page asked that no journey answers',
      ).to.deep.equal([]);
    });
  });

  it('arrives, and is shown the three things the site does', () => {
    // The home page asks whether the site is well.
    cy.intercept('GET', '/api/health', { fixture: 'journeys/health.json' });
    visit('/');
    cy.wait('@session');
    cy.get('h1')
      .should('have.length', 1)
      .and('have.text', 'Find your next frontier.');
    cy.contains('button', 'Sign in with Frontier').should('be.visible');
    cy.get('header nav a').then(($links) => {
      expect(
        $links
          .toArray()
          .map((link) => [link.textContent?.trim(), link.getAttribute('href')]),
      ).to.deep.equal([
        ['Explore', '/explore'],
        ['Inspect', '/inspect'],
        ['Plan', '/plan'],
      ]);
    });
    cy.get('a.journey-step').then(($steps) => {
      expect(
        $steps.toArray().map((step) => step.getAttribute('href')),
      ).to.deep.equal(['/explore', '/inspect', '/plan']);
    });
    // The search box on the home page leads into Explore.
    cy.get('#home-reference-system').should('have.attr', 'role', 'combobox');
    cy.contains('a', 'Or open Explore without a reference system').click();
    cy.location('pathname').should('eq', '/explore');
    cy.get('#finder-title').should('be.visible');
  });

  it('finds systems near Sol, and is told how many there are', () => {
    findAnswersNearSol();
    visit('/explore');
    searchNearSol();

    // What was asked: outwards from Sol, any colony type, tier C or better
    // (a Best Colony Potential of 45 or more), the first 50.
    cy.wait('@search')
      .its('request.body')
      .should((asked) => {
        expect(asked.reference_coords).to.deep.equal({ x: 0, y: 0, z: 0 });
        expect(asked.filters).to.deep.equal({
          distance: { min: 0, max: 500 },
          economy: 'any',
        });
        expect(asked).to.include({
          sort_by: 'distance',
          size: 50,
          from: 0,
          reach_ly: 250,
          min_development_score: 45,
        });
      });

    cy.contains('Showing 50 of 1,000 (results 1–50)').should('be.visible');
    cy.contains(
      '1,000+ matches; navigation is limited to the first 1,000 results.',
    );
    cy.contains('Within 500 LY of Sol · tier C or better · sorted by distance');
    // The bar above the page names the system, not its id64.
    cy.get('[data-testid="selected-system-context"]').should(
      'contain.text',
      'Selected system Sol',
    );
    cy.get('li[data-system-result]').should('have.length', 50);

    // Nearest first, with what the catalogue knows about each.
    cy.get(`li[data-system-result="5367098657608"]`).within(() => {
      cy.contains('strong', "Teegarden's star");
      cy.contains('12.43 LY');
      cy.contains('Best Colony Potential: 53');
      cy.contains('Primary: Stronghold');
    });
    cy.get('li[data-system-result]').then(($rows) => {
      const lightYears = $rows
        .toArray()
        .map((row) => Number(/([\d.]+) LY/u.exec(row.textContent ?? '')?.[1]));
      expect(lightYears.every((value) => Number.isFinite(value))).to.equal(
        true,
      );
      expect([...lightYears].sort((a, b) => a - b)).to.deep.equal(lightYears);
    });

    // Weak systems are left out: every one shown is tier C or better.
    cy.get('li[data-system-result]').each(($row) => {
      const score = Number(
        /Best Colony Potential: (\d+)/u.exec($row.text())?.[1],
      );
      expect(score).to.be.at.least(45);
    });

    // The map cannot start here; the page says so and Find carries on.
    cy.contains(
      '[role=alert]',
      'The galaxy map could not start on this device.',
    );
  });

  it('opens one of the systems found, and reads its bodies and stations', () => {
    findAnswersNearSol();
    theSystemsPageAnswers();
    visit('/explore');
    searchNearSol();
    cy.wait('@search');

    cy.get(`li[data-system-result="${LP_634_1}"]`).within(() => {
      cy.contains('strong', 'LP 634-1');
      cy.get('a[aria-label="Inspect LP 634-1"]').click();
    });

    cy.location('pathname').should('eq', '/inspect');
    cy.location('search').should('contain', `system=${LP_634_1}`);
    cy.get('h1').should('contain.text', 'LP 634-1');
    cy.contains(`ID64 ${LP_634_1}`);
    cy.contains('-23.34, -9.78, 28.19 ly');
    cy.contains('DA6 VII');

    // Overview: the seven economy potentials, highest first.
    cy.contains('[role=tab]', 'Overview').should(
      'have.attr',
      'aria-selected',
      'true',
    );
    cy.contains('tr', 'Industrial').should('contain.text', '75');
    cy.contains('tr', 'Tourism').should('contain.text', '70');

    // Bodies: the star and its four icy bodies, by their real names.
    cy.contains('[role=tab]', 'Bodies').click();
    for (const name of [
      'LP 634-1 1',
      'LP 634-1 1 a',
      'LP 634-1 2',
      'LP 634-1 3',
    ]) {
      cy.contains(name).should('exist');
    }
    cy.contains('Star · DA6 VII');
    cy.contains('Icy body');

    // Stations: there are none, and the page says so; it is not left blank.
    cy.contains('[role=tab]', 'Stations').click();
    cy.contains('No stations are recorded for this system.').should(
      'be.visible',
    );

    // And the way back leads to the same search, not to an empty Explore;
    // the other way searches near LP 634-1 instead.
    cy.contains('a', 'Back to results')
      .should('have.attr', 'href')
      .and('match', /^\/explore\?/u)
      .and('contain', `near=${SOL}`);
    cy.contains('a', 'Search near this system')
      .should('have.attr', 'href')
      .and('contain', `near=${LP_634_1}`)
      .and('contain', `selected=${LP_634_1}`);
  });

  it('is told when Find cannot answer, and is not shown an empty list as if it were the answer', () => {
    suggestionsForSol();
    // The API's own answer when its database cannot be asked.
    cy.intercept('POST', '/api/local/search', {
      statusCode: 503,
      headers: { 'content-type': 'application/problem+json' },
      body: {
        type: 'https://ed-finder.app/problem/search-unavailable',
        title: 'Search backend temporarily unavailable',
        status: 503,
      },
    }).as('search');
    visit('/explore');
    searchNearSol();
    cy.wait('@search');

    cy.contains(
      '[role=alert]',
      'Discovery results could not be loaded. Try again shortly.',
    )
      .should('be.visible')
      .within(() => cy.contains('button', 'Retry'));
    cy.get('li[data-system-result]').should('have.length', 0);
  });
});
