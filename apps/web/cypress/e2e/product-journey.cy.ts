import { GALAXY_REGION_NAMES } from '../../src/lib/spatial/galaxy-regions';
import { ARCHETYPES } from '../../src/lib/features/explore/archetypes';
import type {
  RankingIdentity,
  SystemArchetypeExplanationResponse,
} from '../../src/lib/api/generated/types.gen';

type ProductWindow = Window & { __productRuntimeFailures?: string[] };

const readySelector = '[role="status"][data-renderer-state="ready"]';
const canvasSelector = 'canvas[data-spatial-canvas]';
const mapWindowSelector = '[data-explore-map-window]';
const requestTimeout = 20_000;
const ARCHETYPE_KEYS = [
  'paradise',
  'mining_hub',
  'manufacturing_hub',
  'megacomplex',
  'research_hub',
  'stronghold',
  'population_capital',
  'flexible',
] as const;

const visitExplore = (path = '/explore') => {
  cy.intercept('GET', '/api/local/autocomplete*').as('autocomplete');
  cy.intercept('POST', '/api/local/search').as('search');
  cy.visit(path, {
    onBeforeLoad(window) {
      const productWindow = window as ProductWindow;
      productWindow.__productRuntimeFailures = [];
      window.addEventListener('error', () => {
        productWindow.__productRuntimeFailures?.push('error');
      });
      window.addEventListener('unhandledrejection', () => {
        productWindow.__productRuntimeFailures?.push('unhandledrejection');
      });
    },
  });
};

// The map is always on screen beside the Finder panel. Show on map centres
// it on the selection or anchor; at phone width, where the panel covers the
// map, it also hides the panel.
const openExploreMap = () => {
  cy.get('#finder-panel').then(($panel) => {
    if ($panel.is(':visible')) cy.get('[data-explore-map-open]').click();
  });
  cy.get(mapWindowSelector).should('be.visible');
  cy.get(`${mapWindowSelector} ${canvasSelector}`).should('have.length', 1);
};

// Back to the Finder panel (a no-op when it is already shown).
const showFinderPanel = () => {
  cy.get('body').then(($body) => {
    const show = $body.find('[data-finder-panel-show]');
    if (show.length) cy.wrap(show).click();
  });
  cy.get('#finder-panel').should('be.visible');
  cy.get(canvasSelector).should('have.length', 1);
};

const assertSpatialResultsReady = () => {
  openExploreMap();
  cy.get(canvasSelector).scrollIntoView();
  cy.get(readySelector, { timeout: 20_000 }).should('be.visible');
  cy.get('.spatial-canvas')
    .invoke('attr', 'data-scene-target-count')
    .then((count) => expect(Number(count)).to.be.greaterThan(0));
};

// URL reads use browser automation, which can stall while the scene renders.
// Wait on DOM readiness first, then allow the URL read its own retry window.
const waitForExploreSettled = () => {
  cy.get('.results-panel', { timeout: 20_000 }).should(
    'have.attr',
    'aria-busy',
    'false',
  );
  // The one map canvas is always mounted. Wait for it to settle before
  // browser automation reads the URL. At phone width the Finder covers the
  // map (kept off screen), so readiness is checked without visibility.
  cy.get('body').then(($body) => {
    if (!$body.find(mapWindowSelector).length) return;
    cy.get(`${mapWindowSelector} ${readySelector}`, { timeout: 20_000 }).should(
      'exist',
    );
    cy.get(`${mapWindowSelector} .spatial-canvas`, { timeout: 20_000 }).should(
      'have.attr',
      'data-camera-transition-active',
      'false',
    );
  });
};

const waitForInspectSettled = () => {
  cy.get('.inspect-page [data-system-id64]', { timeout: 20_000 }).should(
    'have.attr',
    'aria-busy',
    'false',
  );
  cy.get('.inspect-page [role="tabpanel"]:visible', {
    timeout: 20_000,
  }).should('have.length', 1);
};

const inspectTab = (name: 'Overview' | 'Bodies' | 'Stations') =>
  cy.contains('.inspect-page [role="tab"]', new RegExp(`^${name}$`));

const assertInspectTab = (name: 'Overview' | 'Bodies' | 'Stations') => {
  inspectTab(name)
    .should('have.attr', 'aria-selected', 'true')
    .and('have.attr', 'tabindex', '0')
    .then(($tab) => {
      const panelId = $tab.attr('aria-controls');
      expect(panelId, `${name} panel ID`).to.be.a('string');
      expect(panelId).not.to.equal('');
      cy.get(`[id="${panelId}"]`)
        .should('be.visible')
        .and('have.attr', 'role', 'tabpanel')
        .and('have.attr', 'aria-labelledby', $tab.attr('id'));
    });
  cy.get('.inspect-page [role="tab"][tabindex="0"]').should('have.length', 1);
  cy.get('.inspect-page [role="tabpanel"][hidden]')
    .should('have.length', 2)
    .each(($panel) => {
      expect($panel).to.have.attr('inert');
      expect(
        $panel[0].contains($panel[0].ownerDocument.activeElement),
      ).to.equal(false);
    });
};

const evidenceDialog = '[data-evidence-popout][aria-modal="false"]';

const preferReducedMotion = (window: Window) => {
  const matchMedia = window.matchMedia.bind(window);
  window.matchMedia = (query) => {
    const media = matchMedia(query);
    if (query === '(prefers-reduced-motion: reduce)')
      Object.defineProperty(media, 'matches', { value: true });
    return media;
  };
};

let anchorAutocompleteCount = 0;
const keyboardChooseAnchor = (query: string) => {
  // Wait for the complete query only: typing cancels the shorter prefix
  // requests, and a cancelled request has no response to assert on.
  const alias = `anchorAutocomplete${++anchorAutocompleteCount}`;
  cy.intercept({
    method: 'GET',
    pathname: '/api/local/autocomplete',
    query: { q: query },
  }).as(alias);
  cy.get('#system-search').clear().type(query);
  cy.wait(`@${alias}`).its('response.statusCode').should('eq', 200);
  cy.get('[role="listbox"] [role="option"]', { timeout: 10_000 }).should(
    'have.length.greaterThan',
    0,
  );
  cy.get('#system-search').type('{downArrow}{enter}');
  cy.wait('@search').its('response.statusCode').should('eq', 200);
};

// PostgreSQL fixture-output authority: tests/test_seed_cypress_v3_generation.py,
// EXPECTED_FLEXIBLE_B_ROWS / EXPECTED_SUMMARY / EXPECTED_WIDE_ARCHETYPES and
// EXPECTED_B_FLOOR_ORDER. All eight scores are pinned there already. The best
// other non-Flexible scores are Research Hub 82, Stronghold 74 and Stronghold
// 57; each Flexible score clears its rival by >= 3 (section 9, 2026-10-31).
const FLEXIBLE_B_FIXTURE_ROWS = [
  {
    id64: '9007199254740993',
    name: 'V3 Lossless Reach',
    score: 100,
    tier: 'S',
    overall: 100,
    confidence: 0.8665,
    completeness: 0.6481,
    secondary: 'Research Hub',
    rival: 'research_hub',
    rivalScore: 82,
    fitLabel: 'Clear fit',
    evidence: 'Fit confidence 87%; evidence 65% complete',
  },
  {
    id64: '10477373803000',
    name: 'Achenar',
    score: 100,
    tier: 'S',
    overall: 100,
    confidence: 0.8486,
    completeness: 0.6356,
    secondary: 'Stronghold',
    rival: 'stronghold',
    rivalScore: 74,
    fitLabel: 'Clear fit',
    evidence: 'Fit confidence 85%; evidence 64% complete',
  },
  {
    id64: '158872029',
    name: 'HD 38179',
    score: 75,
    tier: 'B',
    overall: 75,
    confidence: 0.803,
    completeness: 0.631,
    secondary: 'Stronghold',
    rival: 'stronghold',
    rivalScore: 57,
    fitLabel: 'Clear fit',
    evidence: 'Fit confidence 80%; evidence 63% complete',
  },
] as const;

const assertFinderUrl = (
  archetype: 'any' | 'flexible',
  offset: number,
  selected: string | null,
  minimumTier: 'B' | 'A' = 'B',
  overlay = false,
) => {
  waitForExploreSettled();
  cy.location('pathname', { timeout: 15_000 }).should('eq', '/explore');
  cy.location('search', { timeout: 15_000 }).should((search) => {
    const params = new URLSearchParams(search);
    expect(params.get('archetype')).to.equal(
      archetype === 'any' ? null : archetype,
    );
    expect(params.get('min-tier')).to.equal(
      archetype === 'any' || minimumTier === 'B' ? null : minimumTier,
    );
    expect(params.get('page_size')).to.equal('1');
    expect(params.get('offset')).to.equal(offset === 0 ? null : String(offset));
    expect(params.getAll('selected')).to.deep.equal(selected ? [selected] : []);
    expect(params.getAll('system')).to.deep.equal(
      overlay && selected ? [selected] : [],
    );
  });
  cy.get(`input[type="radio"][value="${archetype}"]`).should('be.checked');
  if (archetype === 'any') cy.get('.minimum-tier').should('not.exist');
  else cy.get('.minimum-tier select').should('have.value', minimumTier);
  cy.get('.page-size input').should('have.value', '1');
};

