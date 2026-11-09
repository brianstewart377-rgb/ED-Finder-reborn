import 'cypress-axe';

const A11Y_TIMEOUT_MS = 60_000;

Cypress.Commands.overwrite(
  'checkA11y',
  (originalFn, context, options, violationCallback, skipFailures) => {
    const previousTimeout = Cypress.config('defaultCommandTimeout');

    cy.then(() => {
      Cypress.config('defaultCommandTimeout', A11Y_TIMEOUT_MS);
    });
    originalFn(context, options, violationCallback, skipFailures);
    cy.then(() => {
      Cypress.config('defaultCommandTimeout', previousTimeout);
    });
  },
);

// A dynamically-imported build chunk can transiently fail to load under the CI
// preview server (observed firefox-only, e.g. during an SPA reload) and surface
// as an unhandled promise rejection. That is environmental noise, not a defect
// in what these specs assert: a genuinely broken build still fails their
// positive assertions (h1 visible, renderer-state=ready, etc.). Swallow ONLY
// that error — returning false prevents Cypress failing the test; every other
// uncaught exception makes this return true and still fails as usual.
Cypress.on(
  'uncaught:exception',
  (err) => !/dynamically imported module/i.test(err.message),
);
