import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient } from '@tanstack/svelte-query';
import { tick } from 'svelte';
import * as api from '$lib/api/client';
import { queryKeys } from '$lib/api/query';
import { auth, type AuthState } from '$lib/auth/auth';
import { streamJournals } from '$lib/journal/parse';
import {
  uploadJournalBatches,
  type CommittedBatch,
  type UploadResult,
} from '$lib/journal/uploader';
import JournalAccountPanel from './JournalAccountPanelTestHost.svelte';

vi.mock('$lib/api/client', () => ({
  getVerifiedCommanders: vi.fn(),
  importVerifiedJournals: vi.fn(),
  offerGalaxyFacts: vi.fn(),
  getGalaxyContributions: vi.fn(),
  withdrawGalaxyContribution: vi.fn(),
  getGalaxyImpact: vi.fn(),
}));
vi.mock('$lib/journal/parse', () => ({ streamJournals: vi.fn() }));
vi.mock('$lib/journal/uploader', () => ({ uploadJournalBatches: vi.fn() }));
vi.mock('$lib/auth/auth', async () => {
  const { writable } = await import('svelte/store');
  return { auth: { ...writable({}), signIn: vi.fn() } };
});

const hash = 'a'.repeat(64);
const saved: api.V3VerifiedImportReceipt = {
  import_ids: ['original-import'],
  files_admitted: 1,
  files_skipped: 0,
  events_inserted: 2,
  duplicates_skipped: 0,
  held_files: [{ name: 'another.log', reason: 'commander_not_linked' }],
};

function result(over: Partial<UploadResult> = {}): UploadResult {
  return {
    receipt: saved,
    held: [],
    committedShas: [hash],
    failed: [],
    ...over,
  };
}

/** Drive the panel's upload path: emit the given committed batches, then resolve. */
function mockUpload(res: UploadResult, batches: CommittedBatch[] = []) {
  vi.mocked(uploadJournalBatches).mockImplementation(
    async (_source, _submit, options) => {
      for (const batch of batches) options.onBatchCommitted?.(batch);
      return res;
    },
  );
}

async function selectFile() {
  await screen.findByText(/F123/);
  await fireEvent.change(screen.getByLabelText('Select journal logs'), {
    target: { files: [new File(['journal'], 'mine.log')] },
  });
}

