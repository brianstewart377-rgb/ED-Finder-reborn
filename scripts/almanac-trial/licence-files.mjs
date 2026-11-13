// Almanac's map is used under the PolyForm Noncommercial License 1.0.0. That
// licence asks that anyone who gets a copy of the software also gets the
// licence terms and every line of it that begins "Required Notice:". The
// built page is such a copy, so the build puts the licence files beside it
// and refuses to finish unless the page itself shows each required line.
import { copyFile, mkdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';

/** The licence and notice files of what the page is built from, and their names in the built page. */
export const LICENCE_FILES = [
  ['@elite-dangerous-almanac/galaxy-map/LICENSE.md', 'galaxy-map-LICENSE.md'],
  ['@elite-dangerous-almanac/galaxy-map/THIRD_PARTY_NOTICES.md', 'galaxy-map-THIRD_PARTY_NOTICES.md'],
  ['@elite-dangerous-almanac/core/LICENSE', 'almanac-core-LICENSE.txt'],
  ['@elite-dangerous-almanac/core/THIRD_PARTY_NOTICES.md', 'almanac-core-THIRD_PARTY_NOTICES.md'],
  ['gl-matrix/LICENSE.md', 'gl-matrix-LICENSE.md'],
];

/**
 * The lines of a licence that begin "Required Notice:". The licence's own
 * worked example is a quoted line ("> Required Notice: ...") and is not one.
 */
export function requiredNotices(licenceText) {
  return licenceText
    .split(/\r?\n/u)
    .map((line) => line.trimEnd())
    .filter((line) => line.startsWith('Required Notice:'));
}

/** The required lines a page does not show, read as the browser would show them. */
export function noticesMissingFrom(pageHtml, notices) {
  const shown = pageHtml.replace(/<[^>]*>/gu, ' ').replace(/&amp;/gu, '&').replace(/\s+/gu, ' ');
  return notices.filter((notice) => !shown.includes(notice.replace(/\s+/gu, ' ')));
}

/**
 * Copy the licence files into `siteFolder/licences`, and stop the build if
 * the built page does not show every required notice of the map's licence.
 */
export async function addLicences(siteFolder, modulesFolder) {
  await mkdir(join(siteFolder, 'licences'), { recursive: true });
  for (const [from, to] of LICENCE_FILES) {
    await copyFile(join(modulesFolder, from), join(siteFolder, 'licences', to));
  }
  const notices = requiredNotices(await readFile(join(modulesFolder, LICENCE_FILES[0][0]), 'utf8'));
  if (notices.length === 0) throw new Error("The map's licence holds no Required Notice line: has its wording changed?");
  const missing = noticesMissingFrom(await readFile(join(siteFolder, 'index.html'), 'utf8'), notices);
  if (missing.length > 0) throw new Error(`The page does not show: ${missing.join(' | ')}`);
  return { files: LICENCE_FILES.map(([, to]) => to), notices };
}
