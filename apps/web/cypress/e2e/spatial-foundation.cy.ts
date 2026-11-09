type SpatialWindow = Window & { __spatialRuntimeFailures?: string[] };

const canvasSelector = 'canvas[data-spatial-canvas]';
const readySelector = '[role="status"][data-renderer-state="ready"]';

const assertMapWindowLayout = (width: number, height: number) => {
  // Cypress scrolls the whole document to reach controls inside the Finder
  // panel. A person does not, so bring the map back before measuring it.
  cy.get('[data-explore-map-window]').then(($window) => {
    $window[0].scrollIntoView({ block: 'nearest' });
  });
  cy.get('[data-explore-map-window]').should(($window) => {
    const window = $window[0];
    const bounds = window.getBoundingClientRect();
    const canvas = window
      .querySelector(canvasSelector)!
      .getBoundingClientRect();
    const controls = window
      .querySelector('[aria-label="Galaxy camera controls"]')!
      .getBoundingClientRect();
    expect(window.scrollHeight, 'window has no inner scrollbar').to.be.at.most(
      window.clientHeight,
    );
    const content = window.querySelector('.map-window-content')!;
    expect(
      content.scrollHeight,
      'map content has no inner scrollbar',
    ).to.be.at.most(content.clientHeight);
    // Browser scroll offsets round independently of fractional CSS borders.
    expect(bounds.top, 'window top fits viewport').to.be.at.least(-1);
    expect(bounds.bottom, 'window bottom fits viewport').to.be.at.most(
      height + 1,
    );
    expect(canvas.top, 'canvas starts inside window').to.be.at.least(
      bounds.top,
    );
    expect(canvas.bottom, 'canvas ends inside window').to.be.at.most(
      bounds.bottom,
    );
    expect(
      canvas.height,
      'canvas gets the main share of the workspace',
    ).to.be.at.least(width < 700 ? height * 0.6 : bounds.height * 0.75);
    // Like the in-game map, the controls float over the bottom of the map
    // instead of taking space below it.
    expect(controls.top, 'camera controls sit over the canvas').to.be.at.least(
      canvas.top,
    );
    expect(
      controls.bottom,
      'camera controls end inside the canvas',
    ).to.be.at.most(canvas.bottom);
    expect(
      controls.left,
      'camera controls start inside the canvas',
    ).to.be.at.least(canvas.left);
  });
};

const assertNoHorizontalOverflow = () => {
  cy.document().should((document) => {
    expect(
      document.documentElement.scrollWidth,
      'page has no horizontal overflow',
    ).to.be.at.most(document.documentElement.clientWidth);
  });
};

const visitFoundation = () => {
  cy.visit('/spatial-foundation', {
    onBeforeLoad(window) {
      const spatialWindow = window as SpatialWindow;
      spatialWindow.__spatialRuntimeFailures = [];
      window.addEventListener('error', () => {
        spatialWindow.__spatialRuntimeFailures?.push('error');
      });
      window.addEventListener('unhandledrejection', () => {
        spatialWindow.__spatialRuntimeFailures?.push('unhandledrejection');
      });
    },
  });
};

const assertRendererReady = () => {
  cy.get(readySelector, { timeout: 20_000 })
    .should('be.visible')
    .invoke('attr', 'data-renderer-backend')
    .should('match', /^(WEBGPU|WEBGL2)$/);
  cy.get(canvasSelector)
    .should('have.length', 1)
    .and('be.visible')
    .then(([canvas]) => {
      expect((canvas as HTMLCanvasElement).width).to.be.greaterThan(0);
      expect((canvas as HTMLCanvasElement).height).to.be.greaterThan(0);
    });
};

