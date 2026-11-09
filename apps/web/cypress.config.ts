import { defineConfig } from 'cypress';
export default defineConfig({
  video: true,
  screenshotOnRunFailure: true,
  screenshotsFolder: 'cypress/artifacts/screenshots',
  videosFolder: 'cypress/artifacts/videos',
  e2e: {
    baseUrl: process.env.CYPRESS_BASE_URL ?? 'http://127.0.0.1:4174',
    // CI-only retries. The product spatial-pick (Babylon/WebGL) is inherently
    // timing-sensitive under headless firefox and resolves intermittently even
    // with a 20s window; retries absorb that environmental GPU-pick flake. A
    // genuine regression fails all attempts, so this masks flakes, not bugs.
    // Interactive (openMode) stays at 0 so local runs surface failures fast.
    retries: { runMode: 2, openMode: 0 },
    // Product E2E is deliberately enumerated. Review Lab has its own Cypress
    // config and trusted handshake; adding a lab spec must never make the
    // normal seeded product lane collect it through a broad glob.
    specPattern: [
      'cypress/e2e/foundation.cy.ts',
      'cypress/e2e/spatial-foundation.cy.ts',
      'cypress/e2e/product-journey.cy.ts',
      'cypress/e2e/account-journal.cy.ts',
      'cypress/e2e/plan.cy.ts',
    ],
    supportFile: 'cypress/support/e2e.ts',
  },
});
