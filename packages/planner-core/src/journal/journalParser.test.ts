import { webcrypto } from 'node:crypto';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { EVENT_PAYLOAD_FIELDS, parseJournalFilesStreaming } from './journalParser';

beforeAll(() => {
  if (!globalThis.crypto?.subtle) vi.stubGlobal('crypto', webcrypto);
});

async function sha256HexOf(bytes: Uint8Array): Promise<string> {
  const digest = await webcrypto.subtle.digest('SHA-256', bytes.buffer as ArrayBuffer);
  return Array.from(new Uint8Array(digest))
    .map((item) => item.toString(16).padStart(2, '0'))
    .join('');
}

describe('V3 journal parser extensions', () => {
  it('does not inherit commander, location or game version from another file', async () => {
    const first = [
      { event: 'Fileheader', gameversion: '4.1' },
      { event: 'Commander', FID: 'F123', Name: 'Owner' },
      { event: 'Location', StarSystem: 'First system', SystemAddress: 123 },
      { event: 'Died', Commander: 'Other player' },
    ].map((row) => JSON.stringify(row)).join('\n');
    const result = await parseJournalFilesStreaming([
      streamingFile(first, 'first.log'),
      streamingFile(JSON.stringify({ event: 'Scan', BodyID: 1 }), 'second.log'),
    ]);
    expect(result.checkpoints[0]?.state.commander).toBe('Owner');
    expect(result.checkpoints[1]?.state).toMatchObject({
      commander: null, system_id64: null, game_version: null,
    });
    expect(result.observations.at(-1)?.payload).not.toHaveProperty('SystemAddress');
    expect(result.observations.at(-1)?.payload).not.toHaveProperty('GameVersion');
  });
  it('reports per-file SHA-256, size, event and line counts in file_manifest without touching the A-1 client_manifest', async () => {
    const text = [
      JSON.stringify({ timestamp: '2026-08-28T10:00:00Z', event: 'Fileheader', gameversion: '4.1.0.0', build: 'r307504' }),
      JSON.stringify({
        timestamp: '2026-08-28T10:00:01Z', event: 'Scan', StarSystem: 'Hash Test',
        SystemAddress: 10477373803, BodyName: 'Hash Test A', BodyID: 1,
      }),
    ].join('\n') + '\n';
    const bytes = new TextEncoder().encode(text);
    const expectedSha256 = await sha256HexOf(bytes);

    const result = await parseJournalFilesStreaming([streamingFile(text, 'Journal.hash.log', 7)]);

    expect(result.file_manifest).toEqual([
      {
        name: 'Journal.hash.log',
        content_sha256: expectedSha256,
        size_bytes: bytes.length,
        event_count: 2,
        line_count: 2,
      },
    ]);
    // The A-1 wire contract (JournalImportFileRef is extra='forbid' server-side)
    // must stay byte-compatible: no new fields in client_manifest.files.
    expect(result.client_manifest.files).toEqual([{ name: 'Journal.hash.log', event_count: 2 }]);
  });

  it('attaches GameVersion/GameBuild from rolling Fileheader/LoadGame state to observation payloads only', async () => {
    const text = [
      JSON.stringify({ timestamp: '2026-08-28T10:00:00Z', event: 'Fileheader', gameversion: '4.1.0.0', build: 'r307504' }),
      JSON.stringify({
        timestamp: '2026-08-28T10:00:01Z', event: 'Scan', StarSystem: 'Version Test',
        SystemAddress: 10477373804, BodyName: 'Version Test A', BodyID: 1,
      }),
      JSON.stringify({
        timestamp: '2026-08-28T10:00:02Z', event: 'FSDJump', StarSystem: 'Other',
        SystemAddress: 10477373805,
      }),
      JSON.stringify({
        timestamp: '2026-08-28T10:00:03Z', event: 'LoadGame', Commander: 'CMDR Test',
        FID: 'F123', gameversion: '4.2.0.0', build: 'r307600',
      }),
      JSON.stringify({
        timestamp: '2026-08-28T10:00:04Z', event: 'ScanOrganic', SystemAddress: 10477373805,
        Body: 3, ScanType: 'Log', Species: '$Species_Bacterium_01;',
      }),
    ].join('\n');

    const result = await parseJournalFilesStreaming([streamingFile(text, 'Journal.version.log')]);
    const payloadByType = Object.fromEntries(result.observations.map((o) => [o.event_type, o.payload]));

    expect(payloadByType.Scan).toMatchObject({ GameVersion: '4.1.0.0', GameBuild: 'r307504' });
    // Non-observation events keep the existing lowercase attach only.
    expect(payloadByType.FSDJump).not.toHaveProperty('GameVersion');
    expect(payloadByType.FSDJump).not.toHaveProperty('GameBuild');
    // The rolling state advances on LoadGame and flows into later observations.
    expect(payloadByType.ScanOrganic).toMatchObject({ GameVersion: '4.2.0.0', GameBuild: 'r307600' });
    expect(result.observations[4]?.event_type).toBe('ScanOrganic');
  });

  it('keeps line-hash dedupe and checkpoints intact while adding the file manifest', async () => {
    const dup = JSON.stringify({
      timestamp: '2026-08-28T10:00:00Z', event: 'Scan', StarSystem: 'Dup Test',
      SystemAddress: 10477373806, BodyName: 'Dup Test A', BodyID: 1,
    });
    const text = `${dup}\n${dup}\n`;
    const result = await parseJournalFilesStreaming([streamingFile(text, 'Journal.dup.log')]);

    expect(result.observations).toHaveLength(1);
    expect(result.checkpoints[0]).toMatchObject({ complete: true, version: 1 });
    expect(result.file_manifest).toHaveLength(1);
    expect(result.file_manifest[0]?.event_count).toBe(1);
  });

  it('reports live per-file progress via the callback', async () => {
    const fileA = streamingFile('{"event":"Fileheader","timestamp":"2026-08-28T09:00:00Z"}\n', 'Journal.a.log');
    const fileB = streamingFile('{"event":"Fileheader","timestamp":"2026-08-28T09:00:01Z"}\n', 'Journal.b.log');
    const updates: Array<{ files_processed: number; files_total: number; events_parsed: number }> = [];

    await parseJournalFilesStreaming([fileA, fileB], (update) => { updates.push(update); });

    expect(updates).toEqual([
      { files_processed: 1, files_total: 2, events_parsed: 1 },
      { files_processed: 2, files_total: 2, events_parsed: 2 },
    ]);
  });

  it('client allowlist parity: exported events can carry the system name the sanitizer requires', () => {
    // Backend fix wave: the server sanitizer fail-closes on a missing system
    // name for every non-sale exported type, so the client parser must also
    // allow SystemName/StarSystem on CodexEntry / ScanOrganic /
    // SAAScanComplete (the client strips pre-network — a field the client
    // strips can never reach the server, so both allowlists must agree).
    for (const eventType of ['CodexEntry', 'ScanOrganic', 'SAAScanComplete'] as const) {
      const allowed = EVENT_PAYLOAD_FIELDS[eventType];
      expect(allowed).toContain('SystemName');
      expect(allowed).toContain('StarSystem');
    }
    expect(EVENT_PAYLOAD_FIELDS.CodexEntry).toContain('System');
  });
});

function streamingFile(text: string, name: string, chunkSize = 17): File {
  const bytes = new TextEncoder().encode(text);
  return {
    name,
    size: bytes.length,
    text: () => Promise.reject(new Error('whole-file text() must not be used')),
    slice(start = 0, end = bytes.length) {
      const part = bytes.slice(start, end);
      let offset = 0;
      return {
        stream() {
          return new ReadableStream<Uint8Array>({
            pull(controller) {
              if (offset >= part.length) {
                controller.close();
                return;
              }
              controller.enqueue(part.slice(offset, offset + chunkSize));
              offset += chunkSize;
            },
          });
        },
      } as Blob;
    },
  } as File;
}
