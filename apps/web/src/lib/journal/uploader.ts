import type {
  V3JournalImportRequest,
  V3VerifiedImportReceipt,
} from '$lib/api/client';

type FileRef = V3JournalImportRequest['files'][number];
type EventInput = NonNullable<V3JournalImportRequest['events']>[number];

export interface HeldItem {
  name: string;
  reason: string;
}

/**
 * One journal file's parsed, allowlist-stripped result. A file's manifest and
 * its events always travel together so the server's "every event's source_file
 * is in the manifest" invariant holds within each uploaded batch.
 */
export interface ParsedFile {
  manifest: FileRef[]; // usually one entry; empty when the file is held
  events: EventInput[]; // empty when the file is held
  held: HeldItem[];
}

export interface CommittedBatch {
  shas: string[]; // content_sha256 of files committed in this batch
  importIds: string[]; // import_ids the server returned for this batch
}

export interface UploadProgress {
  filesParsed: number;
  filesCommitted: number;
  eventsCommitted: number;
  batchesSent: number;
}

export interface UploadResult {
  receipt: V3VerifiedImportReceipt; // aggregated across every batch
  held: HeldItem[]; // parse-time holds + failed-batch reasons
  committedShas: string[]; // content_sha256 committed (drives resume)
  failed: Array<{ files: string[]; reason: string }>;
}

export type SubmitFn = (
  body: V3JournalImportRequest,
  signal?: AbortSignal,
) => Promise<V3VerifiedImportReceipt>;

export interface UploadOptions {
  parserVersion: string;
  maxBytes?: number; // batch flush threshold, serialized (default 700 KiB, under the ~1 MB edge limit)
  maxEvents?: number; // batch flush threshold, event count (default 20,000)
  maxFiles?: number; // batch flush threshold, file count (default 200)
  maxRetries?: number; // retryable-failure attempts after the first (default 5)
  signal?: AbortSignal;
  onProgress?: (progress: UploadProgress) => void;
  onBatchCommitted?: (batch: CommittedBatch) => void;
  isRetryable?: (error: unknown) => boolean;
  backoffMs?: (attempt: number) => number;
  sleep?: (ms: number, signal?: AbortSignal) => Promise<void>;
}

// The public edge / CDN in front of production rejects request bodies over
// ~1 MB (nginx's default client_max_body_size) with a 413 before the API is
// reached, and that limit is not controllable from application code. Keep each
// batch comfortably under it; a ~500 KB body is known to pass.
const DEFAULT_MAX_BYTES = 700 * 1024;

// Retry transport failures (no status), 429s, and 5xx. Everything else — a
// residual 413, a 422 validation error — is deterministic and must not be
// retried; the batch is recorded as failed and later batches still upload.
function defaultRetryable(error: unknown): boolean {
  const status = (error as { status?: number } | null)?.status;
  if (status === undefined || status === 0) return true;
  if (status === 429) return true;
  return status >= 500;
}

function defaultBackoff(attempt: number): number {
  const base = Math.min(30_000, 500 * 2 ** (attempt - 1));
  return base + Math.floor(Math.random() * 250);
}

function defaultSleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted)
      return reject(new DOMException('Cancelled', 'AbortError'));
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new DOMException('Cancelled', 'AbortError'));
      },
      { once: true },
    );
  });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'upload failed';
}

function estimateBytes(file: ParsedFile): number {
  return (
    JSON.stringify(file.events).length + JSON.stringify(file.manifest).length
  );
}

/**
 * Split one parsed file into units that each fit under the byte/event budget,
 * repeating the file manifest in every slice. A single journal session can
 * exceed the ~1 MB edge limit on its own, and the server requires every event's
 * source_file to be present in the same request's manifest — so an oversized
 * file must be sent as several manifest-carrying slices rather than dropped.
 * Idempotent: slices dedupe server-side by source_record_hash. Held reasons are
 * handled by the caller before splitting, so slices carry none.
 */
export function splitFileForUpload(
  file: ParsedFile,
  maxBytes: number,
  maxEvents: number,
): ParsedFile[] {
  const total = estimateBytes(file);
  if (
    file.events.length <= 1 ||
    (total <= maxBytes && file.events.length <= maxEvents)
  )
    return [{ manifest: file.manifest, events: file.events, held: [] }];
  const manifestBytes = JSON.stringify(file.manifest).length;
  const perEvent = Math.max(
    1,
    Math.ceil((total - manifestBytes) / file.events.length),
  );
  const byBytes = Math.floor((maxBytes - manifestBytes) / perEvent);
  const sliceSize = Math.max(1, Math.min(maxEvents, byBytes));
  const parts: ParsedFile[] = [];
  for (let start = 0; start < file.events.length; start += sliceSize)
    parts.push({
      manifest: file.manifest,
      events: file.events.slice(start, start + sliceSize),
      held: [],
    });
  return parts;
}

/**
 * Consume a stream of parsed files and upload them in bounded, independently
 * committed batches. Each batch is retried on transient failure, and because
 * the server dedupes by content hash, an interrupted run is safe to resume by
 * re-parsing only the files whose content_sha256 is not in `committedShas`.
 */