describe('private journal import and explicit sharing', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    (auth as typeof auth & { set: (value: AuthState) => void }).set({
      authenticated: true,
      loading: false,
      user: {
        account_id: 'account-one',
        commander_name: 'Test Commander',
        is_owner: false,
      },
      ownerClaimAvailable: false,
      error: null,
    });
    vi.mocked(api.getGalaxyImpact).mockResolvedValue({
      systems_discovered: 0,
      bodies_scanned: 0,
      earth_like_worlds: 0,
      water_worlds: 0,
      ammonia_worlds: 0,
      terraformable_candidates: 0,
      gas_giants: 0,
    });
    vi.mocked(api.getVerifiedCommanders).mockResolvedValue([
      {
        commander_id: 'commander',
        commander_name: 'Test Commander',
        journal_fid: 'F123',
        verified_at: '2026-01-01T00:00:00Z',
      },
    ]);
    vi.mocked(api.getGalaxyContributions).mockResolvedValue([]);
    vi.mocked(api.importVerifiedJournals).mockResolvedValue(saved);
    vi.mocked(api.offerGalaxyFacts).mockResolvedValue({
      new_offers: 1,
      already_offered: 0,
      skipped: {},
    });
    vi.mocked(streamJournals).mockReturnValue(
      (async function* () {})() as unknown as AsyncIterable<never>,
    );
    mockUpload(result());
  });
  afterEach(cleanup);

  it('keeps sharing off by default and reports held files alongside successful imports', async () => {
    render(JournalAccountPanel);
    expect(screen.getByRole('checkbox')).not.toBeChecked();
    await selectFile();
    await fireEvent.click(
      screen.getByRole('button', { name: 'Import journals' }),
    );
    await screen.findByText(/2 events saved/);
    expect(
      screen.getByText(/another.log: commander not linked/),
    ).toBeInTheDocument();
    expect(api.offerGalaxyFacts).not.toHaveBeenCalled();
  });

  it('offers only the committed batch hashes for the returned import ids', async () => {
    mockUpload(result(), [{ shas: [hash], importIds: ['original-import'] }]);
    vi.mocked(api.offerGalaxyFacts).mockRejectedValueOnce(new Error('offline'));
    render(JournalAccountPanel);
    await selectFile();
    await fireEvent.click(screen.getByRole('checkbox'));
    await fireEvent.click(
      screen.getByRole('button', { name: 'Import journals' }),
    );
    await screen.findByText(/Your Journal is saved. Sharing needs a retry/);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Retry sharing saved import' }),
    );
    await waitFor(() => expect(api.offerGalaxyFacts).toHaveBeenCalledTimes(2));
    expect(api.offerGalaxyFacts).toHaveBeenLastCalledWith(
      'original-import',
      [hash],
      expect.any(AbortSignal),
    );
  });

  it('bounds merged sharing retries and retains only unsuccessful hash chunks', async () => {
    const hashes = (start: number) =>
      Array.from({ length: 200 }, (_, index) =>
        (start + index).toString(16).padStart(64, '0'),
      );
    vi.mocked(api.offerGalaxyFacts)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ new_offers: 1, already_offered: 0, skipped: {} })
      .mockRejectedValueOnce(new Error('second chunk offline'));
    render(JournalAccountPanel);
    await screen.findByText(/F123/);
    await fireEvent.click(screen.getByRole('checkbox'));

    const importBatch = async (shas: string[]) => {
      mockUpload(result({ committedShas: shas }), [
        { shas, importIds: ['original-import'] },
      ]);
      await fireEvent.change(screen.getByLabelText('Select journal logs'), {
        target: { files: [new File(['journal'], 'batch.log')] },
      });
      await fireEvent.click(
        screen.getByRole('button', { name: 'Import journals' }),
      );
    };

    await importBatch(hashes(1));
    await screen.findByText(/Your Journal is saved. Sharing needs a retry/);
    await importBatch(hashes(200));
    await waitFor(() => expect(api.offerGalaxyFacts).toHaveBeenCalledTimes(3));
    expect(
      vi.mocked(api.offerGalaxyFacts).mock.calls.map((call) => call[1].length),
    ).toEqual([200, 200, 199]);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Retry sharing saved import' }),
    );
    await waitFor(() => expect(api.offerGalaxyFacts).toHaveBeenCalledTimes(4));
    expect(vi.mocked(api.offerGalaxyFacts).mock.calls[3][1]).toEqual(
      hashes(200).slice(1),
    );
  });

  it('aborts work on account-panel removal and ignores a late response', async () => {
    let complete!: (value: UploadResult) => void;
    let seen!: AbortSignal;
    vi.mocked(uploadJournalBatches).mockImplementation(
      (_source, _submit, options) => {
        seen = options.signal!;
        return new Promise((resolve) => {
          complete = resolve;
        });
      },
    );
    const panel = render(JournalAccountPanel);
    await selectFile();
    await fireEvent.click(screen.getByRole('checkbox'));
    await fireEvent.click(
      screen.getByRole('button', { name: 'Import journals' }),
    );
    await waitFor(() => expect(uploadJournalBatches).toHaveBeenCalledOnce());
    panel.unmount();
    expect(seen.aborted).toBe(true);
    complete(result());
    await Promise.resolve();
    expect(api.offerGalaxyFacts).not.toHaveBeenCalled();
  });

  it('keeps verified commanders usable if loading contribution history fails', async () => {
    vi.mocked(api.getGalaxyContributions).mockRejectedValue(
      new Error('offline'),
    );
    render(JournalAccountPanel);
    await selectFile();
    expect(
      screen.getByRole('button', { name: 'Import journals' }),
    ).toBeEnabled();
    expect(screen.getByRole('alert')).toHaveTextContent('Some account data');
  });

  it('shows a recovery message without exposing raw import errors', async () => {
    vi.mocked(uploadJournalBatches).mockRejectedValue(
      new Error('Worker failed to load: 415'),
    );
    render(JournalAccountPanel);
    await selectFile();
    await fireEvent.click(
      screen.getByRole('button', { name: 'Import journals' }),
    );
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(
        'Import could not finish. Saved progress is safe; please try again.',
      ),
    );
  });

  it.each(['complete', 'partial', 'cancelled', 'interrupted'] as const)(
    'refreshes impact after saved batches when an import is %s',
    async (outcome) => {
      const client = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      });
      const invalidate = vi.spyOn(client, 'invalidateQueries');
      let importSignal: AbortSignal | undefined;
      vi.mocked(uploadJournalBatches).mockImplementation(
        async (_source, _submit, options) => {
          importSignal = options.signal;
          options.onBatchCommitted?.({
            shas: [hash],
            importIds: ['original-import'],
          });
          if (outcome === 'interrupted') throw new Error('connection lost');
          if (outcome === 'cancelled') {
            await tick();
            await fireEvent.click(
              screen.getByRole('button', { name: 'Stop import' }),
            );
          }
          return result(
            outcome === 'partial'
              ? { failed: [{ files: ['retry.log'], reason: 'offline' }] }
              : {},
          );
        },
      );
      render(JournalAccountPanel, { props: { client } });
      await selectFile();
      await fireEvent.click(
        screen.getByRole('button', { name: 'Import journals' }),
      );
      await waitFor(() =>
        expect(invalidate).toHaveBeenCalledWith({
          queryKey: queryKeys.galaxyImpact('account-one'),
        }),
      );
      await waitFor(() => expect(api.getGalaxyImpact).toHaveBeenCalledTimes(2));
      if (outcome === 'cancelled') {
        expect(importSignal?.aborted).toBe(true);
        expect(
          screen.getByText(
            'Import stopped. Saved events remain; re-import to continue.',
          ),
        ).toBeInTheDocument();
      }
    },
  );

  it('reports a batch that needs a retry without losing saved work', async () => {
    mockUpload(
      result({
        committedShas: [hash],
        failed: [{ files: ['bad.log'], reason: 'API 413' }],
        held: [{ name: 'bad.log', reason: 'Upload failed: API 413' }],
      }),
    );
    render(JournalAccountPanel);
    await selectFile();
    await fireEvent.click(
      screen.getByRole('button', { name: 'Import journals' }),
    );
    await screen.findByText(/need a retry/i);
    expect(screen.getByText(/bad.log: Upload failed/)).toBeInTheDocument();
  });

  it('explains why nothing was imported when every file is held', async () => {
    mockUpload(
      result({
        receipt: {
          import_ids: [],
          files_admitted: 0,
          files_skipped: 0,
          events_inserted: 0,
          duplicates_skipped: 0,
          held_files: [],
        },
        committedShas: [],
        held: [
          {
            name: 'huge.log',
            reason: 'File exceeds this importer’s per-file size limit',
          },
          {
            name: 'nocmdr.log',
            reason:
              'Commander identity record has an invalid timestamp; file held for review',
          },
          {
            name: 'empty.log',
            reason: 'No supported events with valid timestamps',
          },
        ],
      }),
    );
    render(JournalAccountPanel);
    await selectFile();
    await fireEvent.click(
      screen.getByRole('button', { name: 'Import journals' }),
    );
    await waitFor(() => {
      const alert = screen.getByRole('alert');
      expect(alert).toHaveTextContent('3');
      expect(alert).toHaveTextContent(/per-file size limit/);
      expect(alert).toHaveTextContent(/Commander/i);
    });
  });
});