describe('non-product spatial runtime foundation', () => {
  it('stays ready through resize, navigation, and remount', () => {
    cy.viewport(1100, 720);
    visitFoundation();
    cy.contains('NON-PRODUCT diagnostic').should('be.visible');
    assertRendererReady();

    cy.get(canvasSelector).then(([firstCanvas]) => {
      const firstRevision = Number(
        firstCanvas.getAttribute('data-resize-revision'),
      );
      cy.viewport(820, 640);
      cy.get(canvasSelector).should(([resizedCanvas]) => {
        expect(
          Number(resizedCanvas.getAttribute('data-resize-revision')),
        ).to.be.greaterThan(firstRevision);
      });
      cy.get(readySelector).should('be.visible');

      cy.contains('a', 'ED-Finder V3').click();
      cy.location('pathname').should('eq', '/');
      cy.go('back');
      cy.location('pathname').should('eq', '/spatial-foundation');
      assertRendererReady();
      cy.get(canvasSelector).should(([remountedCanvas]) => {
        expect(remountedCanvas).not.to.equal(firstCanvas);
      });
    });

    cy.window().then((window) => {
      expect((window as SpatialWindow).__spatialRuntimeFailures).to.deep.equal(
        [],
      );
    });
  });

  it('captures deterministic diagnostic evidence without a product baseline', () => {
    cy.viewport(1280, 720);
    visitFoundation();
    assertRendererReady();
    cy.document().then((document) => {
      const style = document.createElement('style');
      style.textContent =
        '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}';
      document.head.append(style);
    });
    cy.screenshot(`spatial-foundation/${Cypress.browser.name}-1280x720`, {
      capture: 'viewport',
      overwrite: true,
    });
  });
});

