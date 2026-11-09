import { describe, expect, it, vi } from 'vitest';
import type { V3VerifiedImportReceipt } from '$lib/api/client';
import {
  uploadJournalBatches,
  splitFileForUpload,
  type ParsedFile,
  type SubmitFn,
} from './uploader';

function receipt(
  over: Partial<V3VerifiedImportReceipt> = {},
): V3VerifiedImportReceipt {
  return {
    import_ids: [],
    files_admitted: 0,
    files_skipped: 0,
    events_inserted: 0,
    duplicates_skipped: 0,
    held_files: [],
    ...over,
  };
}

function parsedFile(
  name: string,
  eventCount: number,
  payload: Record<string, unknown> = {},
): ParsedFile {
  return {
    manifest: [
      {
        name,
        content_sha256: `sha-${name}`,
        size_bytes: 1,
        line_count: eventCount,
        event_count: eventCount,
      },
    ],
    events: Array.from({ length: eventCount }, (_, index) => ({
      event_type: 'Scan',
      event_timestamp: '2026-01-01T00:00:00Z',
      source_record_hash: `${name}-${index}`,
      source_file: name,
      source_offset: index,
      payload,
    })),
    held: [],
  };
}

async function* stream(...files: ParsedFile[]): AsyncIterable<ParsedFile> {
  for (const file of files) yield file;
}

// Deterministic, instant test timing.
const fastTiming = {
  backoffMs: () => 1,
  sleep: () => Promise.resolve(),
};

