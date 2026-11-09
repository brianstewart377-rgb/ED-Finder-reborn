import { webcrypto } from 'node:crypto';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import {
  parseJournalFilesStreaming,
  parseJournalJson,
  SUPPORTED_JOURNAL_EVENTS,
} from './index';

beforeAll(() => {
  if (!globalThis.crypto?.subtle) vi.stubGlobal('crypto', webcrypto);
});

describe('BigInt-safe streaming journal parser', () => {
  it('extracts Trailblazers PP2 system and personal events without changing anomalous values', async () => {
    const file = streamingFile([
      JSON.stringify({ timestamp: '2025-03-20T06:59:59Z', event: 'Fileheader', gameversion: '4.1.0.0', build: 'r307504' }),
      JSON.stringify({
        timestamp: '2025-03-20T07:00:00Z', event: 'Location', StarSystem: 'Test',
        SystemAddress: 10477373803, ControllingPower: 'Felicia Winters',
        Powers: ['Felicia Winters'], PowerplayState: 'Fortified',
        PowerplayStateControlProgress: 5000, PowerplayStateReinforcement: -1,
        PowerplayStateUndermining: 999999,
      }),
      JSON.stringify({
        timestamp: '2025-03-20T07:01:00Z', event: 'PowerplayMerits',
        Power: 'Felicia Winters', MeritsGained: 321, TotalMerits: 12345,
      }),
    ].join('\n'), 'Journal.powerplay.log');
    const result = await parseJournalFilesStreaming([file]);
    expect(result.powerplay_events).toHaveLength(2);
    expect(result.preview.powerplay_events_ready).toBe(2);
    expect(result.powerplay_events[0]?.game_build).toBe('4.1.0.0 / r307504');
    expect(result.powerplay_events[0]?.source_payload.PowerplayStateControlProgress).toBe(5000);
    expect(result.powerplay_events[0]?.source_payload.PowerplayStateReinforcement).toBe(-1);
    expect(result.powerplay_events[1]?.source_payload.MeritsGained).toBe(321);
  });
  it('preserves an id64 above 2^53 and BodyID=0 as decimal strings', async () => {
    const parsed = parseJournalJson('{"SystemAddress":9007199254740993,"BodyID":0}');
    expect(parsed.SystemAddress).toBe(9007199254740993n);

    const result = await parseJournalFilesStreaming([streamingFile([
      journalLine('Scan', {
        StarSystem: 'Precision Reach',
        SystemAddress: rawNumber('9007199254740993'),
        BodyName: 'Precision Reach',
        BodyID: 0,
        StarType: 'G',
      }),
    ].join('\n'), 'Journal.precision.log')]);

    expect(result.observations).toHaveLength(1);
    expect(result.observations[0]).toMatchObject({
      system_id64: '9007199254740993',
      subject_type: 'body',
      subject_id: '0',
      source_offset: 0,
    });
    expect(result.observations[0]?.payload).toMatchObject({
      SystemAddress: '9007199254740993',
      BodyID: '0',
    });
  });

  it('emits every supported event in source order with surface and screenshot metadata', async () => {
    const lines = SUPPORTED_JOURNAL_EVENTS.map((event, index) => journalLine(event, {
      ...(event === 'Location' ? {
        StarSystem: 'Coverage',
        SystemAddress: rawNumber('12884901889'),
        StarPos: [1, 2, 3],
      } : {}),
      ...(event === 'NavRoute' ? {
        Route: [{ StarSystem: 'Coverage', SystemAddress: rawNumber('12884901889'), StarPos: [1, 2, 3] }],
      } : {}),
      ...(bodyEvent(event) ? { BodyName: 'Coverage A 1', BodyID: 0 } : {}),
      ...(event === 'ScanOrganic' ? { ScanType: 'Analyse', Species: '$Species_Bacterium_01;' } : {}),
      ...(event === 'Screenshot' ? {
        Filename: 'C:\\Users\\Commander\\Pictures\\Screenshot_0001.bmp',
        Width: 3840,
        Height: 2160,
        Latitude: 0,
        Longitude: 0,
        Altitude: 42,
        Heading: 90,
      } : {}),
      timestamp: `2026-08-12T20:${String(index).padStart(2, '0')}:00Z`,
    }));

    const result = await parseJournalFilesStreaming([
      streamingFile(lines.join('\r\n'), 'Journal.coverage.log', 11),
    ]);

    expect(result.observations.map((item) => item.event_type)).toEqual(SUPPORTED_JOURNAL_EVENTS);
    const screenshot = result.observations.find((item) => item.event_type === 'Screenshot');
    expect(screenshot?.payload).toMatchObject({
      Filename: 'Screenshot_0001.bmp',
      Width: 3840,
      Height: 2160,
      Latitude: 0,
      Longitude: 0,
      Altitude: 42,
      Heading: 90,
    });
    expect(result.checkpoints[0]).toMatchObject({ complete: true, next_offset: expect.any(Number) });
  });

  it('uses content identity for renamed/copied journals, not the filename or offset', async () => {
    const event = journalLine('FSDJump', {
      StarSystem: 'Copy Test',
      SystemAddress: rawNumber('9007199254740995'),
      StarPos: [0, 0, 0],
    });
    const original = await parseJournalFilesStreaming([streamingFile(`${event}\n`, 'Journal.original.log')]);
    const renamed = await parseJournalFilesStreaming([streamingFile(`\n${event}\n`, 'Journal.renamed.log')]);
    const together = await parseJournalFilesStreaming([
      streamingFile(`${event}\n`, 'Journal.original.log'),
      streamingFile(`\n${event}\n`, 'Journal.copy.log'),
    ]);

    expect(original.observations[0]?.observation_key).toBe(renamed.observations[0]?.observation_key);
    expect(original.observations[0]?.source_file).not.toBe(renamed.observations[0]?.source_file);
    expect(original.observations[0]?.source_offset).not.toBe(renamed.observations[0]?.source_offset);
    expect(together.observations).toHaveLength(1);
  });

  it('preserves the exact organic Log, Sample, Sample, Analyse sequence', async () => {
    const scanTypes = ['Log', 'Sample', 'Sample', 'Analyse'];
    const lines = [
      journalLine('Location', {
        StarSystem: 'Sequence',
        SystemAddress: rawNumber('9007199254740996'),
      }),
      ...scanTypes.map((ScanType, index) => journalLine('ScanOrganic', {
        timestamp: `2026-08-12T21:00:0${index + 1}Z`,
        Body: 0,
        ScanType,
        Genus: '$Codex_Ent_Bacterial_Genus_Name;',
        Species: '$Codex_Ent_Bacterial_01_Name;',
        Variant: '$Codex_Ent_Bacterial_01_A_Name;',
      })),
    ];

    const result = await parseJournalFilesStreaming([
      streamingFile(lines.join('\n'), 'Journal.organics.log', 7),
    ]);
    const organic = result.observations.filter((item) => item.event_type === 'ScanOrganic');

    expect(organic.map((item) => item.payload.ScanType)).toEqual(scanTypes);
    expect(organic.map((item) => item.subject_id)).toEqual(['0', '0', '0', '0']);
    expect(organic.map((item) => item.system_id64)).toEqual([
      '9007199254740996', '9007199254740996',
      '9007199254740996', '9007199254740996',
    ]);
  });

  it('resumes at a byte checkpoint while preserving system and body state', async () => {
    const first = journalLine('Location', {
      StarSystem: 'Resume',
      SystemAddress: rawNumber('9007199254740997'),
    });
    const second = journalLine('ApproachBody', { Body: 'Resume A', BodyID: 0 });
    const third = journalLine('ScanOrganic', { Body: 0, ScanType: 'Log', Species: '$Species_Tussock_01;' });
    const text = `${first}\n${second}\n${third}\n`;
    const boundary = new TextEncoder().encode(`${first}\n${second}\n`).length;
    const file = streamingFile(text, 'Journal.resume.log', 5);

    const initial = await parseJournalFilesStreaming([{ file, end_offset: boundary }]);
    expect(initial.checkpoints[0]).toMatchObject({
      complete: false,
      next_offset: boundary,
      state: { system_id64: '9007199254740997', body_id: '0', body_name: 'Resume A' },
    });

    const resumed = await parseJournalFilesStreaming([{
      file,
      checkpoint: initial.checkpoints[0],
    }]);
    expect(resumed.observations).toHaveLength(1);
    expect(resumed.observations[0]).toMatchObject({
      event_type: 'ScanOrganic',
      system_id64: '9007199254740997',
      subject_id: '0',
      source_offset: boundary,
    });
  });

  it('streams multi-year files in order without attributing an unidentified file to the prior file context', async () => {
    const oldFile = streamingFile([
      journalLine('Fileheader', { timestamp: '2019-01-01T00:00:00Z', gameversion: '3.3' }),
      journalLine('Location', {
        timestamp: '2019-01-01T00:00:01Z',
        StarSystem: 'Long Memory',
        SystemAddress: rawNumber('9007199254740999'),
      }),
    ].join('\n'), 'Journal.2019.log', 3);
    const newFile = streamingFile([
      journalLine('Fileheader', { timestamp: '2026-08-12T00:00:00Z', gameversion: '4.2' }),
      journalLine('ScanOrganic', {
        timestamp: '2026-08-12T00:00:01Z',
        Body: 0,
        ScanType: 'Log',
        Species: '$Species_Fonticulua_01;',
      }),
    ].join('\n'), 'Journal.2026.log', 2);

    const result = await parseJournalFilesStreaming([oldFile, newFile]);

    expect(result.observations.map((item) => item.event_type)).toEqual([
      'Fileheader', 'Location', 'Fileheader', 'ScanOrganic',
    ]);
    expect(result.observations[3]).toMatchObject({
      system_id64: null,
      subject_id: '0',
    });
    expect(result.client_manifest.files).toEqual([
      { name: 'Journal.2019.log', event_count: 2 },
      { name: 'Journal.2026.log', event_count: 2 },
    ]);
  });
});

function journalLine(event: string, fields: Record<string, unknown>): string {
  const tokens = Object.entries({ timestamp: '2026-08-12T20:00:00Z', event, ...fields })
    .map(([key, value]) => `"${key}":${isRawNumber(value) ? value.value : JSON.stringify(value)}`);
  return `{${tokens.join(',')}}`;
}

class RawNumber {
  constructor(readonly value: string) {}
}

function isRawNumber(value: unknown): value is RawNumber {
  return value !== null && typeof value === 'object' && 'value' in value
    && typeof (value as { value?: unknown }).value === 'string';
}

function rawNumber(value: string): RawNumber {
  return new RawNumber(value);
}

function bodyEvent(event: string): boolean {
  return new Set([
    'ApproachBody', 'CodexEntry', 'Disembark', 'Embark', 'FSSBodySignals', 'LeaveBody',
    'Liftoff', 'SAAScanComplete', 'SAASignalsFound', 'Scan', 'ScanOrganic', 'Screenshot', 'Touchdown',
  ]).has(event);
}

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
