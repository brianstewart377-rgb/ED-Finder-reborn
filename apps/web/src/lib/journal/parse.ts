import type { ParsedFile } from './uploader';

type WorkerReply =
  | { type: 'file'; file: ParsedFile }
  | { type: 'done' }
  | { type: 'error'; message: string };

/**
 * Expose the parse worker as a pull-driven `AsyncIterable<ParsedFile>`. Each
 * `next()` asks the worker for exactly one more file, so a slow consumer (an
 * in-flight batch upload) throttles parsing and memory stays flat regardless of
 * how many files were selected.
 */
export function streamJournals(
  files: File[],
  signal: AbortSignal,
): AsyncIterable<ParsedFile> {
  return {
    async *[Symbol.asyncIterator]() {
      const worker = new Worker(
        new URL('./import-worker.ts', import.meta.url),
        {
          type: 'module',
        },
      );
      let reject: ((error: unknown) => void) | null = null;
      const onAbort = () => {
        worker.terminate();
        reject?.(new DOMException('Cancelled', 'AbortError'));
      };
      signal.addEventListener('abort', onAbort, { once: true });
      worker.postMessage({ type: 'init', files });
      const pull = () =>
        new Promise<WorkerReply>((resolve, rej) => {
          reject = rej;
          worker.onmessage = (event: MessageEvent<WorkerReply>) => {
            reject = null;
            resolve(event.data);
          };
          worker.onerror = (event) => {
            reject = null;
            rej(
              new Error(
                (event as ErrorEvent).message || 'Journal parsing failed',
              ),
            );
          };
          worker.postMessage({ type: 'pull' });
        });
      try {
        for (;;) {
          if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
          const reply = await pull();
          if (reply.type === 'done') return;
          if (reply.type === 'error') throw new Error(reply.message);
          yield reply.file;
        }
      } finally {
        signal.removeEventListener('abort', onAbort);
        worker.terminate();
      }
    },
  };
}