const assertRankingsRequest = (
  offset: number,
  minimumTier: 'B' | 'A' = 'B',
) => {
  const expected = FLEXIBLE_B_FIXTURE_ROWS[offset];
  return cy
    .wait('@rankings', { requestTimeout })
    .then(({ request, response }) => {
      const params = new URL(request.url).searchParams;
      expect(Object.fromEntries(params)).to.deep.equal({
        archetype: 'flexible',
        min_score: minimumTier === 'B' ? '60' : '76',
        limit: '1',
        offset: String(offset),
      });
      expect(response?.statusCode).to.equal(200);
      expect(response?.body).to.include({
        archetype: 'flexible',
        count: 1,
        total: minimumTier === 'B' ? 3 : 2,
        is_truncated: false,
      });
      expect(response?.body.results).to.have.length(1);
      // Cypress's JSON decoder cannot preserve the unsafe integer ID64. Its
      // exact identity is checked on the DOM and URL, never rounded here.
      expect(response?.body.results[0]).to.include({
        name: expected.name,
        score: expected.score,
        tier: expected.tier,
        selected_archetype: 'flexible',
        primary_archetype: 'flexible',
        secondary_archetype: expected.rival,
        confidence: expected.confidence,
        completeness: expected.completeness,
        overall_development_potential: expected.overall,
        rival_archetype: expected.rival,
        rival_score: expected.rivalScore,
      });
    });
};

const assertFlexiblePage = (
  offset: number,
  total = 3,
  selected: string | null = null,
) => {
  const expected = FLEXIBLE_B_FIXTURE_ROWS[offset];
  cy.get('.results-panel').should('have.attr', 'aria-busy', 'false');
  cy.get('[data-system-result]').should('have.length', 1);
  cy.get(`[data-system-result="${expected.id64}"]`).within(() => {
    cy.get('.result-title').should('contain.text', expected.name);
    cy.get('.selected-fit')
      .should(
        'contain.text',
        `Flexible Multi-Role Colony fit: ${expected.score}`,
      )
      .find('[data-tier]')
      .should('have.attr', 'data-tier', expected.tier)
      .and('have.text', `Tier ${expected.tier}`)
      .and('have.css', 'white-space', 'nowrap');
    cy.get('.overall-potential')
      .should('contain.text', `Best Colony Potential: ${expected.overall}`)
      .find('[data-tier]')
      .should('not.exist'); // Slice 1b has not supplied best_tier.
    cy.get('.classification-types')
      .should('contain.text', 'Primary: Flexible Multi-Role Colony;')
      .and('contain.text', `Secondary: ${expected.secondary}`);
    cy.get('.fit-label').should('have.text', expected.fitLabel);
    cy.get('.evidence-badge').should('have.text', 'Fit confidence: moderate');
    cy.get('.evidence-description').should('have.text', expected.evidence);
    cy.get('[data-result-select]').should(
      'have.attr',
      'aria-pressed',
      String(selected === expected.id64),
    );
  });
  cy.get('.ranking-summary').should(
    'contain.text',
    `Showing 1 of ${total} (results ${offset + 1}–${offset + 1})`,
  );
  assertSpatialResultsReady();
  cy.get('.spatial-canvas').should(
    'have.attr',
    'data-system-target-count',
    selected && selected !== expected.id64 ? '2' : '1',
  );
};

const assertAnyPage = (selected: string | null) => {
  cy.wait('@search', { requestTimeout }).then(({ request, response }) => {
    expect(request.body).to.deep.equal({
      galaxy_wide: true,
      sort_by: 'development',
      size: 1,
      from: 0,
    });
    expect(request.body).not.to.have.property('min_development_score');
    expect(response?.statusCode).to.equal(200);
    expect(response?.body).to.include({ total: 3, count: 1 });
    expect(response?.body.results[0].name).to.equal('V3 Lossless Reach');
  });
  assertFinderUrl('any', 0, selected);
  cy.contains('label', 'Minimum tier').should('not.exist');
  cy.get('.results-panel').should('have.attr', 'aria-busy', 'false');
  cy.get('[data-system-result]').should('have.length', 1);
  cy.get('[data-system-result="9007199254740993"]').within(() => {
    cy.get('.selected-fit, .fit-label').should('not.exist');
    cy.get('.evidence-badge').should(
      'have.text',
      'Evidence confidence: moderate',
    );
    cy.get('.evidence-description').should(
      'have.text',
      'Evidence confidence 82%; evidence 65% complete',
    );
    cy.get('[data-result-select]').should(
      'have.attr',
      'aria-pressed',
      String(selected === '9007199254740993'),
    );
  });
  cy.get('.ranking-summary').should(
    'contain.text',
    'Showing 1 of 3 (results 1–1)',
  );
  assertSpatialResultsReady();
  cy.get('.spatial-canvas').should(
    'have.attr',
    'data-system-target-count',
    '1',
  );
};

const assertRetainedSelection = () => {
  openExploreMap();
  const selected = FLEXIBLE_B_FIXTURE_ROWS[0];
  cy.get('[data-testid="selected-system-context"]').should(
    'contain.text',
    selected.id64,
  );
  cy.get(`[data-selected-system-card="${selected.id64}"]`)
    .should('have.length', 1)
    .and('contain.text', selected.name);
  cy.get(canvasSelector).scrollIntoView();
  cy.get(`[data-map-label-key="system:${selected.id64}"]`, { timeout: 20_000 })
    .should('have.length', 1)
    .and('be.visible')
    .and('have.attr', 'data-map-label-selected', 'true')
    .and('contain.text', selected.name);
  // Ranking/history steps use the results in the Finder panel.
  showFinderPanel();
};

const openMapLayers = () => {
  cy.get('[data-map-layers-toggle]').then(($trigger) => {
    if ($trigger.attr('aria-expanded') !== 'true') cy.wrap($trigger).click();
  });
  cy.get('[data-map-layers-dialog]')
    .should('be.visible')
    .and('have.attr', 'aria-modal', 'false');
};

const closeMapLayers = () => {
  cy.get('[data-map-layers-toggle]')
    .should('have.attr', 'aria-expanded', 'true')
    .click()
    .should('have.attr', 'aria-expanded', 'false');
};

const openMapRegions = () => {
  cy.get('[data-map-regions-trigger]').then(($trigger) => {
    if ($trigger.attr('aria-expanded') !== 'true') cy.wrap($trigger).click();
  });
  cy.get('[data-map-regions-panel]')
    .should('be.visible')
    .and('have.attr', 'aria-modal', 'false');
};

const closeMapRegions = () => {
  cy.get('[data-map-regions-trigger]')
    .should('have.attr', 'aria-expanded', 'true')
    .click()
    .should('have.attr', 'aria-expanded', 'false');
};

const captureFinder = (state: 'any' | 'flexible' | 'flexible-explanation') => {
  openExploreMap();
  openMapRegions();
  cy.get('[data-region-resource-state="ready"]', { timeout: 20_000 })
    .scrollIntoView()
    .should('contain.text', '42/42 regions');
  closeMapRegions();
  openMapLayers();
  cy.get('[data-nebula-resource-state="ready"]', { timeout: 20_000 }).should(
    'contain.text',
    '5,842 catalogue records',
  );
  closeMapLayers();
  showFinderPanel();
  cy.document().then((document) => {
    const style = document.createElement('style');
    style.textContent =
      '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';
    document.head.append(style);
  });
  for (const [width, height] of [
    [1280, 800],
    [390, 844],
  ]) {
    cy.viewport(width, height);
    cy.get('.results-panel').scrollIntoView();
    // The Finder shares the screen with the one always-mounted map.
    cy.get(canvasSelector).should('have.length', 1);
    cy.screenshot(
      `product/finder-${state}-${Cypress.browser.name}-${width}x${height}`,
      {
        capture: 'viewport',
        overwrite: true,
        disableTimersAndAnimations: true,
      },
    );
    if (state === 'flexible-explanation') {
      cy.get('[data-system-result]').screenshot(
        `product/finder-expanded-card-${Cypress.browser.name}-${width}x${height}`,
        { overwrite: true, disableTimersAndAnimations: true },
      );
    }
  }
  cy.viewport(1280, 800);
};

