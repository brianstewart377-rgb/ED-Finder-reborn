type ReviewLabConfig = Readonly<{
  reviewLabRun: boolean;
  reviewOutputPath: string;
  reviewScenariosJson: string;
}>;

type ReviewSummary = {
  summarySchemaVersion: number;
  reviewLabRun: true;
  selectedScenarioNames: string[];
  browserFlowKeys: string[];
  scenarios: Record<
    string,
    {
      status: 'passed' | 'failed';
      checks: Record<string, boolean>;
      diagnostics?: Record<string, string>;
    }
  >;
  apiResponses: Array<{
    method: string;
    path: string;
    status: number;
    expectedFailure?: boolean;
  }>;
  externalOrigins: string[];
  consoleEntries: Array<{ type: string; text: string }>;
  pageErrors: string[];
  fatalError: string | null;
};

const REVIEW_SYSTEM = { id64: '9100000000001', name: 'Review Wiring' };
const mapWindowSelector = '[data-explore-map-window]';
const readySelector = `${mapWindowSelector} [role="status"][data-renderer-state="ready"]`;
const openReviewMap = () => {
  cy.get('[data-explore-map-open]').click();
  cy.get(mapWindowSelector).should('be.visible');
  cy.get(`${mapWindowSelector} canvas[data-spatial-canvas]`).should(
    'have.length',
    1,
  );
};
const selectedStorageKey = 'ed-finder:selected-system-context';
const selectedMarkerSelector = `[data-map-label-key="system:${REVIEW_SYSTEM.id64}"][data-map-label-selected="true"]`;
let currentFlow = '';

const isExpectedReviewFailure = (status: number, tag: unknown) =>
  status === 503 &&
  ['api-failure', 'rankings-api-failure'].includes(String(tag ?? ''));