export async function uploadJournalBatches(
  source: AsyncIterable<ParsedFile>,
  submit: SubmitFn,
  options: UploadOptions,
): Promise<UploadResult> {
  const maxBytes = options.maxBytes ?? DEFAULT_MAX_BYTES;
  const maxEvents = options.maxEvents ?? 20_000;
  // Matches the server's MAX_FILES_PER_IMPORT: a batch with more than 200 file
  // manifest entries is rejected with a non-retryable 422 before the handler runs.
  const maxFiles = options.maxFiles ?? 200;
  const maxRetries = options.maxRetries ?? 5;
  const isRetryable = options.isRetryable ?? defaultRetryable;
  const backoffMs = options.backoffMs ?? defaultBackoff;
  const sleep = options.sleep ?? defaultSleep;
  const signal = options.signal;

  const importIds = new Set<string>();
  const receipt: V3VerifiedImportReceipt = {
    import_ids: [],
    files_admitted: 0,
    files_skipped: 0,
    events_inserted: 0,
    duplicates_skipped: 0,
    held_files: [],
  };
  const held: HeldItem[] = [];
  // A Set so a split file (whose sha appears in several sub-batches) is counted
  // and reported once.
  const committed = new Set<string>();
  const failed: UploadResult['failed'] = [];

  let batchFiles: FileRef[] = [];
  let batchEvents: EventInput[] = [];
  let batchShas: string[] = [];
  let batchNames: string[] = [];
  let batchBytes = 0;
  let filesParsed = 0;
  let batchesSent = 0;

  const resetBatch = () => {
    batchFiles = [];
    batchEvents = [];
    batchShas = [];
    batchNames = [];
    batchBytes = 0;
  };

  const emit = () =>
    options.onProgress?.({
      filesParsed,
      filesCommitted: committed.size,
      eventsCommitted: receipt.events_inserted,
      batchesSent,
    });

  const submitWithRetry = async (
    body: V3JournalImportRequest,
  ): Promise<V3VerifiedImportReceipt> => {
    let attempt = 0;
    for (;;) {
      if (signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
      try {
        return await submit(body, signal);
      } catch (error) {
        if (signal?.aborted) throw error;
        attempt += 1;
        if (attempt > maxRetries || !isRetryable(error)) throw error;
        await sleep(backoffMs(attempt), signal);
      }
    }
  };

  const flush = async () => {
    if (!batchFiles.length) return;
    const names = batchNames;
    const shas = batchShas;
    const body: V3JournalImportRequest = {
      parser_version: options.parserVersion,
      files: batchFiles,
      events: batchEvents,
    };
    resetBatch();
    let committedReceipt: V3VerifiedImportReceipt;
    try {
      committedReceipt = await submitWithRetry(body);
    } catch (error) {
      if (signal?.aborted) throw error; // propagate abort to stop the run
      const reason = errorMessage(error);
      failed.push({ files: names, reason });
      for (const name of names)
        held.push({ name, reason: `Upload failed: ${reason}` });
      return;
    }
    batchesSent += 1;
    for (const id of committedReceipt.import_ids) importIds.add(id);
    receipt.files_admitted += committedReceipt.files_admitted;
    receipt.files_skipped += committedReceipt.files_skipped;
    receipt.events_inserted += committedReceipt.events_inserted;
    receipt.duplicates_skipped += committedReceipt.duplicates_skipped;
    receipt.held_files.push(...committedReceipt.held_files);
    for (const sha of shas) committed.add(sha);
    options.onBatchCommitted?.({
      shas,
      importIds: committedReceipt.import_ids,
    });
    emit();
  };

  try {
    for await (const file of source) {
      if (signal?.aborted) break;
      filesParsed += 1;
      if (file.held.length) held.push(...file.held);
      if (!file.events.length) {
        emit();
        continue;
      }
      // An oversized file is split into manifest-carrying slices so no single
      // request exceeds the edge body limit; normal files yield one unit.
      for (const unit of splitFileForUpload(file, maxBytes, maxEvents)) {
        const unitBytes = estimateBytes(unit);
        if (
          batchFiles.length &&
          (batchBytes + unitBytes > maxBytes ||
            batchEvents.length + unit.events.length > maxEvents ||
            batchFiles.length + unit.manifest.length > maxFiles)
        )
          await flush();
        batchFiles.push(...unit.manifest);
        batchEvents.push(...unit.events);
        for (const entry of unit.manifest) {
          batchShas.push(entry.content_sha256);
          batchNames.push(entry.name);
        }
        batchBytes += unitBytes;
        // A slice at (or over) the thresholds is flushed as its own batch.
        if (
          batchBytes >= maxBytes ||
          batchEvents.length >= maxEvents ||
          batchFiles.length >= maxFiles
        )
          await flush();
      }
      emit();
    }
    if (!signal?.aborted) await flush();
  } catch (error) {
    if (!signal?.aborted) throw error; // only abort is swallowed for a partial result
  }

  receipt.import_ids = [...importIds].sort();
  return { receipt, held, committedShas: [...committed], failed };
}
