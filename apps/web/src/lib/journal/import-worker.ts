import { parseJournalFilesStreaming } from '@ed-finder/planner-core/journal';
import type { ParsedFile } from './uploader';

// Streaming, pull-driven parse worker. The main thread pulls one file at a
// time (see parse.ts), so exactly one file's events exist in memory at once and
// upload speed applies natural backpressure — a whole journal folder can be
// imported without the old single-body ceilings. Per-file allowlist stripping
// still happens upstream in the EDRE parser, before anything is emitted here.
const MAX_FILES = 2000;
const MAX_FILE_BYTES = 32 * 1024 * 1024;

let files: File[] = [];
let index = 0;

function heldFile(
  name: string,
  reason: string,
): { type: 'file'; file: ParsedFile } {
  return {
    type: 'file',
    file: { manifest: [], events: [], held: [{ name, reason }] },
  };
}

async function processNext(): Promise<
  { type: 'file'; file: ParsedFile } | { type: 'done' }
> {
  if (index >= files.length) return { type: 'done' };
  const position = index++;
  const file = files[position];
  const name = file.name;
  if (position >= MAX_FILES)
    return heldFile(name, 'Select up to 2000 files per import');
  if (file.size > MAX_FILE_BYTES)
    return heldFile(name, 'File exceeds this importer’s per-file size limit');
  try {
    // Parse one file at a time so cross-file line dedupe cannot remove an
    // identity header. The server owns account/commander semantic dedupe.
    const parsed = await parseJournalFilesStreaming([file]);
    // An identity change must never disappear from ownership classification.
    if (
      parsed.observations.some(
        (row) =>
          ['Commander', 'LoadGame'].includes(row.event_type) &&
          (!row.observed_at || !Number.isFinite(Date.parse(row.observed_at))),
      )
    )
      return heldFile(
        name,
        'Commander identity record has an invalid timestamp; file held for review',
      );
    const events = parsed.observations
      .filter(
        (row) =>
          row.observed_at && Number.isFinite(Date.parse(row.observed_at)),
      )
      .map((row) => ({
        event_type: row.event_type,
        event_timestamp: row.observed_at!,
        source_record_hash: row.observation_key,
        source_file: row.source_file,
        source_offset: row.source_offset ?? 0,
        payload: row.payload,
      }));
    if (!events.length)
      return heldFile(name, 'No supported events with valid timestamps');
    const held: Array<{ name: string; reason: string }> = [];
    if (parsed.preview.skipped_lines)
      held.push({
        name,
        reason: `${parsed.preview.skipped_lines} malformed, duplicate or unsupported lines skipped; valid records retained`,
      });
    return {
      type: 'file',
      file: { manifest: parsed.file_manifest, events, held },
    };
  } catch {
    return heldFile(name, 'Could not read this file; other files can continue');
  }
}

self.onmessage = async (
  message: MessageEvent<{ type: 'init'; files: File[] } | { type: 'pull' }>,
) => {
  const data = message.data;
  if (data.type === 'init') {
    files = data.files;
    index = 0;
    return;
  }
  try {
    self.postMessage(await processNext());
  } catch {
    self.postMessage({
      type: 'error',
      message: 'Journal parsing stopped; no upload was sent',
    });
  }
};