// Normal Product API + real Babylon. The diagnostic route above remains a
// separate foundation check; these cases exercise Explore's full-window map
// beside the Finder panel (owner decision 2026-11-07).
for (const [width, height] of [
  [1280, 800],
  [390, 844],
]) {
  it(`keeps the full-window map and the Finder together at ${width}×${height} with reduced motion`, () => {
    const mapWindow = '[data-explore-map-window]';
    const phone = width <= 900;
    // Use the short seeded name that fits the renderer's phone label bounds.
    // Lossless oversized id64s retain their separate full Product journey.
    const selectedId = '10477373803000';
    const selectedResult = `[data-system-result="${selectedId}"]`;
    const opener = `${selectedResult} [data-result-map-open]`;
    let initialCanvas: HTMLCanvasElement;
    let resultScrollTop = 0;
    let query = '';
    let bearing = '';
    let pitch = '';
    const layoutRequestCounts = new Map<string, number>();
    const cameraTransitions: string[] = [];
    cy.viewport(width, height);
    cy.intercept('GET', '/api/archetypes/rankings*').as('windowRankings');
    cy.intercept('POST', '/api/local/search').as('windowSearch');
    cy.intercept('GET', '/api/map/heatmap*').as('windowDensity');
    cy.intercept('GET', '/api/map/systems*').as('windowCatalogue');
    cy.intercept('GET', '/assets/edastro-mapcharts-nebulae.json').as(
      'windowNebulae',
    );
    cy.intercept('GET', '/api/archetypes/system/*/explanation*').as(
      'windowExplanation',
    );
    cy.visit('/explore?archetype=flexible&page_size=3', {
      onBeforeLoad(window) {
        const matchMedia = window.matchMedia.bind(window);
        window.matchMedia = (query) => {
          const media = matchMedia(query);
          if (query === '(prefers-reduced-motion: reduce)')
            Object.defineProperty(media, 'matches', { value: true });
          return media;
        };
      },
    });
    cy.wait('@windowRankings', { requestTimeout: 20_000 })
      .its('response.statusCode')
      .should('eq', 200);
    cy.get('[data-system-result]').should('have.length', 3);
    // One canvas from the start; at phone width the Finder covers it.
    cy.get(canvasSelector).should('have.length', 1);
    cy.get('#finder-panel').should('be.visible');
    cy.get(mapWindow).should(phone ? 'not.be.visible' : 'be.visible');
    assertNoHorizontalOverflow();
    cy.window().should((window) => {
      expect(
        window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      ).to.equal(true);
    });
    // One real expanded explanation makes the result list scrollable even
    // with this intentionally small three-system Product fixture.
    cy.get(`${selectedResult} [data-archetype-explanation-toggle]`).click();
    cy.wait('@windowExplanation', { requestTimeout: 20_000 })
      .its('response.statusCode')
      .should('eq', 200);
    cy.get(`${selectedResult} [data-result-select]`).click();
    cy.get('.result-list')
      .scrollTo(0, 120)
      .invoke('scrollTop')
      .then((position) => {
        resultScrollTop = Number(position);
        expect(resultScrollTop).to.be.greaterThan(0);
      });
    cy.location('search').then((search) => {
      query = search;
    });
    cy.document().then((document) => {
      const observer = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
          if (mutation.target.nodeType === 1)
            cameraTransitions.push(
              (mutation.target as Element).getAttribute(
                'data-camera-transition-active',
              ) ?? '',
            );
        }
      });
      observer.observe(document.documentElement, {
        subtree: true,
        attributes: true,
        attributeFilter: ['data-camera-transition-active'],
      });
    });
    cy.get(canvasSelector).then(([canvas]) => {
      initialCanvas = canvas as HTMLCanvasElement;
    });
    // Focus scrolls the button into view inside the result list like a
    // keyboard user would; measure the list there, then click natively so
    // Cypress's own scroll-to-top cannot move the list before the click.
    cy.get(opener).focus();
    cy.get('.result-list')
      .invoke('scrollTop')
      .then((position) => {
        resultScrollTop = Number(position);
      });
    cy.get(opener).then(($button) => $button[0].click());
    if (phone) {
      // The Finder and the map take turns on a phone.
      cy.get('#finder-panel').should('not.be.visible');
      cy.get('[data-finder-panel-show]').should('be.focused');
    } else {
      // On a wide screen the map is already beside the results.
      cy.get('#finder-panel').should('be.visible');
      cy.get(opener).should('be.focused');
    }
    cy.get(mapWindow).should('be.visible');
    cy.get(`${mapWindow} h2`).should('have.text', 'Achenar · Galaxy map');
    cy.contains(`${mapWindow} a`, 'Inspect Achenar')
      .should('be.visible')
      .and('have.attr', 'href')
      .and('include', `system=${selectedId}`);
    cy.contains(`${mapWindow} a`, 'Plan here').should('be.visible');
    assertRendererReady();
    cy.get(canvasSelector).should(([canvas]) => {
      expect(canvas).to.equal(initialCanvas);
    });
    assertMapWindowLayout(width, height);
    assertNoHorizontalOverflow();
    // The map is part of the page: focus can leave it and nothing is inert.
    cy.get('a.brand').parents('[inert]').should('have.length', 0);
    cy.get('[inert]').should('not.exist');
    cy.get(`${mapWindow} .spatial-canvas`).should(
      'have.attr',
      'data-camera-transition-active',
      'false',
    );
    // Show on map uses the existing SET_CAMERA path. Verify the selected
    // system is actually centred, independent of FLY_TO completion markers.
    cy.get(`${mapWindow} [data-map-label-key="system:${selectedId}"]`, {
      timeout: 20_000,
    })
      .should('have.attr', 'data-map-label-selected', 'true')
      .should(($label) => {
        const canvas = $label[0]
          .closest('.spatial-canvas')!
          .querySelector('canvas')!;
        expect(Number($label.attr('data-map-label-anchor-x'))).to.be.closeTo(
          canvas.clientWidth / 2,
          2,
        );
        expect(Number($label.attr('data-map-label-anchor-y'))).to.be.closeTo(
          canvas.clientHeight / 2,
          2,
        );
      });
    cy.get('[data-catalogue-star-count]', { timeout: 20_000 }).should(
      'contain.text',
      'exact stars',
    );
    cy.screenshot(
      `product/explore-map-full-window-${Cypress.browser.name}-${width}x${height}`,
      {
        capture: 'viewport',
        overwrite: true,
        disableTimersAndAnimations: false,
      },
    );
    cy.contains(`${mapWindow} button`, /^Galaxy$/).click({
      scrollBehavior: false,
    });
    cy.get('[data-map-camera-preset="galaxy"]').should(
      'have.attr',
      'aria-pressed',
      'true',
    );
    cy.get('[data-camera-distance]').should(($distance) => {
      expect(Number($distance.attr('data-camera-distance'))).to.be.greaterThan(
        10_000,
      );
    });
    cy.get(`${mapWindow} .spatial-canvas`).should(
      'have.attr',
      'data-camera-transition-active',
      'false',
    );
    cy.contains(`${mapWindow} button`, /^Local$/).click({
      scrollBehavior: false,
    });
    cy.get('[data-map-camera-preset="local"]').should(
      'have.attr',
      'aria-pressed',
      'true',
    );
    cy.get('[data-camera-distance]').should(
      'have.attr',
      'data-camera-distance',
      '400',
    );
    cy.contains(`${mapWindow} button`, 'Centre on Achenar').should(
      'be.visible',
    );
    cy.get('[data-map-regions-trigger]').click({ scrollBehavior: false });
    cy.get('[data-map-regions-panel]')
      .should('be.visible')
      .and('have.attr', 'aria-modal', 'false');
    cy.get('[data-galaxy-region-option]').should('have.length', 42);
    cy.get('#explore-region-picker').select('18', { scrollBehavior: false });
    cy.get('[data-selected-region-id="18"]').should(
      'contain.text',
      'Inner Orion Spur',
    );
    cy.contains('[data-map-regions-panel] button', 'All 42 regions').should(
      'be.visible',
    );
    cy.contains('[data-map-regions-panel] button', 'View selected').should(
      'be.enabled',
    );
    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.get('[data-map-regions-panel]').should('not.exist');
    cy.get('[data-map-regions-trigger]').should('be.focused');
    cy.get(mapWindow).should('be.visible');
    cy.get('[data-map-sources-trigger]').click({ scrollBehavior: false });
    cy.get('[data-map-sources-panel]')
      .should('be.visible')
      .and('have.attr', 'aria-modal', 'false')
      .and('contain.text', 'exact catalogue coordinates')
      .and('contain.text', 'pinned in-game source');
    cy.injectAxe();
    cy.checkA11y(mapWindow);
    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.get('[data-map-sources-panel]').should('not.exist');
    cy.get('[data-map-sources-trigger]').should('be.focused');
    cy.get(mapWindow).should('be.visible');
    cy.get(`${mapWindow} [aria-label="Rotate right"]`).click({
      scrollBehavior: false,
    });
    cy.contains(`${mapWindow} button`, /^Top down$/).click({
      scrollBehavior: false,
    });
    cy.get(`${mapWindow} .spatial-canvas`).then(($canvas) => {
      bearing = $canvas.attr('data-camera-bearing') ?? '';
      pitch = $canvas.attr('data-camera-pitch') ?? '';
      expect(Number(pitch)).to.equal(Math.PI / 2);
    });
    cy.get('[data-map-layers-toggle]').click({ scrollBehavior: false });
    cy.get('[data-nebula-toggle]')
      .should('have.attr', 'aria-checked', 'true')
      .click({ scrollBehavior: false });
    cy.injectAxe();
    cy.checkA11y(mapWindow);
    // Layers owns the Escape; the map and its one runtime stay.
    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.get('[data-map-layers-dialog]').should('not.exist');
    cy.get('[data-map-layers-toggle]').should('be.focused');
    cy.press(Cypress.Keyboard.Keys.ESC);
    cy.get(mapWindow).should('be.visible');
    cy.get(canvasSelector).should(([canvas]) => {
      expect(canvas).to.equal(initialCanvas);
    });
    assertMapWindowLayout(width, height);
    assertNoHorizontalOverflow();
    if (!phone) {
      // Hide Finder gives the map the whole width with the same canvas.
      cy.get('[data-finder-panel-hide]').click();
      cy.get('#finder-panel').should('not.be.visible');
      cy.get('[data-finder-panel-show]').should('be.focused');
      cy.get(canvasSelector).then(([canvas]) => {
        expect(canvas).to.equal(initialCanvas);
        const revision = Number(canvas.getAttribute('data-resize-revision'));
        cy.viewport(width - 20, height - 40);
        cy.get(canvasSelector).should(([resized]) => {
          expect(resized).to.equal(initialCanvas);
          expect(
            Number(resized.getAttribute('data-resize-revision')),
          ).to.be.greaterThan(revision);
        });
      });
      cy.viewport(width, height);
      assertMapWindowLayout(width, height);
      assertNoHorizontalOverflow();
      cy.checkA11y(mapWindow);
      cy.screenshot(
        `product/explore-map-finder-hidden-${Cypress.browser.name}-${width}x${height}`,
        {
          capture: 'viewport',
          overwrite: true,
          disableTimersAndAnimations: false,
        },
      );
    }
    // Measure the settled map before returning to the Finder. At phone width
    // the map keeps its size off screen, so the turn starts no map requests.
    cy.wait(500);
    for (const alias of [
      '@windowDensity.all',
      '@windowCatalogue.all',
      '@windowNebulae.all',
    ]) {
      cy.get(alias).then((requests) => {
        layoutRequestCounts.set(alias, requests.length);
      });
    }
    cy.get('[data-finder-panel-show]').click({ scrollBehavior: false });
    cy.get('#finder-panel').should('be.visible');
    cy.get(canvasSelector).should(([canvas]) => {
      expect(canvas).to.equal(initialCanvas);
    });
    if (phone) {
      cy.wait(500);
      cy.then(() => {
        for (const [alias, count] of layoutRequestCounts)
          cy.get(alias).should('have.length', count);
      });
    }
    cy.get(phone ? opener : '[data-finder-panel-hide]').should('be.focused');
    cy.get('.result-list').should(($list) => {
      expect($list[0].scrollTop).to.equal(resultScrollTop);
    });
    cy.get(`${selectedResult} [data-result-select]`).should(
      'have.attr',
      'aria-pressed',
      'true',
    );
    cy.get(`${selectedResult} [data-archetype-explanation-toggle]`).should(
      'have.attr',
      'aria-expanded',
      'true',
    );
    cy.get('input[type="radio"][value="flexible"]').should('be.checked');
    cy.get('.page-size input').should('have.value', '3');
    cy.then(() => cy.location('search').should('eq', query));
    cy.get('@windowRankings.all').should('have.length', 1);
    cy.get('@windowSearch.all').should('have.length', 0);
    cy.then(() => {
      cy.get(`${mapWindow} .spatial-canvas`)
        .should('have.attr', 'data-camera-bearing', bearing)
        .and('have.attr', 'data-camera-pitch', pitch)
        .and('have.attr', 'data-camera-transition-active', 'false');
    });
    cy.then(() => {
      expect(cameraTransitions).not.to.include('true');
    });
    cy.checkA11y();
  });
}

