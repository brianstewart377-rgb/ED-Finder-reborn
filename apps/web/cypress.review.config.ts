import { randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { defineConfig } from 'cypress';

const REVIEW_ROOT = '/tmp/edfinder-local-review';
const SUMMARY_SCHEMA_VERSION = 2;

function isWithinReviewRoot(candidate: string): boolean {
  const relative = path.relative(REVIEW_ROOT, candidate);
  return (
    relative !== '' &&
    relative !== '..' &&
    !relative.startsWith(`..${path.sep}`) &&
    !path.isAbsolute(relative)
  );
}

export default defineConfig({
  retries: 0,
  video: true,
  screenshotOnRunFailure: true,
  trashAssetsBeforeRuns: true,
  viewportWidth: 1280,
  viewportHeight: 800,
  defaultCommandTimeout: 10_000,
  requestTimeout: 10_000,
  responseTimeout: 15_000,
  pageLoadTimeout: 30_000,
  numTestsKeptInMemory: 0,
  chromeWebSecurity: true,
  screenshotsFolder: 'cypress/artifacts/review-lab/screenshots',
  videosFolder: 'cypress/artifacts/review-lab/videos',
  downloadsFolder: 'cypress/artifacts/review-lab/downloads',
  e2e: {
    baseUrl: process.env.CYPRESS_BASE_URL ?? 'http://127.0.0.1:4173',
    specPattern: 'cypress/e2e/review-lab.cy.ts',
    supportFile: 'cypress/support/e2e.ts',
    testIsolation: true,
    setupNodeEvents(on, config) {
      const marker = process.env.EDFINDER_REVIEW_LAB_RUN ?? '';
      const outputPath = process.env.EDFINDER_REVIEW_OUTPUT_PATH ?? '';
      const scenariosJson = process.env.EDFINDER_REVIEW_SCENARIOS_JSON ?? '';
      on('task', {
        getReviewLabConfig() {
          return {
            reviewLabRun: marker === '1',
            reviewOutputPath: outputPath,
            reviewScenariosJson: scenariosJson,
          };
        },
        async writeReviewLabSummary(input: {
          outputPath: string;
          summary: Record<string, unknown>;
        }) {
          const candidate = input.outputPath;
          const trustedPath =
            marker === '1' &&
            candidate === outputPath &&
            path.isAbsolute(candidate) &&
            path.resolve(candidate) === candidate &&
            isWithinReviewRoot(candidate);
          const trustedSummary =
            input.summary?.summarySchemaVersion === SUMMARY_SCHEMA_VERSION &&
            input.summary?.reviewLabRun === true;
          if (!trustedPath || !trustedSummary) {
            throw new Error('Refusing an unverified Review Lab summary.');
          }
          const outputDirectory = path.dirname(candidate);
          await fs.mkdir(outputDirectory, { recursive: true });
          const [realRoot, realDirectory] = await Promise.all([
            fs.realpath(REVIEW_ROOT),
            fs.realpath(outputDirectory),
          ]);
          if (
            realRoot !== path.resolve(REVIEW_ROOT) ||
            !isWithinReviewRoot(realDirectory)
          ) {
            throw new Error('Refusing a Review Lab summary outside its root.');
          }
          const temporary = path.join(
            outputDirectory,
            `.${path.basename(candidate)}.${randomUUID()}.tmp`,
          );
          try {
            await fs.writeFile(
              temporary,
              `${JSON.stringify(input.summary, null, 2)}\n`,
              { encoding: 'utf8', flag: 'wx' },
            );
            await fs.rename(temporary, candidate);
          } catch (error) {
            await fs.rm(temporary, { force: true });
            throw error;
          }
          return null;
        },
      });
      return config;
    },
  },
});