describe('uploadJournalBatches', () => {
  it('splits files into batches bounded by event count and never splits a file', async () => {
    const submit = vi.fn<SubmitFn>().mockResolvedValue(receipt());
    await uploadJournalBatches(
      stream(parsedFile('a', 6), parsedFile('b', 6), parsedFile('c', 6)),
      submit,
      { parserVersion: 'test', maxEvents: 10, ...fastTiming },
    );
    // a(6) fits; adding b(6) would exceed 10 -> flush [a]; b(6)+c(6) exceed -> flush [b]; flush [c].
    expect(submit).toHaveBeenCalledTimes(3);
    for (const [body] of submit.mock.calls) {
      // manifest and events for each file travel together in the same batch.
      const names = new Set(body.files.map((f) => f.name));
      for (const event of body.events ?? [])
        expect(names.has(event.source_file)).toBe(true);
    }
  });

  it('bounds a batch by serialized bytes', async () => {
    const submit = vi.fn<SubmitFn>().mockResolvedValue(receipt());
    const big = 'x'.repeat(4096);
    await uploadJournalBatches(
      stream(
        parsedFile('a', 1, { blob: big }),
        parsedFile('b', 1, { blob: big }),
        parsedFile('c', 1, { blob: big }),
      ),
      submit,
      { parserVersion: 'test', maxBytes: 5000, ...fastTiming },
    );
    // Each file ~4 KiB; two would exceed 5 KiB, so one file per batch.
    expect(submit).toHaveBeenCalledTimes(3);
  });

  it('bounds a batch by file count so the server 200-file cap is never exceeded', async () => {
    const submit = vi.fn<SubmitFn>().mockResolvedValue(receipt());
    await uploadJournalBatches(
      stream(
        parsedFile('a', 1),
        parsedFile('b', 1),
        parsedFile('c', 1),
        parsedFile('d', 1),
        parsedFile('e', 1),
      ),
      submit,
      { parserVersion: 'test', maxFiles: 2, ...fastTiming },
    );
    // 5 tiny files, cap 2 -> [a,b] [c,d] [e]; nothing is bounded by bytes/events.
    expect(submit).toHaveBeenCalledTimes(3);
    for (const [body] of submit.mock.calls)
      expect(body.files.length).toBeLessThanOrEqual(2);
  });

  it('splits an oversized single file across batches instead of dropping it', async () => {
    const submit = vi.fn<SubmitFn>().mockResolvedValue(receipt());
    const result = await uploadJournalBatches(
      stream(parsedFile('huge', 50), parsedFile('small', 1)),
      submit,
      { parserVersion: 'test', maxEvents: 10, ...fastTiming },
    );
    const hugeBatches = submit.mock.calls.filter(([body]) =>
      body.files.some((f) => f.name === 'huge'),
    );
    // 50 events at a 10-event cap -> 5 slices, each carrying the manifest.
    expect(hugeBatches.length).toBe(5);
    for (const [body] of hugeBatches) {
      expect(body.files.map((f) => f.name)).toEqual(['huge']);
      expect(body.events?.length ?? 0).toBeLessThanOrEqual(10);
    }
    const hugeEvents = hugeBatches.reduce(
      (n, [body]) => n + (body.events?.length ?? 0),
      0,
    );
    expect(hugeEvents).toBe(50); // no event lost
    // The file sha is committed once despite spanning multiple batches.
    expect(result.committedShas.filter((s) => s === 'sha-huge')).toEqual([
      'sha-huge',
    ]);
  });

  it('splitFileForUpload keeps a fitting file whole', () => {
    const parts = splitFileForUpload(parsedFile('a', 3), 1_000_000, 20_000);
    expect(parts).toHaveLength(1);
    expect(parts[0].events).toHaveLength(3);
  });

  it('splitFileForUpload slices an oversized file by bytes, repeating the manifest and preserving every event', () => {
    const file = parsedFile('big', 10, { blob: 'x'.repeat(200) });
    const parts = splitFileForUpload(file, 800, 20_000);
    expect(parts.length).toBeGreaterThan(1);
    for (const part of parts) expect(part.manifest).toEqual(file.manifest);
    expect(parts.flatMap((p) => p.events)).toEqual(file.events); // order + no loss/dup
  });

  it('splitFileForUpload also slices by event count', () => {
    const parts = splitFileForUpload(parsedFile('m', 10), 10_000_000, 4);
    expect(parts.map((p) => p.events.length)).toEqual([4, 4, 2]);
  });

  it('uses a sub-1MB default byte budget so batches clear the ~1MB edge limit', async () => {
    const submit = vi.fn<SubmitFn>().mockResolvedValue(receipt());
    // ~1.5 MB of events in one file, default options (no maxBytes override).
    const heavy = parsedFile('heavy', 1500, { blob: 'z'.repeat(1000) });
    await uploadJournalBatches(stream(heavy), submit, {
      parserVersion: 'test',
      ...fastTiming,
    });
    expect(submit.mock.calls.length).toBeGreaterThan(1);
    for (const [body] of submit.mock.calls)
      expect(JSON.stringify(body).length).toBeLessThan(1024 * 1024);
  });

  it('aggregates receipts across batches (import_id union + summed counts)', async () => {
    const submit = vi
      .fn<SubmitFn>()
      .mockResolvedValueOnce(
        receipt({
          import_ids: ['imp-1'],
          files_admitted: 1,
          events_inserted: 6,
        }),
      )
      .mockResolvedValueOnce(
        receipt({
          import_ids: ['imp-1', 'imp-2'],
          files_admitted: 1,
          events_inserted: 6,
          duplicates_skipped: 2,
          held_files: [
            { name: 'b', reason: 'previous_import_needs_ownership_review' },
          ],
        }),
      );
    const result = await uploadJournalBatches(
      stream(parsedFile('a', 6), parsedFile('b', 6)),
      submit,
      { parserVersion: 'test', maxEvents: 10, ...fastTiming },
    );
    expect(result.receipt.import_ids).toEqual(['imp-1', 'imp-2']);
    expect(result.receipt.files_admitted).toBe(2);
    expect(result.receipt.events_inserted).toBe(12);
    expect(result.receipt.duplicates_skipped).toBe(2);
    expect(result.receipt.held_files).toHaveLength(1);
    expect(result.committedShas).toEqual(['sha-a', 'sha-b']);
  });

  it('retries a 429 with backoff then succeeds', async () => {
    const submit = vi
      .fn<SubmitFn>()
      .mockRejectedValueOnce(Object.assign(new Error('rate'), { status: 429 }))
      .mockResolvedValueOnce(
        receipt({ import_ids: ['imp-1'], files_admitted: 1 }),
      );
    const result = await uploadJournalBatches(
      stream(parsedFile('a', 1)),
      submit,
      {
        parserVersion: 'test',
        ...fastTiming,
      },
    );
    expect(submit).toHaveBeenCalledTimes(2);
    expect(result.receipt.files_admitted).toBe(1);
    expect(result.failed).toEqual([]);
  });

  it('retries a transport error (no status) then succeeds', async () => {
    const submit = vi
      .fn<SubmitFn>()
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce(receipt({ files_admitted: 1 }));
    const result = await uploadJournalBatches(
      stream(parsedFile('a', 1)),
      submit,
      {
        parserVersion: 'test',
        ...fastTiming,
      },
    );
    expect(submit).toHaveBeenCalledTimes(2);
    expect(result.failed).toEqual([]);
  });

  it('does not retry a non-retryable 4xx and keeps uploading later batches', async () => {
    const submit = vi
      .fn<SubmitFn>()
      .mockRejectedValueOnce(
        Object.assign(new Error('too big'), { status: 413 }),
      )
      .mockResolvedValueOnce(
        receipt({ files_admitted: 1, import_ids: ['imp-2'] }),
      );
    const result = await uploadJournalBatches(
      stream(parsedFile('a', 6), parsedFile('b', 6)),
      submit,
      { parserVersion: 'test', maxEvents: 10, ...fastTiming },
    );
    expect(submit).toHaveBeenCalledTimes(2); // no retry of the 413 batch
    expect(result.failed).toHaveLength(1);
    expect(result.failed[0].files).toEqual(['a']);
    expect(result.receipt.import_ids).toEqual(['imp-2']);
    expect(result.committedShas).toEqual(['sha-b']);
  });

  it('gives up after maxRetries and records the batch as failed', async () => {
    const submit = vi
      .fn<SubmitFn>()
      .mockRejectedValue(Object.assign(new Error('rate'), { status: 429 }));
    const result = await uploadJournalBatches(
      stream(parsedFile('a', 1)),
      submit,
      {
        parserVersion: 'test',
        maxRetries: 2,
        ...fastTiming,
      },
    );
    expect(submit).toHaveBeenCalledTimes(3); // initial + 2 retries
    expect(result.failed).toHaveLength(1);
  });

  it('stops on abort and returns already-committed work as resumable', async () => {
    const controller = new AbortController();
    const submit = vi.fn<SubmitFn>().mockImplementation(async (body) => {
      if (body.files.some((f) => f.name === 'b')) controller.abort();
      return receipt({ files_admitted: 1 });
    });
    const result = await uploadJournalBatches(
      stream(parsedFile('a', 6), parsedFile('b', 6), parsedFile('c', 6)),
      submit,
      {
        parserVersion: 'test',
        maxEvents: 10,
        signal: controller.signal,
        ...fastTiming,
      },
    );
    // 'a' committed before abort; 'c' never sent.
    expect(result.committedShas).toContain('sha-a');
    expect(result.committedShas).not.toContain('sha-c');
  });

  it('flows held-only files into the held summary without any POST', async () => {
    const submit = vi.fn<SubmitFn>().mockResolvedValue(receipt());
    const heldFile: ParsedFile = {
      manifest: [],
      events: [],
      held: [{ name: 'skip.log', reason: 'Could not read this file' }],
    };
    const result = await uploadJournalBatches(stream(heldFile), submit, {
      parserVersion: 'test',
      ...fastTiming,
    });
    expect(submit).not.toHaveBeenCalled();
    expect(result.held).toEqual([
      { name: 'skip.log', reason: 'Could not read this file' },
    ]);
  });

  it('reports progress as files parse and commit', async () => {
    const submit = vi
      .fn<SubmitFn>()
      .mockResolvedValue(receipt({ files_admitted: 1, events_inserted: 6 }));
    const progress = vi.fn();
    await uploadJournalBatches(
      stream(parsedFile('a', 6), parsedFile('b', 6)),
      submit,
      {
        parserVersion: 'test',
        maxEvents: 10,
        onProgress: progress,
        ...fastTiming,
      },
    );
    const last = progress.mock.calls.at(-1)?.[0];
    expect(last.filesCommitted).toBe(2);
    expect(last.batchesSent).toBe(2);
  });
});
