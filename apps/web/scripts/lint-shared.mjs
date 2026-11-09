import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { ESLint } from 'eslint';

const repoRoot = fileURLToPath(new URL('../../..', import.meta.url));
const configFile = fileURLToPath(
  new URL('../eslint.shared.config.js', import.meta.url),
);

export async function lintSharedPackages() {
  // An explicit config uses cwd as its flat-config base. Keep it at the repo
  // root so packages/** matches even when pnpm starts this from apps/web.
  const eslint = new ESLint({ cwd: repoRoot, overrideConfigFile: configFile });
  const results = await eslint.lintFiles([
    'packages/api-client/src',
    'packages/planner-core/src',
  ]);
  const formatter = await eslint.loadFormatter('stylish');
  return { results, output: await formatter.format(results) };
}

if (
  process.argv[1] &&
  pathToFileURL(process.argv[1]).href === import.meta.url
) {
  try {
    const { results, output } = await lintSharedPackages();
    process.stdout.write(output);
    process.stdout.write(`Linted ${results.length} shared files.\n`);
    process.exitCode = results.some((result) => result.errorCount > 0) ? 1 : 0;
  } catch (error) {
    console.error(error);
    process.exitCode = 2;
  }
}
