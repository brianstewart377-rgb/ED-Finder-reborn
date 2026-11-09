// @vitest-environment node
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

import type { ESLint } from 'eslint';
import { describe, expect, it } from 'vitest';

import { lintSharedPackages } from './lint-shared.mjs';

const repoRoot = fileURLToPath(new URL('../../..', import.meta.url));
const webRoot = path.join(repoRoot, 'apps/web');
const launcher = path.join(webRoot, 'scripts/lint-shared.mjs');
const require = createRequire(import.meta.url);
const eslintCli = path.join(
  path.dirname(require.resolve('eslint/package.json')),
  'bin/eslint.js',
);

function runLauncher(cwd: string) {
  const result = spawnSync(process.execPath, [launcher], {
    cwd,
    encoding: 'utf8',
    timeout: 30_000,
  });
  expect(result.error).toBeUndefined();
  expect(result.signal).toBeNull();
  return result;
}

describe('shared lint launcher', () => {
  it('lints exactly the same files as the original root-based CLI command', async () => {
    // Invoke ESLint's JS entry point so the baseline itself also runs on Windows.
    const baseline = spawnSync(
      process.execPath,
      [
        eslintCli,
        '--config',
        'apps/web/eslint.shared.config.js',
        'packages/api-client/src',
        'packages/planner-core/src',
        '--format',
        'json',
      ],
      { cwd: repoRoot, encoding: 'utf8', timeout: 30_000 },
    );
    expect(baseline.error).toBeUndefined();
    expect(baseline.status, baseline.stderr).toBe(0);
    const expected = JSON.parse(baseline.stdout) as ESLint.LintResult[];
    const { results } = await lintSharedPackages();
    const paths = results.map((result) => result.filePath).sort();

    expect(paths.length).toBeGreaterThan(0);
    expect(paths).toEqual(expected.map((result) => result.filePath).sort());
    expect(paths).toContain(
      path.join(repoRoot, 'packages/api-client/src/core.ts'),
    );
    expect(paths).toContain(
      path.join(repoRoot, 'packages/planner-core/src/index.ts'),
    );
    expect(paths).not.toContain(
      path.join(repoRoot, 'packages/api-client/src/generated/api.gen.ts'),
    );

    const command = runLauncher(webRoot);
    expect(command.status, command.stdout + command.stderr).toBe(0);
    expect(command.stdout).toContain(`Linted ${expected.length} shared files.`);
  }, 30_000);

  it('reports and fails on a temporary planner lint error from an unrelated cwd', async () => {
    const fixtureDir = await mkdtemp(
      path.join(repoRoot, 'packages/planner-core/src/shared-lint-fixture-'),
    );
    let unrelatedCwd: string | undefined;
    try {
      unrelatedCwd = await mkdtemp(path.join(tmpdir(), 'shared lint cwd '));
      const fixture = path.join(fixtureDir, 'injected.ts');
      // Warnings retain the original CLI's successful exit status.
      await writeFile(fixture, 'const unusedSharedLintFixture = 1;\n');
      const warning = runLauncher(unrelatedCwd);
      expect(warning.status, warning.stdout + warning.stderr).toBe(0);
      expect(warning.stdout).toContain('@typescript-eslint/no-unused-vars');

      await writeFile(fixture, 'export const lintError = new Array(1, 2);\n');
      const error = runLauncher(unrelatedCwd);
      expect(error.status, error.stdout + error.stderr).toBe(1);
      expect(error.stdout).toContain(fixture);
      expect(error.stdout).toContain('@typescript-eslint/no-array-constructor');
      expect(error.stdout).toMatch(/Linted \d+ shared files\./);
    } finally {
      await rm(fixtureDir, { recursive: true, force: true });
      if (unrelatedCwd) {
        await rm(unrelatedCwd, { recursive: true, force: true });
      }
    }
  }, 30_000);
});
