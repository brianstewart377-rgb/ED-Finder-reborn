import { defineConfig } from 'cypress';
export default defineConfig({
  video: true,
  screenshotOnRunFailure: true,
  screenshotsFolder: 'cypress/artifacts/screenshots',
  videosFolder: 'cypress/artifacts/videos',
  e2e: {
    baseUrl: process.env.CYPRESS_BASE_URL ?? 'http://127.0.0.1:4174',
    // No retries: a flaky journey is quarantined and fixed, never retried
    // until green (CLAUDE.md).
    retries: { runMode: 0, openMode: 0 },
    // The journeys a visitor makes, on recorded real answers (see the file).
    specPattern: ['cypress/e2e/journeys.cy.ts'],
    supportFile: 'cypress/support/e2e.ts',
  },
});
