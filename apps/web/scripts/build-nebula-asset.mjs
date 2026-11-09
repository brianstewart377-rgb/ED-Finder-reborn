import { createHash } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const SOURCE_URL =
  'https://edastro.com/mapcharts/files/nebulae-coordinates.csv';
const CATALOGUE_URL = 'https://edastro.com/mapcharts/files.html';
const OUTPUT_URL = new URL(
  '../static/assets/edastro-mapcharts-nebulae.json',
  import.meta.url,
);
const REQUIRED_COLUMNS = ['Name', 'System', 'X', 'Y', 'Z', 'Type', 'RegionID'];
const ACCEPTED_TYPES = new Set(['planetary', 'real', 'procgen']);

function parseCsv(source) {
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;

  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (quoted) {
      if (character === '"' && source[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (character === '"') {
        quoted = false;
      } else {
        field += character;
      }
      continue;
    }
    if (character === '"') {
      quoted = true;
    } else if (character === ',') {
      row.push(field);
      field = '';
    } else if (character === '\n') {
      row.push(field.replace(/\r$/u, ''));
      rows.push(row);
      row = [];
      field = '';
    } else {
      field += character;
    }
  }
  if (quoted) throw new Error('Nebula CSV ends inside a quoted field');
  if (field.length > 0 || row.length > 0) {
    row.push(field.replace(/\r$/u, ''));
    rows.push(row);
  }
  return rows.filter((candidate) => candidate.some((value) => value !== ''));
}

const response = await fetch(SOURCE_URL, {
  headers: { 'user-agent': 'ED-Finder nebula asset builder' },
});
if (!response.ok) {
  throw new Error(
    `EDAstro Mapcharts nebula request failed with HTTP ${response.status}`,
  );
}

const sourceText = await response.text();
const sourceByteCount = Buffer.byteLength(sourceText, 'utf8');
const sourceSha256 = createHash('sha256').update(sourceText).digest('hex');
const rows = parseCsv(sourceText.replace(/^\uFEFF/u, ''));
const header = rows.shift();
if (!header) throw new Error('Nebula CSV is empty');
const column = Object.fromEntries(
  REQUIRED_COLUMNS.map((name) => [name, header.indexOf(name)]),
);
for (const name of REQUIRED_COLUMNS) {
  if (column[name] === -1) {
    throw new Error(`Nebula CSV is missing required column ${name}`);
  }
}

const nebulae = rows
  .map((row, rowIndex) => {
    if (row.length !== header.length) {
      throw new Error(
        `Nebula CSV row ${rowIndex + 2} has ${row.length} columns; expected ${header.length}`,
      );
    }
    const name = row[column.Name].trim();
    const systemName = row[column.System].trim();
    const coordinates = [column.X, column.Y, column.Z].map((index) =>
      Number(row[index]),
    );
    const sourceType = row[column.Type].trim().toLowerCase();
    const regionId = Number(row[column.RegionID]);
    if (
      !name ||
      !systemName ||
      !coordinates.every(Number.isFinite) ||
      !ACCEPTED_TYPES.has(sourceType) ||
      !Number.isInteger(regionId) ||
      regionId < 1 ||
      regionId > 42
    ) {
      throw new Error(`Nebula CSV row ${rowIndex + 2} is malformed`);
    }
    const canonicalIdentity = [
      name,
      systemName,
      ...coordinates.map(String),
      sourceType,
      String(regionId),
    ].join('\u001f');
    const sourceId = createHash('sha256')
      .update(canonicalIdentity)
      .digest('hex')
      .slice(0, 20);
    return {
      id: `MAPCHARTS:${sourceId}`,
      source: 'EDAstro Mapcharts',
      sourceId,
      name,
      systemName,
      regionId,
      kind: sourceType === 'planetary' ? 'planetary-nebula' : 'nebula',
      sourceType,
      coordinates,
      poiUrl: null,
    };
  })
  .sort((left, right) => left.id.localeCompare(right.id));

if (nebulae.length < 5_000) {
  throw new Error(
    `EDAstro Mapcharts nebula inventory unexpectedly contains only ${nebulae.length} rows`,
  );
}
if (new Set(nebulae.map((nebula) => nebula.id)).size !== nebulae.length) {
  throw new Error('EDAstro Mapcharts nebula identities are not unique');
}

const asset = {
  schemaVersion: 2,
  datasetId: 'edastro-mapcharts-nebulae-coordinates',
  sourceUrl: SOURCE_URL,
  catalogueUrl: CATALOGUE_URL,
  rightsNotice: 'Copyright © CMDR Orvidius — All Rights Reserved',
  usageBasis:
    'Purpose-published automation-ready CSV; non-commercial use accepted by the ED-Finder owner',
  attribution: 'EDAstro / CMDR Orvidius',
  sourceLastModified: response.headers.get('last-modified'),
  sourceByteCount,
  sourceSha256,
  count: nebulae.length,
  nebulae,
};

await writeFile(OUTPUT_URL, `${JSON.stringify(asset)}\n`, 'utf8');
console.log(
  `Wrote ${nebulae.length} EDAstro Mapcharts nebula landmarks to ${fileURLToPath(OUTPUT_URL)}`,
);