const clean = (value: unknown) =>
  String(value ?? '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 500);

const parseTrustedConfig = (raw: ReviewLabConfig) => {
  if (
    raw?.reviewLabRun !== true ||
    typeof raw.reviewOutputPath !== 'string' ||
    !raw.reviewOutputPath ||
    typeof raw.reviewScenariosJson !== 'string' ||
    !raw.reviewScenariosJson
  ) {
    throw new Error(
      'Review Lab browser verification requires the trusted Node-task handshake.',
    );
  }
  const plan = JSON.parse(raw.reviewScenariosJson) as {
    selectedScenarioNames?: unknown;
    browserFlowKeys?: unknown;
  };
  if (
    !Array.isArray(plan.selectedScenarioNames) ||
    !Array.isArray(plan.browserFlowKeys)
  ) {
    throw new Error('Review Lab scenario plan is malformed.');
  }
  return {
    outputPath: raw.reviewOutputPath,
    selectedScenarioNames: plan.selectedScenarioNames as string[],
    browserFlowKeys: plan.browserFlowKeys as string[],
  };
};

describe('isolated V3 Review Lab', () => {
  let outputPath = '';
  let summary: ReviewSummary;

  const setReviewMode = (
    mode:
      | 'normal'
      | 'api_failure'
      | 'empty_results'
      | 'rankings_api_failure'
      | 'rankings_empty_results',
  ) =>
    cy
      .request('POST', `/api/review/scenario/${mode}`)
      .its('status')
      .should('eq', 200);

  const collectExternalResourceOrigins = (window: Window) => {
    const currentOrigin = window.location.origin;
    for (const entry of window.performance.getEntriesByType('resource')) {
      try {
        const url = new URL(entry.name, window.location.href);
        if (
          (url.protocol === 'http:' || url.protocol === 'https:') &&
          url.origin !== currentOrigin
        ) {
          summary.externalOrigins.push(url.origin);
        }
      } catch {
        // Ignore non-URL performance entries; containment only concerns origins.
      }
    }
  };

  before(() => {
    for (const tag of ['api-failure', 'rankings-api-failure']) {
      expect(isExpectedReviewFailure(503, tag), tag).to.equal(true);
      expect(isExpectedReviewFailure(500, tag), tag).to.equal(false);
    }
    expect(isExpectedReviewFailure(503, undefined)).to.equal(false);
    expect(isExpectedReviewFailure(503, 'unknown-failure')).to.equal(false);
    cy.task('getReviewLabConfig').then((raw) => {
      const config = parseTrustedConfig(raw as ReviewLabConfig);
      outputPath = config.outputPath;
      summary = {
        summarySchemaVersion: 2,
        reviewLabRun: true,
        selectedScenarioNames: config.selectedScenarioNames,
        browserFlowKeys: config.browserFlowKeys,
        scenarios: {},
        apiResponses: [],
        externalOrigins: [],
        consoleEntries: [],
        pageErrors: [],
        fatalError: null,
      };
    });
    cy.intercept({ url: '**/api/**', middleware: true }, (request) => {
      request.on('response', (response) => {
        const url = new URL(request.url);
        summary.apiResponses.push({
          method: request.method,
          path: `${url.pathname}${url.search}`,
          status: response.statusCode,
          expectedFailure: isExpectedReviewFailure(
            response.statusCode,
            response.headers['x-edfinder-review-failure'],
          ),
        });
      });
    });
  });

  after(() => {
    cy.task('writeReviewLabSummary', { outputPath, summary });
  });

  it('executes only selected Review Lab synthetic edge and failure scenarios', () => {
    Cypress.once('fail', (error) => {
      summary.fatalError = clean(error.message);
      if (currentFlow) {
        summary.scenarios[currentFlow] = {
          status: 'failed',
          checks: summary.scenarios[currentFlow]?.checks ?? {},
        };
      }
      throw error;
    });

    const instrumentWindow = (window: Window) => {
      try {
        Object.defineProperty(window.document, 'visibilityState', {
          configurable: true,
          get: () => 'visible',
        });
      } catch {
        // Cypress owns the page lifecycle; retry pacing is best-effort here.
      }
      window.addEventListener('error', (event) =>
        summary.pageErrors.push(clean(event.error?.stack || event.message)),
      );
      window.addEventListener('unhandledrejection', (event) =>
        summary.pageErrors.push(clean(event.reason)),
      );
      for (const type of ['error', 'warn'] as const) {
        const browserConsole = (window as Window & { console: Console })
          .console;
        const original = browserConsole[type];
        browserConsole[type] = (...args: unknown[]) => {
          summary.consoleEntries.push({ type, text: clean(args.join(' ')) });
          original.apply(browserConsole, args);
        };
      }
    };

    const markPassed = (
      flow: string,
      checks: Record<string, boolean>,
      diagnostics?: Record<string, string>,
    ) => {
      summary.scenarios[flow] = { status: 'passed', checks, diagnostics };
    };

    const flows = summary.browserFlowKeys;

    const assertSelectionPreserved = () => {
      cy.location('search').should((search) => {
        expect(new URLSearchParams(search).getAll('selected')).to.deep.equal([
          REVIEW_SYSTEM.id64,
        ]);
      });
      cy.window()
        .its('localStorage')
        .invoke('getItem', selectedStorageKey)
        .should('eq', REVIEW_SYSTEM.id64);
      cy.get(selectedMarkerSelector, { timeout: 20_000 }).should('be.visible');
    };

    let anchorAutocompleteCount = 0;
    const chooseReviewAnchor = () => {
      const autocompleteAlias = `anchorAutocomplete${++anchorAutocompleteCount}`;
      // Typing sends a request per debounced prefix and cancels the earlier
      // ones; wait for the complete name's request, not the first prefix.
      cy.intercept({
        method: 'GET',
        pathname: '/api/local/autocomplete',
        query: { q: REVIEW_SYSTEM.name },
      }).as(autocompleteAlias);
      cy.get('#system-search').clear().type(REVIEW_SYSTEM.name);
      cy.wait(`@${autocompleteAlias}`)
        .its('response.statusCode')
        .should('eq', 200);
      cy.contains('[role="option"]', REVIEW_SYSTEM.name).click();
    };

    // URL-owned anchors and selection-only links resolve through V3 system
    // routes the Review Lab API does not serve. Remount through the supported
    // autocomplete path instead of reloading the shared search URL.
    const remountExplore = () => {
      cy.visit('/explore', { onBeforeLoad: instrumentWindow });
      chooseReviewAnchor();
      openReviewMap();
    };

    let selectionSearchCount = 0;
    const establishSelectedSystem = () => {
      // Earlier intercepts also observe later flows, so each call needs a fresh alias.
      const selectionSearchAlias = `selectionSearch${++selectionSearchCount}`;
      setReviewMode('normal');
      cy.intercept('POST', '/api/local/search').as(selectionSearchAlias);
      cy.visit('/explore', { onBeforeLoad: instrumentWindow });
      chooseReviewAnchor();
      cy.wait(`@${selectionSearchAlias}`)
        .its('response.statusCode')
        .should('eq', 200);
      openReviewMap();
      cy.get(readySelector, { timeout: 20_000 }).should('be.visible');
      assertSelectionPreserved();
    };

    if (flows.includes('syntheticWiring')) {
      cy.then(() => {
        currentFlow = 'syntheticWiring';
      });
      setReviewMode('normal');
      cy.intercept('POST', '/api/local/search').as('wiringSearch');
      cy.visit('/explore', { onBeforeLoad: instrumentWindow });
      chooseReviewAnchor();
      cy.wait('@wiringSearch').its('response.statusCode').should('eq', 200);
      cy.get(`[data-system-result="${REVIEW_SYSTEM.id64}"]`).should(
        'contain.text',
        REVIEW_SYSTEM.name,
      );
      openReviewMap();
      cy.get(readySelector, { timeout: 20_000 })
        .should('be.visible')
        .then(() =>
          markPassed('syntheticWiring', {
            syntheticSystemVisible: true,
            babylonReady: true,
          }),
        );
      cy.window().then(collectExternalResourceOrigins);
    }

    if (flows.includes('apiFailure')) {
      cy.then(() => {
        currentFlow = 'apiFailure';
      });
      establishSelectedSystem();
      setReviewMode('api_failure');
      cy.intercept('POST', '/api/local/search').as('failedSearch');
      // A changed query triggers a real search while retaining the selected
      // point already established in this mounted Explore workspace.
      cy.contains('label', 'Results per page')
        .find('input')
        .clear()
        .type('1')
        .blur();
      cy.wait('@failedSearch').then(({ response }) => {
        expect(response?.statusCode).to.equal(503);
        expect(response?.headers['x-edfinder-review-failure']).to.equal(
          'api-failure',
        );
      });
      cy.contains('[role="alert"]', 'Discovery results could not be loaded.', {
        timeout: 20_000,
      }).should('be.visible');
      assertSelectionPreserved();
      cy.then(() =>
        markPassed('apiFailure', {
          failureModeActivated: true,
          errorRendered: true,
          selectionContextPreserved: true,
          selectedMarkerPreserved: true,
        }),
      );
      cy.window().then(collectExternalResourceOrigins);
      setReviewMode('normal');
    }

    if (flows.includes('rankingsApiFailure')) {
      cy.then(() => {
        currentFlow = 'rankingsApiFailure';
      });
      establishSelectedSystem();
      setReviewMode('rankings_api_failure');
      cy.intercept('GET', '/api/archetypes/rankings*').as('failedRankings');
      cy.get('input[type="radio"][value="flexible"]').check();
      cy.location('search').should((search) => {
        expect(new URLSearchParams(search).get('archetype')).to.equal(
          'flexible',
        );
      });
      cy.wait('@failedRankings').then(({ response }) => {
        expect(response?.statusCode).to.equal(503);
        expect(response?.headers['x-edfinder-review-failure']).to.equal(
          'rankings-api-failure',
        );
      });
      cy.contains(
        '.results-panel [role="alert"]',
        'Ranking temporarily unavailable',
        { timeout: 20_000 },
      ).should('be.visible');
      assertSelectionPreserved();
      cy.then(() =>
        markPassed('rankingsApiFailure', {
          failureModeActivated: true,
          errorRendered: true,
          selectionContextPreserved: true,
          selectedMarkerPreserved: true,
        }),
      );
      cy.window().then(collectExternalResourceOrigins);
      setReviewMode('normal');
    }

    if (flows.includes('rankingsEmptyResults')) {
      cy.then(() => {
        currentFlow = 'rankingsEmptyResults';
      });
      setReviewMode('rankings_empty_results');
      cy.intercept('GET', '/api/archetypes/rankings*').as('emptyRankings');
      cy.visit('/explore?archetype=flexible&page_size=1', {
        onBeforeLoad: instrumentWindow,
      });
      cy.wait('@emptyRankings').then(({ response }) => {
        expect(response?.statusCode).to.equal(200);
        expect(response?.body.results).to.deep.equal([]);
        expect(response?.body.total).to.equal(0);
        expect(response?.body.is_truncated).to.equal(false);
      });
      cy.contains(
        '.results-panel',
        'No systems meet minimum tier B for Flexible Multi-Role Colony.',
      ).should('be.visible');
      openReviewMap();
      cy.get(readySelector, { timeout: 20_000 }).should('be.visible');
      cy.get('.spatial-canvas').should(
        'have.attr',
        'data-scene-target-count',
        '0',
      );
      cy.intercept('POST', '/api/local/search').as('restoredAnySearch');
      cy.contains('button', 'Choose Any').click();
      cy.wait('@restoredAnySearch').then(({ response }) => {
        expect(response?.statusCode).to.equal(200);
        expect(response?.body.source).to.match(/^v3:/);
        expect(response?.body.results.length).to.be.greaterThan(0);
      });
      cy.get(`[data-system-result="${REVIEW_SYSTEM.id64}"]`).should(
        'contain.text',
        REVIEW_SYSTEM.name,
      );
      cy.then(() =>
        markPassed('rankingsEmptyResults', {
          emptyModeActivated: true,
          emptyRendered: true,
          zeroTargetScene: true,
          babylonReady: true,
          anySearchRestored: true,
        }),
      );
      cy.window().then(collectExternalResourceOrigins);
      setReviewMode('normal');
    }

    if (flows.includes('emptyResults')) {
      cy.then(() => {
        currentFlow = 'emptyResults';
      });
      setReviewMode('empty_results');
      cy.intercept('POST', '/api/local/search').as('emptySearch');
      // Explicit Finder state requests results without selecting an anchor:
      // the synthetic empty response must still prove a zero-target scene.
      cy.visit('/explore?page_size=1', { onBeforeLoad: instrumentWindow });
      cy.wait('@emptySearch').its('response.statusCode').should('eq', 200);
      cy.contains('No systems match this discovery area.').should('be.visible');
      openReviewMap();
      cy.get(readySelector, { timeout: 20_000 }).should('be.visible');
      cy.get('.spatial-canvas')
        .should('have.attr', 'data-scene-target-count', '0')
        .then(() =>
          markPassed('emptyResults', {
            emptyModeActivated: true,
            emptyRendered: true,
            zeroTargetScene: true,
            babylonReady: true,
          }),
        );
      cy.window().then(collectExternalResourceOrigins);
      setReviewMode('normal');
    }

    if (flows.includes('rendererRecovery')) {
      cy.then(() => {
        currentFlow = 'rendererRecovery';
      });
      setReviewMode('normal');
      cy.intercept('POST', '/api/local/search').as('rendererSearch');
      cy.visit('/explore', { onBeforeLoad: instrumentWindow });
      chooseReviewAnchor();
      cy.wait('@rendererSearch').its('response.statusCode').should('eq', 200);
      cy.get(`[data-system-result="${REVIEW_SYSTEM.id64}"]`).should(
        'contain.text',
        REVIEW_SYSTEM.name,
      );
      openReviewMap();
      cy.get(readySelector, { timeout: 20_000 }).should('be.visible');
      cy.get('.spatial-canvas').should(($scene) => {
        expect(
          $scene.attr('data-scene-target-count'),
          'normal Review Wiring scene target count',
        ).to.match(/^[1-9]\d*$/);
      });
      cy.get(readySelector, { timeout: 20_000 })
        .should('be.visible')
        .invoke('attr', 'data-renderer-backend')
        .then((backend) => {
          cy.get<HTMLCanvasElement>('canvas[data-spatial-canvas]').then(
            ($canvas) => {
              const canvas = $canvas[0];
              let recoveryMode = 'neutral-lifecycle-fallback';
              const context =
                backend === 'WEBGL2' ? canvas.getContext('webgl2') : null;
              const extension = context?.getExtension('WEBGL_lose_context');
              if (extension) {
                const activeContext = context as WebGL2RenderingContext;
                const contextLost = new Cypress.Promise<void>((resolve) => {
                  canvas.addEventListener('webglcontextlost', () => resolve(), {
                    once: true,
                  });
                });
                const contextRestored = new Cypress.Promise<boolean>(
                  (resolve) => {
                    canvas.addEventListener(
                      'webglcontextrestored',
                      () => resolve(true),
                      { once: true },
                    );
                    window.setTimeout(() => resolve(false), 2_000);
                  },
                );
                extension.loseContext();
                cy.wrap(contextLost)
                  .then(() => {
                    expect(activeContext.isContextLost()).to.equal(true);
                  })
                  .then(() => {
                    extension.restoreContext();
                    return cy.wrap(contextRestored);
                  })
                  .then((restored) => {
                    if (!restored) {
                      recoveryMode = 'webgl-context-loss-remount-fallback';
                      remountExplore();
                      return;
                    }
                    recoveryMode = 'webgl-context-loss-and-restore';
                    expect(activeContext.isContextLost()).to.equal(false);
                  });
              } else {
                remountExplore();
              }
              cy.get(readySelector, { timeout: 20_000 }).should('be.visible');
              cy.then(() =>
                markPassed(
                  'rendererRecovery',
                  {
                    babylonReady: true,
                    rendererLifecycleExercised: true,
                    rendererRemainedUsable: true,
                    noUncaughtError: summary.pageErrors.length === 0,
                  },
                  { recoveryMode },
                ),
              );
            },
          );
        });
      cy.window().then(collectExternalResourceOrigins);
    }

    cy.then(() => {
      currentFlow = '';
      expect(
        [...new Set(summary.externalOrigins)],
        'Review Lab external resource origins',
      ).to.deep.equal([]);
    });
  });
});