it('keeps the map within the tablet page width and Finder turns start no new requests', () => {
  const requests = { search: 0, catalogue: 0, density: 0, nebulae: 0 };
  cy.intercept('POST', '/api/local/search', () => {
    requests.search++;
  });
  cy.intercept('GET', '/api/map/systems*', () => {
    requests.catalogue++;
  });
  cy.intercept('GET', '/api/map/heatmap*', () => {
    requests.density++;
  });
  cy.intercept('GET', '/assets/edastro-mapcharts-nebulae.json', () => {
    requests.nebulae++;
  });
  cy.viewport(768, 800);
  cy.visit('/explore?archetype=flexible&page_size=3');
  cy.get('[data-system-result]', { timeout: 20_000 }).should('have.length', 3);
  cy.get('[data-system-result="10477373803000"] [data-result-select]').click();
  assertNoHorizontalOverflow();
  cy.get('[data-explore-map-open]').click();
  cy.get('#finder-panel').should('not.be.visible');
  assertRendererReady();
  cy.get('[data-catalogue-star-count]', { timeout: 20_000 }).should(
    'contain.text',
    'exact stars',
  );
  cy.wait(500);
  cy.then(() => {
    const settled = { ...requests };
    assertNoHorizontalOverflow();
    // The map keeps its size off screen while the Finder is shown, so taking
    // turns never resizes the canvas or refetches its footprint.
    cy.get('[data-finder-panel-show]').click({ scrollBehavior: false });
    cy.get('#finder-panel').should('be.visible');
    assertNoHorizontalOverflow();
    cy.get('[data-explore-map-open]').click();
    cy.get('#finder-panel').should('not.be.visible');
    assertRendererReady();
    cy.wait(500);
    cy.then(() => expect(requests).to.deep.equal(settled));
  });
});