describe('V3 home to Explore product journey', () => {
  const referenceId = '9007199254740993';
  const referenceName = 'V3 Lossless Reach';
  const frontierCredit =
    'ED-Finder was created using assets and imagery from Elite Dangerous, with the permission of Frontier Developments plc, for non-commercial purposes. It is not endorsed by nor reflects the views or opinions of Frontier Developments and no employee of Frontier Developments was involved in the making of it.';

  // Successful, ordinary API contracts isolate the home form from the seeded
  // map checkpoint below. No review-only runtime or failure state is used.
  const installHomeFixtures = () => {
    cy.intercept('GET', '/api/health', {
      status: 'ok',
      database: 'connected',
      version: 'test',
      build_sha: 'home-product-fixture',
    }).as('homeHealth');
    cy.intercept('GET', '/api/v1/auth/session', {
      authenticated: false,
      user: null,
      owner_claim_available: false,
    }).as('homeSession');
    cy.intercept('GET', '/api/v2/watchlist/*', (request) => {
      request.reply({
        sync_key: new URL(request.url).pathname.split('/').at(-1),
        watchlist: [],
      });
    });
    cy.intercept('GET', '/api/local/autocomplete*', {
      results: [
        { id64: referenceId, name: referenceName, x: 10, y: 20, z: 30 },
      ],
    }).as('homeAutocomplete');
    cy.intercept('GET', '/api/v1/generation', {
      generation_id: 'home-product-generation',
      generation_key: 'home-product',
      publication_sequence: '1',
      published_at: '2026-11-03T00:00:00Z',
    });
    cy.intercept('GET', `/api/v1/systems/${referenceId}`, {
      id64: referenceId,
      name: referenceName,
      x_ly: 10,
      y_ly: 20,
      z_ly: 30,
      source_body_count: 0,
      loaded_body_count: 0,
      lifecycle_state: 'ACTIVE',
      source_updated_at: null,
    });
    for (const resource of ['bodies', 'rings', 'stations']) {
      cy.intercept('GET', `/api/v1/systems/${referenceId}/${resource}`, []);
    }
    cy.intercept('POST', '/api/local/search', {
      results: [
        {
          id64: referenceId,
          name: referenceName,
          coords: { x: 10, y: 20, z: 30 },
          distance: 0,
          overall_development_potential: 100,
          archetype_score: 100,
          archetype_tier: 'S',
          tags: [],
        },
      ],
      count: 1,
      total: 1,
      ranking: {
        ranking_version: 'home-product-ranking',
        ranking_sha256: 'home-product-ranking-sha',
        derived_generation_id: 'home-product-generation',
        publication_sequence: 1,
        archetype_version: 'home-product-archetype',
        archetype_manifest_sha256: 'home-product-manifest-sha',
        archetype_model_sha256: 'home-product-model-sha',
      },
    }).as('homeSearch');
  };

  for (const [width, height, screenshot] of [
    [1280, 800, 'desktop'],
    [390, 844, 'phone'],
  ] as const) {
    it(`finds the chosen reference system from home at ${width}x${height}`, () => {
      cy.viewport(width, height);
      const apiRequests: string[] = [];
      cy.intercept({ pathname: '/api/**', middleware: true }, (request) => {
        apiRequests.push(new URL(request.url).pathname);
      });
      installHomeFixtures();
      cy.visit('/');
      cy.wait(['@homeHealth', '@homeSession'], { requestTimeout: 20_000 });
      cy.get('h1')
        .should('have.length', 1)
        .and('have.text', 'Find your next frontier.');
      cy.contains('.home-hero', 'Your colony starts here').should('be.visible');
      cy.contains('.home-hero', 'Discover promising systems.').should(
        'be.visible',
      );
      cy.contains(
        '.home-hero',
        'Compare colony potential. Plan what comes next.',
      ).should('be.visible');
      cy.get('form[aria-label="Find a system"]').within(() => {
        cy.contains('label', 'Reference system').should('be.visible');
        cy.get('[role="combobox"]')
          .should('have.attr', 'aria-expanded', 'false')
          .and('be.visible');
        // The product CI build opts in to F4. A local default-off build can
        // exercise its counterpart with --env finderF4Enabled=false.
        if (Cypress.env('finderF4Enabled') !== false) {
          cy.contains('label', 'Colony archetype').should('be.visible');
          cy.get('select option').should(($options) => {
            expect(
              $options.toArray().map((option) => option.textContent),
            ).to.deep.equal([
              'Any archetype',
              ...ARCHETYPES.map(({ label }) => label),
            ]);
          });
        } else {
          cy.get('select').should('not.exist');
          cy.contains('label', 'Colony archetype').should('not.exist');
        }
      });
      cy.get('.journey-step').should(($steps) => {
        expect(
          $steps.toArray().map((step) => ({
            href: step.getAttribute('href'),
            heading: step.querySelector('h2')?.textContent?.trim(),
          })),
        ).to.deep.equal([
          { href: '/explore', heading: 'Find your system' },
          { href: '/inspect', heading: 'Understand its potential' },
          { href: '/plan', heading: 'Plan your colony' },
        ]);
      });
      cy.get('footer').should(($footer) => {
        expect($footer.text().replace(/\s+/g, ' ')).to.contain(frontierCredit);
      });
      cy.get('.home-hero img').should(($image) => {
        const image = $image[0] as HTMLImageElement;
        expect(image.alt).to.equal('');
        expect(image.getAttribute('aria-hidden')).to.equal('true');
        expect(image.naturalWidth).to.be.greaterThan(0);
        expect(image.currentSrc).to.include(
          `home-hero-black-hole-${width <= 640 ? '960' : '1920'}.webp`,
        );
      });
      cy.document().then((document) => document.fonts.ready);
      cy.window().should((window) => {
        const heroRequests = window.performance
          .getEntriesByType('resource')
          .filter(({ name }) => name.includes('home-hero-black-hole-'));
        expect(heroRequests).to.have.length(1);
        // Auth is the existing shared shell bootstrap; the hero adds no data
        // request until the visitor has typed a two-character query.
        expect([...new Set(apiRequests)].sort()).to.deep.equal([
          '/api/health',
          '/api/v1/auth/session',
        ]);
      });
      cy.injectAxe();
      cy.checkA11y();
      cy.get(
        '.home-hero input, .home-hero select, .home-hero button, .home-hero a, .journey-step',
      ).each(($control) => {
        cy.wrap($control)
          .focus()
          .should(($focused) => {
            const style = getComputedStyle($focused[0]);
            expect(style.animationName).to.equal('none');
            expect(style.transitionDuration, $focused[0].outerHTML).to.equal(
              '0s',
            );
          })
          .blur();
      });
      cy.scrollTo('top');
      cy.screenshot(`product/home-hero-${screenshot}`, {
        capture: 'viewport',
        overwrite: true,
      });

      cy.contains('button', 'Find a system').click();
      cy.get('[role="alert"]').should(
        'have.text',
        'Choose a system from the list',
      );
      cy.get('.home-hero [role="combobox"]').should('be.focused').type('V');
      cy.get('@homeAutocomplete.all').should('have.length', 0);
      cy.checkA11y();
      cy.get('.home-hero [role="combobox"]').type('3 Lossless');
      cy.wait('@homeAutocomplete');
      cy.contains('[role="option"]', referenceName).should('be.visible');
      cy.checkA11y();
      if (width <= 640) {
        cy.get('.home-hero [role="combobox"]')
          .type('{downArrow}')
          .should(($input) => {
            const activeId = $input.attr('aria-activedescendant');
            expect(activeId).to.be.a('string').and.not.equal('');
            expect(
              $input[0].ownerDocument
                .getElementById(activeId!)
                ?.getAttribute('role'),
            ).to.equal('option');
          });
        cy.get('.home-hero [role="combobox"]').type('{enter}');
      } else {
        cy.contains('[role="option"]', referenceName).click();
      }
      cy.location('pathname').should('eq', '/');
      cy.get('.home-hero [role="combobox"]').should(
        'have.value',
        referenceName,
      );
      cy.get('[role="listbox"]').should('not.exist');
      cy.get('[role="alert"]').should('not.exist');
      cy.contains('button', 'Find a system').click();
      cy.location('pathname').should('eq', '/explore');
      cy.location('search').should('eq', `?near=${referenceId}`);
      cy.wait('@homeSearch')
        .its('request.body.reference_coords')
        .should('deep.equal', {
          x: 10,
          y: 20,
          z: 30,
        });
      cy.get(`[data-system-result="${referenceId}"]`).should(
        'contain.text',
        referenceName,
      );
      cy.get('h1').should('contain.text', 'Chart a promising system');
    });
  }
});

