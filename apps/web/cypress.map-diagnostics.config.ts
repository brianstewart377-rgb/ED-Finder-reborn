import { defineConfig } from 'cypress';

// Local renderer diagnostics only. The deterministic map fixture and its
// screenshots are neither Product E2E acceptance nor guarded Review Lab
// authority. This config does not start or collect either browser lane.
export default defineConfig({
  retries: 0,
  video: true,
  screenshotOnRunFailure: true,
  screenshotsFolder: 'cypress/artifacts/map-diagnostics/screenshots',
  videosFolder: 'cypress/artifacts/map-diagnostics/videos',
  downloadsFolder: 'cypress/artifacts/map-diagnostics/downloads',
  e2e: {
    baseUrl: process.env.CYPRESS_BASE_URL ?? 'http://127.0.0.1:4174',
    specPattern: 'cypress/e2e/map-review-lab.cy.ts',
    supportFile: 'cypress/support/e2e.ts',
    setupNodeEvents(on) {
      on('before:browser:launch', (browser, launchOptions) => {
        if (browser.name === 'chrome' && browser.isHeadless) {
          // cy.viewport sizes the AUT, not Chrome's screenshot surface. Leave
          // enough room for the largest diagnostic viewport and runner chrome.
          launchOptions.args.push('--window-size=1920,1200');
          launchOptions.args.push('--force-device-scale-factor=1');
        }
        return launchOptions;
      });
    },
  },
});