describe('V3 Explore to Inspect product checkpoint', () => {
  it('requests complete density for the camera bounds as the view narrows', () => {
    cy.viewport(1280, 800);
    // Observe the normal API request. The product seed need not publish a
    // density pyramid to prove that camera navigation sends bounded coverage.
    cy.intercept('GET', '/api/map/heatmap*').as('density');
    visitExplore();
    openExploreMap();
    cy.get(`${mapWindowSelector} h2`).should('have.text', 'Galaxy map');
    cy.contains(`${mapWindowSelector} button`, /^Local$/)
      .should('be.disabled')
      .and('have.attr', 'aria-describedby')
      .then((id) => {
        cy.get(`#${id}`).should(
          'have.text',
          'Select a system to use the Local camera preset.',
        );
      });
    openMapRegions();
    cy.get('[data-region-resource-state="ready"]', { timeout: 20_000 })
      .scrollIntoView()
      .should('be.visible');
    closeMapRegions();
    cy.get(readySelector, { timeout: 20_000 }).should('be.visible');

    let initialSpan = 0;
    cy.wait('@density', { requestTimeout }).then(({ request }) => {
      const params = new URL(request.url).searchParams;
      expect(params.get('coverage')).to.equal('complete');
      expect(params.get('min_systems')).to.equal('1');
      expect(params.get('max_cells')).to.equal('40000');
      for (const axis of ['x', 'y', 'z']) {
        const minimum = params.get(`min_${axis}`);
        const maximum = params.get(`max_${axis}`);
        expect(minimum, `minimum ${axis} bound`).not.to.equal(null);
        expect(maximum, `maximum ${axis} bound`).not.to.equal(null);
        expect(Number.isFinite(Number(minimum))).to.equal(true);
        expect(Number.isFinite(Number(maximum))).to.equal(true);
        expect(Number(maximum)).to.be.greaterThan(Number(minimum));
      }
      initialSpan = Number(params.get('max_x')) - Number(params.get('min_x'));
    });

    // Eight ordinary zoom stops retain density's visible band while entering
    // a regional view. The settled request must cover that view, not the galaxy.
    for (let step = 0; step < 8; step += 1) {
      cy.get('[aria-label="Zoom in"]').click();
    }
    cy.get<Array<{ request: { url: string } }>>('@density.all', {
      timeout: 20_000,
    }).should((requests) => {
      const latest = requests.at(-1);
      expect(latest).not.to.equal(undefined);
      const params = new URL(latest!.request.url).searchParams;
      expect(params.get('coverage')).to.equal('complete');
      const span = Number(params.get('max_x')) - Number(params.get('min_x'));
      expect(span).to.be.lessThan(initialSpan);
      expect(span).to.be.lessThan(100_000);
      expect(span).to.be.greaterThan(0);
    });
  });

  it('applies body counts near a system and restores the shared search on reload', () => {
    const anchorId = '10477373803000';
    const dialog = '[role="dialog"][data-testid="body-filters-dialog"]';
    let anchoredRequest: Record<string, unknown>;
    let radiusRequest: Record<string, unknown>;
    cy.viewport(1280, 800);
    // All responses come from the normal Product API and its published V3
    // fixture generation. The anchor is chosen through the shared keyboard
    // helper, which waits for the complete autocomplete query and the search.
    cy.intercept('GET', `/api/v1/systems/${anchorId}`).as('bodyFilterAnchor');
    visitExplore();
    cy.contains('button', /^Body filters$/).should('be.disabled');
    cy.get('#search-radius').should('be.disabled');
    cy.get('@search.all').should('have.length', 0);
    keyboardChooseAnchor('Achenar');
    cy.get('@search.all')
      .should('have.length', 1)
      .then((calls) => {
        const { request, response } = (
          calls as unknown as Array<{
            request: {
              body: Record<string, unknown> & {
                sort_by?: string;
                filters?: { distance?: unknown };
              };
            };
            response?: { statusCode: number };
          }>
        )[0]!;
        expect(response?.statusCode).to.equal(200);
        expect(request.body.sort_by).to.equal('distance');
        expect(request.body.filters.distance).to.deep.equal({
          min: 0,
          max: 500,
        });
        expect(request.body).not.to.have.property('body_filters');
        anchoredRequest = request.body;
      });
    waitForExploreSettled();
    cy.get('#results-title').should('contain.text', 'Near Achenar');
    cy.get('#results-scope').should(
      'have.text',
      'Within 500 LY of Achenar · sorted by distance',
    );
    cy.get('#search-radius').should('be.enabled').and('have.value', '500');
    // At 1280px, card facts use the available results column's full width;
    // actions follow the identity without taking space away from its name.
    cy.get('[data-system-result]')
      .first()
      .should(($row) => {
        const row = $row[0];
        const selection = row.querySelector<HTMLElement>('.result-select')!;
        const inspect = row.querySelector<HTMLElement>('.inspect-link')!;
        const bounds = row.getBoundingClientRect();
        const selectionBounds = selection.getBoundingClientRect();
        expect(selectionBounds.width).to.be.at.least(bounds.width - 2);
        expect(inspect.getBoundingClientRect().top).to.be.at.least(
          selectionBounds.bottom,
        );
        for (const field of row.querySelectorAll<HTMLElement>(
          '.result-title strong, .result-title code, .result-facts',
        )) {
          expect(field.scrollWidth).to.be.at.most(field.clientWidth + 1);
        }
        expect(row.textContent).not.to.match(/Star star/i);
      });
    cy.get('[data-system-result]')
      .first()
      .screenshot(
        `product/explore-finder-card-${Cypress.browser.name}-1280x800`,
        { overwrite: true },
      );
    // An unfiltered anchored search may use the wider radii.
    cy.get('#search-radius').select('1000');
    cy.wait('@search', { requestTimeout }).then(({ request, response }) => {
      expect(request.body).to.deep.equal({
        ...anchoredRequest,
        filters: { distance: { min: 0, max: 1000 }, economy: 'any' },
      });
      expect(response?.statusCode).to.equal(200);
    });
    waitForExploreSettled();
    cy.get('@search.all').should('have.length', 2);
    cy.get('#results-scope').should(
      'have.text',
      'Within 1,000 LY of Achenar · sorted by distance',
    );
    cy.location('search', { timeout: 15_000 }).should((search) => {
      const params = new URLSearchParams(search);
      expect(params.getAll('radius')).to.deep.equal(['1000']);
    });
    cy.contains('button', /^Body filters$/).click();
    cy.get(dialog).should('be.visible').and('have.attr', 'aria-modal', 'false');
    cy.get(`${dialog} [aria-label="Earth-like minimum slider"]`)
      .invoke('val', '1')
      .trigger('input');
    cy.get(`${dialog} [aria-label="Earth-like minimum"]`)
      .should('have.value', '1')
      .clear()
      .type('1')
      .blur();
    cy.get(`${dialog} [aria-label="Earth-like Any maximum"]`).should(
      'be.checked',
    );
    cy.contains(`${dialog} [role="tab"]`, 'Planets').should(
      'contain.text',
      '1',
    );
    cy.injectAxe();
    cy.checkA11y(dialog);
    // Finish on the desktop viewport so the pop-out is not still repositioning
    // when Apply is clicked.
    for (const [width, height] of [
      [390, 844],
      [1280, 800],
    ]) {
      cy.viewport(width, height);
      cy.get(`${dialog} [role="tabpanel"]:visible`).scrollTo('top');
      cy.get(dialog).should(($dialog) => {
        const bounds = $dialog[0].getBoundingClientRect();
        const viewport = $dialog[0].ownerDocument.defaultView?.visualViewport;
        expect(bounds.top).to.be.at.least(viewport?.offsetTop ?? 0);
        expect(bounds.bottom).to.be.at.most(
          (viewport?.offsetTop ?? 0) + (viewport?.height ?? height) + 1,
        );
        expect(bounds.left).to.be.at.least(0);
        expect(bounds.right).to.be.at.most(width);
      });
      cy.screenshot(
        `product/explore-body-filters-${Cypress.browser.name}-${width}x${height}`,
        {
          capture: 'viewport',
          overwrite: true,
          disableTimersAndAnimations: true,
        },
      );
    }
    // The anchor search and the 1,000 LY radius search have run.
    cy.get('@search.all').should('have.length', 2);
    cy.contains(`${dialog} button`, /^Apply$/).click({
      scrollBehavior: false,
    });
    cy.wait('@search', { requestTimeout }).then(({ request, response }) => {
      expect(request.body).to.deep.equal({
        ...anchoredRequest,
        body_filters: { elw_count: { min: 1 } },
      });
      expect(response?.statusCode).to.equal(200);
      expect(response?.body.results.length).to.be.greaterThan(0);
    });
    cy.get(dialog).should('not.exist');
    cy.contains('button', /^Body filters$/).should('be.focused');
    waitForExploreSettled();
    cy.get('@search.all').should('have.length', 3);
    cy.contains('Earth-like ≥ 1').should('be.visible');
    cy.get('[aria-label="Applied body filters"] .kit-chip').should(
      'contain.text',
      'Applied',
    );
    // Body filters cap the anchored search at the API's 500 LY; the chosen
    // 1,000 LY stays in the URL and resumes when the filters are cleared.
    cy.get('@search.all')
      .its('2')
      .then(({ request, response }) => {
        expect(request.body).to.deep.equal({
          ...anchoredRequest,
          filters: { distance: { min: 0, max: 500 }, economy: 'any' },
          body_filters: { elw_count: { min: 1 } },
        });
        expect(response?.statusCode).to.equal(200);
        radiusRequest = request.body;
      });
    cy.get('#results-scope').should(
      'have.text',
      'Within 500 LY of Achenar · sorted by distance',
    );
    cy.get('#search-radius').should('have.value', '500');
    cy.get('#search-radius option[value="1000"]').should('be.disabled');
    cy.get('#search-radius option[value="2000"]').should('be.disabled');
    cy.get('#search-radius-capped').should(
      'contain.text',
      'Body filters limit the search to 500 LY; your 1,000 LY radius resumes when the filters are cleared.',
    );
    cy.location('search', { timeout: 15_000 }).should((search) => {
      const params = new URLSearchParams(search);
      expect(params.getAll('near')).to.deep.equal([anchorId]);
      expect(params.getAll('bodies')).to.deep.equal(['elw_count:1-']);
      expect(params.getAll('radius')).to.deep.equal(['1000']);
    });
    // Clear persisted state and SvelteKit's history payload so restoration can
    // only use the URL, even in browsers that retain history state on reload.
    cy.window().then((window) => {
      window.localStorage.clear();
      window.sessionStorage.clear();
      window.history.replaceState({}, '', window.location.href);
    });
    cy.reload();
    cy.wait('@bodyFilterAnchor', { requestTimeout })
      .its('response.statusCode')
      .should('eq', 200);
    cy.wait('@search', { requestTimeout }).then(({ request, response }) => {
      expect(request.body).to.deep.equal(radiusRequest);
      expect(response?.statusCode).to.equal(200);
      expect(response?.body.results.length).to.be.greaterThan(0);
    });
    waitForExploreSettled();
    cy.get('@search.all').should('have.length', 4);
    cy.get('#system-search').should('have.value', 'Achenar');
    cy.get('#results-title').should('contain.text', 'Near Achenar');
    cy.get('#results-scope').should(
      'have.text',
      'Within 500 LY of Achenar · sorted by distance',
    );
    cy.get('#search-radius').should('be.enabled').and('have.value', '500');
    cy.get('#search-radius-capped').should('be.visible');
    cy.get('[data-system-result]').should('have.length.greaterThan', 0);
    cy.contains('Earth-like ≥ 1').should('be.visible');
    cy.get('[aria-label="Applied body filters"] .kit-chip').should(
      'contain.text',
      'Applied',
    );
    cy.get('[aria-label="Remove Earth-like filter"]').should('be.visible');
    cy.contains('button', /^Clear all$/).should('be.visible');
    // These are the existing Body filters tabs; Explore task tabs belong to
    // P2c. Switching them and presenting the map never commits another search.
    cy.contains('button', /^Body filters$/).click();
    cy.contains(`${dialog} [role="tab"]`, 'Stars').click();
    cy.contains(`${dialog} [role="tab"]`, 'Planets').click();
    cy.get(`${dialog} [aria-label="Earth-like minimum"]`).should(
      'have.value',
      '1',
    );
    cy.get(dialog)
      .contains('button', /^Cancel$/)
      .click();
    let retainedSearch = '';
    cy.location('search').then((search) => {
      retainedSearch = search;
    });
    for (const [width, height] of [
      [1280, 800],
      [390, 844],
    ]) {
      cy.viewport(width, height);
      showFinderPanel();
      // Hiding and restoring the Finder panel is presentation only.
      cy.get('[data-finder-panel-hide]').click();
      cy.get('#finder-panel').should('not.be.visible');
      cy.get(mapWindowSelector)
        .should('be.visible')
        .and('have.attr', 'data-finder-panel-open', 'false');
      cy.get('[data-finder-panel-show]').should('be.focused').click();
      cy.get('[data-finder-panel-hide]').should('be.focused');
      cy.get('#system-search').should('have.value', 'Achenar');
      cy.get('#search-radius').should('have.value', '500');
      cy.get('[data-result-select][aria-pressed="true"]')
        .closest('[data-system-result]')
        .should('have.attr', 'data-system-result', anchorId);
      cy.contains('Earth-like ≥ 1').should('be.visible');
      cy.then(() => cy.location('search').should('eq', retainedSearch));
      cy.get('@search.all').should('have.length', 4);
      cy.contains('button', /^Body filters$/).click();
      cy.contains(`${dialog} [role="tab"]`, 'Planets').should(
        'have.attr',
        'aria-selected',
        'true',
      );
      cy.get(`${dialog} [aria-label="Earth-like minimum"]`).should(
        'have.value',
        '1',
      );
      cy.get(dialog)
        .contains('button', /^Cancel$/)
        .click();
    }
    cy.viewport(1280, 800);
    cy.location('search', { timeout: 15_000 }).should((search) => {
      const params = new URLSearchParams(search);
      expect(params.getAll('near')).to.deep.equal([anchorId]);
      expect(params.getAll('bodies')).to.deep.equal(['elw_count:1-']);
      expect(params.getAll('radius')).to.deep.equal(['1000']);
    });
  });

  it('ranks the pinned Flexible B corpus, retains URL selection and restores Finder history', () => {
    const selectedId = FLEXIBLE_B_FIXTURE_ROWS[0].id64;
    let copiedExploreUrl = '';
    let copiedExploreQuery = '';
    let rankingIdentity: RankingIdentity;
    cy.viewport(1280, 800);
    // Observe real API traffic only: the Product lane never supplies a response.
    cy.intercept('GET', '/api/archetypes/rankings*').as('rankings');
    cy.intercept('GET', '/api/archetypes/system/*/explanation*').as(
      'archetypeExplanation',
    );
    cy.intercept('GET', /\/api\/archetypes\/system\/\d+(?:\?.*)?$/).as(
      'legacyArchetype',
    );
    visitExplore('/explore?page_size=1');

    // Section 7 steps 1–3: Any is floor-free; keyboard selection commits the
    // default B floor and the bounded one-row ranking request.
    assertAnyPage(null);
    captureFinder('any');
    cy.get('input[type="radio"][value="flexible"]')
      .focus()
      .should('be.focused');
    cy.press(Cypress.Keyboard.Keys.SPACE);
    cy.get('input[type="radio"][value="flexible"]').should('be.checked');
    assertRankingsRequest(0).then(({ response }) => {
      rankingIdentity = response?.body.ranking;
    });
    assertFinderUrl('flexible', 0, null);
    waitForExploreSettled();
    cy.location('search', { timeout: 15_000 }).should(
      'eq',
      '?archetype=flexible&page_size=1',
    );
    assertFlexiblePage(0);

    // F4e: opening the sibling disclosure fetches the real, version-pinned
    // explanation lazily without selecting the card. The endpoint verifies
    // recomputation against all eight stored archetype scores and tiers.
    const card = `[data-system-result="${selectedId}"]`;
    const explanationRegion = `${card} [data-archetype-explanation]`;
    cy.get('@archetypeExplanation.all').should('have.length', 0);
    cy.get(`${card} [data-archetype-explanation-toggle]`)
      .should('have.attr', 'aria-expanded', 'false')
      .click()
      .should('have.attr', 'aria-expanded', 'true');
    cy.wait('@archetypeExplanation', { requestTimeout }).then(
      ({ request, response }) => {
        const url = new URL(request.url);
        expect(url.pathname).to.equal(
          `/api/archetypes/system/${selectedId}/explanation`,
        );
        expect(Object.fromEntries(url.searchParams)).to.deep.equal({
          expected_generation_id: rankingIdentity.derived_generation_id,
          expected_archetype_version: rankingIdentity.archetype_version,
          expected_manifest_sha256: rankingIdentity.archetype_manifest_sha256,
        });
        expect(response?.statusCode).to.equal(200);
        const explanation: SystemArchetypeExplanationResponse = response?.body;
        expect(explanation.ranking).to.deep.equal(rankingIdentity);
        expect(explanation.archetypes.map(({ key }) => key)).to.have.members([
          ...ARCHETYPE_KEYS,
        ]);
        expect(explanation.archetypes).to.have.length(8);
        const flexible = explanation.archetypes.find(
          ({ key }) => key === 'flexible',
        );
        expect(flexible).to.include({
          score: FLEXIBLE_B_FIXTURE_ROWS[0].score,
          tier: FLEXIBLE_B_FIXTURE_ROWS[0].tier,
        });
        cy.get(`${card} .selected-fit`)
          .should(
            'contain.text',
            `Flexible Multi-Role Colony fit: ${flexible?.score}`,
          )
          .find('[data-tier]')
          .should('have.attr', 'data-tier', flexible?.tier);
        for (const entry of explanation.archetypes) {
          cy.get(
            `${explanationRegion} [data-explanation-archetype="${entry.key}"]`,
          ).within(() => {
            cy.get('.explanation-score').should(
              'have.text',
              String(entry.score),
            );
            cy.get('[data-tier]')
              .should('have.attr', 'data-tier', entry.tier)
              .and('have.text', `Tier ${entry.tier}`);
          });
        }
      },
    );
    cy.get(`${explanationRegion} [data-explanation-archetype]`).should(
      ($sections) => {
        expect(
          [...$sections].map((section) => section.dataset.explanationArchetype),
        ).to.have.members([...ARCHETYPE_KEYS]);
      },
    );
    cy.get(`${card} [data-result-select]`).should(
      'have.attr',
      'aria-pressed',
      'false',
    );
    cy.get('@legacyArchetype.all').should('have.length', 0);
    cy.injectAxe();
    cy.checkA11y(explanationRegion);
    captureFinder('flexible-explanation');
    cy.get(`${card} [data-archetype-explanation-toggle]`)
      .click()
      .should('have.attr', 'aria-expanded', 'false');

    // Steps 4–5: the current page owns the results, while selected= owns the
    // retained point independently. All three pages come from PostgreSQL.
    cy.get(`[data-system-result="${selectedId}"] [data-result-select]`).click();
    assertFinderUrl('flexible', 0, selectedId);
    assertRetainedSelection();
    cy.get('[data-testid="system-detail-modal"]').should('not.exist');
    for (const offset of [1, 2]) {
      cy.contains('[aria-label="Result pages"] button', 'Next').click();
      assertRankingsRequest(offset);
      assertFinderUrl('flexible', offset, selectedId);
      assertFlexiblePage(offset, 3, selectedId);
      cy.get(`[data-system-result="${selectedId}"]`).should('not.exist');
      assertRetainedSelection();
      cy.get('[data-testid="system-detail-modal"]').should('not.exist');
    }
    cy.contains('[aria-label="Result pages"] button', 'Next').should(
      'be.disabled',
    );
    openExploreMap();
    waitForExploreSettled();
    cy.location({ timeout: 15_000 }).then(({ pathname, search }) => {
      copiedExploreUrl = `${pathname}${search}`;
      copiedExploreQuery = search.slice(1);
      const inspect = `/inspect?${new URLSearchParams({
        system: selectedId,
        from: copiedExploreQuery,
      })}`;
      cy.get('[data-testid="selected-system-context"] a')
        .first()
        .should('have.attr', 'href', inspect);
      cy.contains(
        `${mapWindowSelector} a`,
        `Inspect ${FLEXIBLE_B_FIXTURE_ROWS[0].name}`,
      ).should('have.attr', 'href', inspect);
    });

    // F4 design section 7, step 5: a full document load drops in-memory
    // result/camera/query state and both persistence stores. The facade now
    // reads the V3 system route to reconstruct the omitted selected point.
    cy.intercept('GET', `/api/v1/systems/${selectedId}`).as('selectedPoint');
    cy.clearLocalStorage();
    cy.window().then((window) => window.sessionStorage.clear());
    cy.then(() => {
      cy.visit(copiedExploreUrl, {
        onBeforeLoad(window) {
          window.localStorage.clear();
          window.sessionStorage.clear();
        },
      });
    });
    assertRankingsRequest(2);
    cy.wait('@selectedPoint', { requestTimeout })
      .its('response.statusCode')
      .should('eq', 200);
    assertFinderUrl('flexible', 2, selectedId);
    assertFlexiblePage(2, 3, selectedId);
    cy.get(`[data-system-result="${selectedId}"]`).should('not.exist');
    assertRetainedSelection();
    cy.get('[data-testid="system-detail-modal"]').should('not.exist');
    cy.then(() => {
      const inspect = `/inspect?${new URLSearchParams({
        system: selectedId,
        from: copiedExploreQuery,
      })}`;
      cy.get('[data-testid="selected-system-context"] a')
        .first()
        .should('have.attr', 'href', inspect)
        .click();
    });
    waitForInspectSettled();
    cy.location('pathname', { timeout: 15_000 }).should('eq', '/inspect');
    cy.location('search', { timeout: 15_000 }).should((search) => {
      const params = new URLSearchParams(search);
      expect(params.get('system')).to.equal(selectedId);
      expect(params.get('from')).to.equal(copiedExploreQuery);
    });
    cy.get(`[data-system-id64="${selectedId}"]`, { timeout: 20_000 }).should(
      'contain.text',
      'V3 Lossless Reach',
    );
    cy.contains('a', 'Open in Explore').click();
    assertRankingsRequest(2);
    assertFinderUrl('flexible', 2, selectedId, 'B', true);
    cy.get('[data-testid="system-detail-modal"]').should('be.visible');
    cy.get('[aria-label="Close system detail"]').click();
    assertFinderUrl('flexible', 2, selectedId);
    assertFlexiblePage(2, 3, selectedId);
    assertRetainedSelection();
    cy.get('[data-testid="system-detail-modal"]').should('not.exist');
    assertRetainedSelection();
    cy.injectAxe();
    cy.checkA11y();

    // Step 6: picker, a non-default tier, and pagination each create history.
    // Back/Forward must refetch the request encoded by every restored URL;
    // selection survives Any, reranking and pages that omit the selected row.
    cy.get('input[type="radio"][value="any"]').check();
    assertAnyPage(selectedId);
    assertRetainedSelection();
    cy.get('input[type="radio"][value="flexible"]').check();
    assertRankingsRequest(0);
    assertFinderUrl('flexible', 0, selectedId);
    assertFlexiblePage(0, 3, selectedId);
    cy.get('.minimum-tier select').select('A');
    assertRankingsRequest(0, 'A');
    assertFinderUrl('flexible', 0, selectedId, 'A');
    assertFlexiblePage(0, 2, selectedId);
    cy.contains('[aria-label="Result pages"] button', 'Next').click();
    assertRankingsRequest(1, 'A');
    assertFinderUrl('flexible', 1, selectedId, 'A');
    assertFlexiblePage(1, 2, selectedId);
    assertRetainedSelection();

    const restoredStates = [
      {
        direction: 'back',
        archetype: 'flexible',
        offset: 0,
        tier: 'A',
        total: 2,
      },
      {
        direction: 'back',
        archetype: 'flexible',
        offset: 0,
        tier: 'B',
        total: 3,
      },
      { direction: 'back', archetype: 'any', offset: 0, tier: 'B', total: 3 },
      {
        direction: 'back',
        archetype: 'flexible',
        offset: 2,
        tier: 'B',
        total: 3,
      },
      {
        direction: 'forward',
        archetype: 'any',
        offset: 0,
        tier: 'B',
        total: 3,
      },
      {
        direction: 'forward',
        archetype: 'flexible',
        offset: 0,
        tier: 'B',
        total: 3,
      },
      {
        direction: 'forward',
        archetype: 'flexible',
        offset: 0,
        tier: 'A',
        total: 2,
      },
      {
        direction: 'forward',
        archetype: 'flexible',
        offset: 1,
        tier: 'A',
        total: 2,
      },
    ] as const;
    for (const state of restoredStates) {
      cy.go(state.direction);
      if (state.archetype === 'any') assertAnyPage(selectedId);
      else {
        assertRankingsRequest(state.offset, state.tier);
        assertFinderUrl(state.archetype, state.offset, selectedId, state.tier);
        assertFlexiblePage(state.offset, state.total, selectedId);
      }
      assertRetainedSelection();
      cy.get('[data-testid="system-detail-modal"]').should('not.exist');
    }

    // Step 7: desktop/phone evidence for the selected card and retained point.
    cy.injectAxe();
    cy.checkA11y();
    captureFinder('flexible');
  });

  it('discovers, selects, spatially picks, inspects, resizes and remounts real systems', () => {
    let initialCanvas!: HTMLCanvasElement;
    let originatingExploreQuery = '';
    cy.viewport(1280, 800);
    cy.intercept('GET', '/api/archetypes/rankings*').as('idleRankings');
    visitExplore();
    cy.get('.results-panel').should('have.attr', 'aria-busy', 'false');
    cy.get('#results-title').should('contain.text', 'Systems');
    cy.contains(
      '.results-panel',
      'Search for a system above to see what is near it.',
    ).should('be.visible');
    cy.get('[data-system-result]').should('not.exist');
    cy.get('@search.all').should('have.length', 0);
    cy.get('@idleRankings.all').should('have.length', 0);
    keyboardChooseAnchor('Achenar');
    cy.get('#results-title').should('contain.text', 'Near Achenar');
    cy.get('[data-system-result="10477373803000"] [data-result-select]', {
      timeout: 15_000,
    })
      .should('be.focused')
      .and('have.attr', 'aria-pressed', 'true');
    cy.get('h1')
      .should('contain.text', 'Chart a promising system')
      .and('be.visible');
    cy.get('[data-system-result]', { timeout: 15_000 }).should(
      'have.length.greaterThan',
      0,
    );
    assertSpatialResultsReady();
    cy.get(`${mapWindowSelector} h2`).should(
      'have.text',
      'Achenar · Galaxy map',
    );
    cy.contains(`${mapWindowSelector} button`, /^Local$/).should('be.enabled');
    cy.contains(`${mapWindowSelector} a`, 'Inspect Achenar')
      .should('have.attr', 'href')
      .and('include', 'system=10477373803000');
    cy.contains(`${mapWindowSelector} a`, 'Plan here').should('be.visible');
    openMapRegions();
    cy.get('[data-region-resource-state="ready"]', { timeout: 20_000 })
      .scrollIntoView()
      .should('contain.text', '42/42 regions');
    closeMapRegions();
    openMapLayers();
    cy.get('[data-nebula-resource-state="ready"]', { timeout: 20_000 })
      .should('contain.text', '5,842 catalogue records')
      .and('contain.text', 'EDAstro / CMDR Orvidius')
      .and('contain.text', 'source CSV');
    closeMapLayers();
    openMapRegions();
    cy.get('[data-galaxy-region-option]').should('have.length', 42);
    cy.get('.spatial-canvas')
      .should('have.attr', 'data-rendered-layer-ids')
      .and('contain', 'galaxy-regions')
      .and('contain', 'galaxy-nebulae')
      .and('contain', 'finder-systems');
    cy.get('.spatial-canvas')
      .invoke('attr', 'data-system-target-count')
      .then((count) => expect(Number(count)).to.be.greaterThan(0));
    cy.contains('button', 'All 42 regions').click();
    closeMapRegions();
    cy.get(canvasSelector).scrollIntoView();
    cy.get('[data-map-label-kind="region"]', { timeout: 20_000 })
      .should('have.length', 42)
      .then(($labels) => {
        const names = $labels
          .toArray()
          .map((label) => label.textContent?.trim())
          .sort();
        expect(names).to.deep.equal([...GALAXY_REGION_NAMES].sort());
      });
    openMapRegions();
    cy.get('#explore-region-picker').select('18');
    cy.get('[data-selected-region-id="18"]').should(
      'contain.text',
      'Inner Orion Spur',
    );
    closeMapRegions();
    cy.get(canvasSelector).then(([canvas]) => {
      initialCanvas = canvas as HTMLCanvasElement;
      const initialRevision = Number(
        initialCanvas.getAttribute('data-resize-revision'),
      );
      cy.viewport(1180, 720);
      cy.get(canvasSelector).should(([resizedCanvas]) => {
        expect(resizedCanvas).to.equal(initialCanvas);
        expect(
          Number(resizedCanvas.getAttribute('data-resize-revision')),
        ).to.be.greaterThan(initialRevision);
      });
      cy.get(readySelector).should('be.visible');
    });
    cy.viewport(1280, 800);
    cy.get(canvasSelector).should(([restoredCanvas]) => {
      expect(restoredCanvas).to.equal(initialCanvas);
    });

    cy.get('[data-testid="selected-system-context"]')
      .should('contain.text', '10477373803000')
      .and('be.visible');

    let keyboardSelectedId!: string;
    cy.get('[data-system-result]')
      .eq(1)
      .then(($result) => {
        keyboardSelectedId = $result.attr('data-system-result') ?? '';
        expect(keyboardSelectedId).to.match(/^\d+$/);
        expect(keyboardSelectedId).not.to.equal('10477373803000');
        cy.get(
          `[data-system-result="${keyboardSelectedId}"] [data-result-select]`,
        )
          .focus()
          .should('be.focused')
          .type('{enter}');
        cy.get('[data-testid="selected-system-context"]').should(
          'contain.text',
          keyboardSelectedId,
        );
        cy.get(
          `[data-system-result="${keyboardSelectedId}"] [data-result-select]`,
        ).should('have.attr', 'aria-pressed', 'true');
      });
    cy.get('[data-system-result="10477373803000"] [data-result-select]')
      .focus()
      .type('{enter}')
      .should('have.attr', 'aria-pressed', 'true');
    cy.then(() => {
      cy.get(
        `[data-system-result="${keyboardSelectedId}"] [data-result-select]`,
      ).should('have.attr', 'aria-pressed', 'false');
    });

    cy.get('.spatial-canvas', { timeout: 20_000 }).should(($canvas) => {
      expect($canvas.attr('data-camera-transition-active')).to.equal('false');
      expect($canvas.attr('data-last-settled-focus-id64')).to.equal(
        '10477373803000',
      );
    });

    cy.get(canvasSelector).scrollIntoView();
    cy.get('[data-map-label-key="system:10477373803000"]', {
      timeout: 20_000,
    })
      .should('be.visible')
      .then(($label) => {
        const anchorX = Number($label.attr('data-map-label-anchor-x'));
        const anchorY = Number($label.attr('data-map-label-anchor-y'));
        expect(Number.isFinite(anchorX)).to.equal(true);
        expect(Number.isFinite(anchorY)).to.equal(true);
        expect(anchorX).to.be.greaterThan(0);
        expect(anchorY).to.be.greaterThan(0);
        cy.get(canvasSelector).click(anchorX, anchorY);
      });
    // The timeout must be on cy.get (its retry window covers the chained
    // assertions); passing it as a 4th arg to .should() is silently ignored and
    // falls back to the 4s default, which a firefox WebGL pick can exceed.
    cy.get('.selection-status', { timeout: 20_000 })
      .should('have.attr', 'data-last-picked-id64', '10477373803000')
      .and('contain.text', 'Spatial pick selected');
    cy.get('[data-system-result="10477373803000"] [data-result-select]').should(
      'have.attr',
      'aria-pressed',
      'true',
    );
    cy.get('[data-result-select][aria-pressed="true"]').should(
      'have.length',
      1,
    );
    cy.get('[data-selected-system-card="10477373803000"]')
      .scrollIntoView()
      .should('be.visible')
      .and('contain.text', 'Achenar');
    cy.get('[data-map-selection-trigger]').click();
    cy.get('[data-map-selection-panel]')
      .should('be.visible')
      .and('contain.text', 'Primary star');
    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.get('[data-map-selection-panel]').should('not.exist');
    cy.get('[data-map-selection-trigger]').should('be.focused');
    openMapLayers();
    cy.get('[data-density-toggle]')
      .should('have.attr', 'role', 'switch')
      .and('have.attr', 'aria-checked', 'true');
    cy.get('[data-commander-history-toggle]')
      .should('have.attr', 'role', 'switch')
      .and('have.attr', 'aria-checked', 'false')
      .click()
      .should('have.attr', 'aria-checked', 'true');
    cy.get('[data-nebula-toggle]')
      .should('have.attr', 'role', 'switch')
      .and('have.attr', 'aria-checked', 'true');

    cy.injectAxe();
    cy.checkA11y();
    cy.screenshot(`product/explore-layers-${Cypress.browser.name}-1280x800`, {
      capture: 'viewport',
      overwrite: true,
    });
    closeMapLayers();
    cy.document().then((document) => {
      const style = document.createElement('style');
      style.textContent =
        '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';
      document.head.append(style);
    });
    cy.screenshot(`product/explore-achenar-${Cypress.browser.name}-1280x800`, {
      capture: 'viewport',
      overwrite: true,
    });

    // F4 design section 6, "Detail overlay": Inspect carries the originating
    // Explore query in `from` so its return links restore the Finder state.
    waitForExploreSettled();
    cy.location('search', { timeout: 15_000 }).then((search) => {
      originatingExploreQuery = search.slice(1);
    });
    cy.contains(`${mapWindowSelector} a`, 'Inspect Achenar').click();
    waitForInspectSettled();
    cy.location('pathname', { timeout: 15_000 }).should('eq', '/inspect');
    cy.location('search', { timeout: 15_000 }).should((search) => {
      const params = new URLSearchParams(search);
      expect(params.get('system')).to.equal('10477373803000');
      expect(params.get('from')).to.equal(originatingExploreQuery);
    });
    cy.get('[data-system-id64="10477373803000"]', { timeout: 20_000 })
      .should('contain.text', 'Achenar')
      .and('contain.text', '10477373803000');
    assertInspectTab('Overview');
    inspectTab('Bodies').click();
    assertInspectTab('Bodies');
    // Renderer status sits below the canvas inside the scrollable Bodies tab.
    cy.get(`.inspect-page ${readySelector}`, { timeout: 20_000 }).should(
      'exist',
    );
    cy.contains('h2', 'Explore Achenar').should('be.visible');
    cy.contains(
      'Planet classes come from the published Ratings V4 derived catalogue',
    ).should('be.visible');
    cy.get('[aria-label="System camera controls"]').should('be.visible');
    cy.get('[aria-label="System bodies"] button')
      .should('have.length.greaterThan', 0)
      .first()
      .click()
      .should('have.attr', 'aria-pressed', 'true');
    cy.get('[data-selected-body-id]').scrollIntoView().should('be.visible');
    cy.injectAxe();
    cy.checkA11y();
    inspectTab('Stations').click();
    assertInspectTab('Stations');
    cy.checkA11y();
    inspectTab('Overview').click();
    assertInspectTab('Overview');
    cy.checkA11y();

    cy.viewport(820, 640);
    cy.contains('a', 'Back to Explore').click();
    waitForExploreSettled();
    cy.location('pathname', { timeout: 15_000 }).should('eq', '/explore');
    assertSpatialResultsReady();
    cy.get(canvasSelector).should(([remountedCanvas]) => {
      expect(remountedCanvas).not.to.equal(initialCanvas);
    });
    cy.window().then((window) => {
      expect((window as ProductWindow).__productRuntimeFailures).to.deep.equal(
        [],
      );
    });
  });

  it('opens real Inspect tabs and evidence with keyboard and reduced motion', () => {
    // Release-smoke candidate: one real system page, read only through the
    // generation-pinned V3 routes. The V2-backed /api/system route (absent
    // tables in V3 production) must never be called.
    cy.intercept('GET', '/api/system/*').as('legacySystem');
    cy.intercept('GET', '/api/v1/systems/10477373803000').as('v3System');
    cy.intercept('GET', '/api/v1/systems/10477373803000/bodies').as('v3Bodies');
    cy.intercept('GET', '/api/v1/systems/10477373803000/rings').as('v3Rings');
    cy.intercept('GET', '/api/v1/systems/10477373803000/stations').as(
      'v3Stations',
    );
    cy.intercept('GET', '/api/ratings/v4/systems/10477373803000').as(
      'v4Ratings',
    );
    cy.intercept(
      'GET',
      '/api/ratings/v4/systems/10477373803000/explanation',
    ).as('v4Explanation');
    cy.visit('/inspect?system=10477373803000', {
      onBeforeLoad: preferReducedMotion,
    });
    cy.window().should((window) => {
      expect(
        window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      ).to.equal(true);
    });
    for (const alias of [
      '@v3System',
      '@v3Bodies',
      '@v3Rings',
      '@v3Stations',
      '@v4Ratings',
    ] as const)
      cy.wait(alias).its('response.statusCode').should('eq', 200);
    cy.get('[data-system-id64="10477373803000"]', { timeout: 20_000 })
      .should('contain.text', 'Achenar')
      .and('contain.text', '10477373803000');
    cy.focused().should('have.prop', 'tagName', 'H1');
    assertInspectTab('Overview');
    cy.get('.inspect-page [role="tablist"]')
      .should('have.attr', 'aria-label')
      .and('not.be.empty');
    cy.contains('dt', 'Main star').next('dd').should('have.text', 'G2 V');
    cy.contains('dt', 'Bodies returned').next('dd').should('have.text', '41');
    cy.get('[data-unavailable-fields][data-availability="not-integrated"]')
      .should('have.length', 2)
      .each(($row) => expect($row).to.contain.text('Not integrated yet'));
    // Ratings V4: all seven economies from the published derived generation,
    // and an explanation fetched only when one is opened.
    cy.get('[data-rating-economy]').should('have.length', 7);
    cy.get('@v4Explanation.all').should('have.length', 0);
    // Capture the visible panel rather than cropping an element that can
    // extend past its scroll container, particularly on phone layouts.
    cy.get('.system-ratings').scrollIntoView();
    cy.screenshot(`product/inspect-achenar-ratings-${Cypress.browser.name}`, {
      capture: 'viewport',
      overwrite: true,
      disableTimersAndAnimations: true,
    });
    cy.get('[data-rating-economy] .rating-why')
      .first()
      .click()
      .should('have.attr', 'aria-expanded', 'true');
    cy.wait('@v4Explanation').its('response.statusCode').should('eq', 200);
    cy.get(evidenceDialog)
      .should('be.visible')
      .and('contain.text', 'Scorer')
      .and('contain.text', 'mechanics');
    cy.contains('.rating-explanation dt', 'Evidence known').should(
      'be.visible',
    );
    cy.injectAxe();
    cy.checkA11y();
    cy.screenshot(`product/inspect-evidence-${Cypress.browser.name}`, {
      capture: 'viewport',
      overwrite: true,
      disableTimersAndAnimations: true,
    });
    cy.get(evidenceDialog).contains('button', 'Close').focus();
    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.get(evidenceDialog).should('not.exist');
    cy.get('[data-rating-economy] .rating-why')
      .first()
      .should('be.focused')
      .and('have.attr', 'aria-expanded', 'false');
    // The shared explanation query supplies one economy at a time. Capture
    // its count before tab switches: reopening a stale query can refresh it.
    let explanationRequests = 0;
    cy.get('[data-rating-economy]')
      .eq(1)
      .then(($row) => {
        const economy = $row.find('th').text().trim();
        const potential = $row.find('.rating-potential').text().trim();
        cy.wrap($row).find('.rating-why').click();
        cy.get(evidenceDialog)
          .should('have.length', 1)
          .and('contain.text', `Why ${economy} scores ${potential}`);
      });
    cy.get(evidenceDialog).contains('button', 'Close').click();
    cy.get('[data-rating-economy] .rating-why').eq(1).should('be.focused');
    cy.get('@v4Explanation.all')
      .its('length')
      .then((count) => {
        explanationRequests = Number(count);
      });

    // Tabs support a single roving stop, wrapped arrows, Home and End. The
    // mounted scene and selected body survive each switch.
    inspectTab('Overview').focus();
    cy.press(Cypress.Keyboard.Keys.RIGHT);
    inspectTab('Bodies').should('be.focused');
    assertInspectTab('Bodies');
    cy.get(`.inspect-page ${readySelector}`, { timeout: 20_000 }).should(
      'exist',
    );
    let systemCanvas!: HTMLCanvasElement;
    let selectedBody!: string;
    let bodiesScrollTop = 0;
    cy.get<HTMLCanvasElement>(canvasSelector).then(([canvas]) => {
      systemCanvas = canvas;
    });
    cy.get('[aria-label="System bodies"] button')
      .first()
      .should('contain.text', 'Star · G2 V')
      .click()
      .should('have.attr', 'aria-pressed', 'true');
    cy.get('[data-selected-body-id]')
      .scrollIntoView()
      .should('be.visible')
      .invoke('attr', 'data-selected-body-id')
      .then((id) => {
        selectedBody = id ?? '';
        expect(selectedBody).not.to.equal('');
      });
    cy.get('.spatial-canvas').should(
      'have.attr',
      'data-camera-transition-active',
      'false',
    );
    cy.checkA11y();
    cy.screenshot(`product/inspect-bodies-${Cypress.browser.name}`, {
      capture: 'viewport',
      overwrite: true,
      disableTimersAndAnimations: true,
    });
    cy.get('.inspect-page [role="tabpanel"]:visible')
      .scrollTo('bottom')
      .invoke('scrollTop')
      .then((position) => {
        bodiesScrollTop = Number(position);
        expect(bodiesScrollTop).to.be.greaterThan(0);
      });
    inspectTab('Bodies').focus();
    cy.press(Cypress.Keyboard.Keys.END);
    inspectTab('Stations').should('be.focused');
    assertInspectTab('Stations');
    cy.checkA11y();
    cy.screenshot(`product/inspect-stations-${Cypress.browser.name}`, {
      capture: 'viewport',
      overwrite: true,
      disableTimersAndAnimations: true,
    });
    cy.press(Cypress.Keyboard.Keys.RIGHT);
    inspectTab('Overview').should('be.focused');
    assertInspectTab('Overview');
    cy.press(Cypress.Keyboard.Keys.LEFT);
    inspectTab('Stations').should('be.focused');
    assertInspectTab('Stations');
    cy.press(Cypress.Keyboard.Keys.HOME);
    inspectTab('Overview').should('be.focused');
    assertInspectTab('Overview');

    cy.contains('button', 'Sources & freshness').click();
    cy.get('.sources-dialog')
      .should('be.visible')
      .and('contain.text', 'Canonical catalogue')
      .and('contain.text', 'Ratings V4 potentials')
      .and('contain.text', 'Commander data');
    cy.checkA11y('.sources-dialog');
    // A non-modal tool may cover the tab strip; keyboard users can still
    // leave Overview without closing it or clicking through the pop-out.
    inspectTab('Overview').focus();
    cy.press(Cypress.Keyboard.Keys.RIGHT);
    assertInspectTab('Bodies');
    cy.get('.sources-dialog').should('not.be.visible');
    cy.then(() => {
      cy.get('.inspect-page [role="tabpanel"]:visible').should(($panel) => {
        expect($panel[0].scrollTop).to.equal(bodiesScrollTop);
      });
      cy.get('[data-selected-body-id]')
        .scrollIntoView()
        .should('be.visible')
        .and('have.attr', 'data-selected-body-id', selectedBody);
      cy.get(canvasSelector).should(([canvas]) => {
        expect(canvas).to.equal(systemCanvas);
      });
    });
    inspectTab('Bodies').focus();
    cy.press(Cypress.Keyboard.Keys.HOME);
    cy.get('.sources-dialog').should('be.visible');
    // The fixed tool is viewport-bounded; capture it without scrolling its
    // underlying Overview panel or cropping overflowing content.
    cy.screenshot(`product/inspect-sources-freshness-${Cypress.browser.name}`, {
      capture: 'viewport',
      overwrite: true,
    });
    cy.get('.sources-dialog').contains('button', 'Close').click();
    cy.contains('button', 'Sources & freshness').should('be.focused');
    for (const alias of [
      '@v3System.all',
      '@v3Bodies.all',
      '@v3Rings.all',
      '@v3Stations.all',
      '@v4Ratings.all',
    ])
      cy.get(alias).should('have.length', 1);
    cy.then(() => {
      cy.get('@v4Explanation.all').should('have.length', explanationRequests);
    });
    cy.viewport(375, 812);
    cy.get('.system-ratings').scrollIntoView();
    cy.screenshot(
      `product/inspect-achenar-ratings-${Cypress.browser.name}-375w`,
      {
        capture: 'viewport',
        overwrite: true,
        disableTimersAndAnimations: true,
      },
    );
    cy.get('[data-rating-economy] .rating-why').first().click();
    cy.get(evidenceDialog).should('be.visible');
    cy.checkA11y();
    cy.screenshot(`product/inspect-evidence-${Cypress.browser.name}-375w`, {
      capture: 'viewport',
      overwrite: true,
      disableTimersAndAnimations: true,
    });
    cy.get(evidenceDialog).contains('button', 'Close').click();
    cy.get('[data-rating-economy] .rating-why').first().should('be.focused');
    cy.contains('button', 'Sources & freshness').click();
    cy.checkA11y('.sources-dialog');
    cy.screenshot(
      `product/inspect-sources-freshness-${Cypress.browser.name}-375w`,
      { capture: 'viewport', overwrite: true },
    );
    cy.get('.sources-dialog').contains('button', 'Close').focus();
    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.get('.sources-dialog').should('not.exist');
    cy.contains('button', 'Sources & freshness').should('be.focused');
    inspectTab('Stations').click();
    assertInspectTab('Stations');
    cy.location('search').should((search) => {
      const params = new URLSearchParams(search);
      expect(params.get('system')).to.equal('10477373803000');
      expect(params.get('tab')).to.equal('stations');
    });
    cy.reload();
    waitForInspectSettled();
    assertInspectTab('Stations');
    cy.injectAxe();
    cy.checkA11y();
    cy.viewport(
      Cypress.config('viewportWidth'),
      Cypress.config('viewportHeight'),
    );

    cy.intercept('GET', '/api/v1/systems/4242').as('v3Missing');
    cy.visit('/inspect?system=4242');
    cy.wait('@v3Missing').its('response.statusCode').should('eq', 404);
    cy.contains('h1', 'System not found').should('be.visible');
    cy.get('@legacySystem.all').should('have.length', 0);
  });

  it('preserves an unsafe-sized id64 through API, selection, route, detail and reload', () => {
    const unsafeId = '9007199254740993';
    let originatingExploreQuery = '';
    visitExplore();
    keyboardChooseAnchor('V3 Lossless');

    cy.get('[data-testid="selected-system-context"]').should(
      'contain.text',
      unsafeId,
    );
    waitForExploreSettled();
    cy.location('search', { timeout: 15_000 }).then((search) => {
      originatingExploreQuery = search.slice(1);
    });
    cy.get(`[data-system-result="${unsafeId}"] .inspect-link`).click();
    cy.get(`[data-system-id64="${unsafeId}"]`, { timeout: 20_000 })
      .should('contain.text', 'V3 Lossless Reach')
      .and('contain.text', unsafeId);
    waitForInspectSettled();
    cy.location('search', { timeout: 15_000 }).should((search) => {
      const params = new URLSearchParams(search);
      expect(params.get('system')).to.equal(unsafeId);
      expect(params.get('from')).to.equal(originatingExploreQuery);
    });
    cy.reload();
    cy.get(`[data-system-id64="${unsafeId}"]`, { timeout: 10_000 })
      .should('contain.text', 'V3 Lossless Reach')
      .and('contain.text', unsafeId);
    cy.get('[data-testid="selected-system-context"]', {
      timeout: 10_000,
    }).should('contain.text', unsafeId);
  });
});
