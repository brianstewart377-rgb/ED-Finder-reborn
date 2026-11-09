import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import axe from 'axe-core';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  getSystem,
  getSystemRatingExplanation,
  getSystemRatings,
  systemDetailFromV3,
} from '$lib/api/client';
import { queryClient, queryKeys } from '$lib/api/query';
import {
  ratingsIdentity,
  referenceRatingExplanation,
  referenceSystemDetail,
  referenceSystemRatings,
  v3Body,
  v3Ring,
  v3Station,
  v3System,
} from '$lib/api/v3-system-fixtures';
import { parseId64 } from '$lib/domain/id64';
import { buildSystemScene } from '$lib/spatial/system-scene';
import SystemDetailTestHost from './SystemDetailTestHost.svelte';

vi.mock('$lib/api/client', async (loadOriginal) => {
  const original = await loadOriginal<typeof import('$lib/api/client')>();
  return {
    ...original,
    getSystem: vi.fn(),
    getSystemRatings: vi.fn(),
    getSystemRatingExplanation: vi.fn(),
  };
});

vi.mock('$lib/spatial/system-scene', async (loadOriginal) => {
  const original =
    await loadOriginal<typeof import('$lib/spatial/system-scene')>();
  return { ...original, buildSystemScene: vi.fn(original.buildSystemScene) };
});

const mockedGetSystem = vi.mocked(getSystem);
const mockedGetRatings = vi.mocked(getSystemRatings);
const mockedGetExplanation = vi.mocked(getSystemRatingExplanation);
const mockedBuildSystemScene = vi.mocked(buildSystemScene);

/** Each child element's text, whitespace-normalised: one entry per column. */
const partsOf = (element: HTMLElement): string[] =>
  [...element.children].map(
    (child) => child.textContent?.replace(/\s+/gu, ' ').trim() ?? '',
  );

function detailValue(term: string): string | null {
  const dt = screen.getByText(term, { selector: 'dt' });
  return dt.parentElement?.querySelector('dd')?.textContent?.trim() ?? null;
}

async function openTab(
  name: 'Overview' | 'Bodies' | 'Stations',
): Promise<HTMLElement> {
  const tab = await screen.findByRole('tab', { name });
  await fireEvent.click(tab);
  return screen.getByRole('tabpanel', { name });
}

async function openSources(): Promise<HTMLElement> {
  await fireEvent.click(
    await screen.findByRole('button', { name: 'Sources & freshness' }),
  );
  return screen.findByRole('dialog', { name: 'Sources & freshness' });
}

function sourceRow(dialog: HTMLElement, product: string): HTMLElement {
  const row = dialog.querySelector(`[data-source-product="${product}"]`);
  expect(row).not.toBeNull();
  return row as HTMLElement;
}

describe('shared system detail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    queryClient.clear();
    mockedGetRatings.mockResolvedValue(referenceSystemRatings());
    mockedGetExplanation.mockResolvedValue(referenceRatingExplanation());
    // SystemDetail mounts the locator SpatialCanvas, whose runtime starts
    // asynchronously and observes its host element. jsdom has no
    // ResizeObserver; the longer ratings tests below give that start enough
    // time to run, so stub it as ExploreWorkspace.test.ts does.
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe = vi.fn();
        disconnect = vi.fn();
        unobserve = vi.fn();
      },
    );
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('links the three tasks to mounted panels and keeps only the active task keyboard-accessible', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });

    const tablist = await screen.findByRole('tablist', {
      name: 'Inspect sections',
    });
    const tabs = within(tablist).getAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent?.trim())).toEqual([
      'Overview',
      'Bodies',
      'Stations',
    ]);
    for (const [index, tab] of tabs.entries()) {
      const panel = document.getElementById(tab.getAttribute('aria-controls')!);
      expect(panel).toHaveAttribute('role', 'tabpanel');
      expect(panel).toHaveAttribute('aria-labelledby', tab.id);
      expect(tab).toHaveAttribute('aria-selected', String(index === 0));
      expect(tab).toHaveAttribute('tabindex', index === 0 ? '0' : '-1');
      if (index === 0) {
        expect(panel).toBeVisible();
        expect(panel).toHaveProperty('inert', false);
      } else {
        expect(panel).toHaveAttribute('hidden');
        expect(panel).toHaveProperty('inert', true);
      }
    }
    const overview = screen.getByRole('tabpanel', { name: 'Overview' });
    expect(within(overview).getByText('Coordinates')).toBeVisible();
    expect(
      await within(overview).findByRole('table', { name: 'Economy potential' }),
    ).toBeVisible();
    expect(
      within(overview).getByRole('button', { name: 'Sources & freshness' }),
    ).toBeVisible();
    expect(screen.queryByRole('list', { name: 'System bodies' })).toBeNull();

    const bodies = await openTab('Bodies');
    expect(
      within(bodies).getByRole('list', { name: 'System bodies' }),
    ).toBeVisible();
    expect(
      within(bodies).getByRole('complementary', { name: 'System map legend' }),
    ).toBeVisible();
    expect(overview).toHaveAttribute('hidden');
    expect(overview).toHaveProperty('inert', true);
    const stations = await openTab('Stations');
    expect(within(stations).getByText('Reference Port')).toBeVisible();
    expect(screen.getAllByRole('tabpanel', { hidden: true })).toHaveLength(3);
  });

  it('automatically activates arrow, Home and End navigation with roving focus and wraparound', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    const overview = await screen.findByRole('tab', { name: 'Overview' });
    overview.focus();
    for (const [key, name] of [
      ['ArrowRight', 'Bodies'],
      ['ArrowRight', 'Stations'],
      ['ArrowRight', 'Overview'],
      ['ArrowLeft', 'Stations'],
      ['Home', 'Overview'],
      ['End', 'Stations'],
    ] as const) {
      await fireEvent.keyDown(document.activeElement!, { key });
      const active = screen.getByRole('tab', { name });
      expect(active).toHaveFocus();
      expect(active).toHaveAttribute('aria-selected', 'true');
      expect(active).toHaveAttribute('tabindex', '0');
      expect(
        screen.getAllByRole('tab').filter((tab) => tab.tabIndex === 0),
      ).toEqual([active]);
      expect(screen.getByRole('tabpanel', { name })).toBeVisible();
    }
    await fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' });
    expect(screen.getByRole('tab', { name: 'Stations' })).toHaveFocus();
  });

  it('retains body selection, sources state and each panel scroll position without refetching on tab switches', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    await screen.findByRole('button', { name: 'Why? Agriculture' });
    const overview = screen.getByRole('tabpanel', { name: 'Overview' });
    overview.scrollTop = 110;
    const sources = await openSources();
    expect(sources).toHaveFocus();
    const bodies = await openTab('Bodies');
    expect(
      screen.queryByRole('dialog', { name: 'Sources & freshness' }),
    ).toBeNull();
    expect(screen.getByRole('tab', { name: 'Bodies' })).toHaveFocus();
    bodies.scrollTop = 225;
    const body = within(bodies).getByRole('button', {
      name: 'Reference System 3 Planet',
    });
    await fireEvent.click(body);
    const selectedCard = within(bodies).getByRole('article');
    expect(selectedCard).toHaveAttribute('data-selected-body-id', '3');
    const stations = await openTab('Stations');
    stations.scrollTop = 70;
    await openTab('Overview');
    expect(overview.scrollTop).toBe(110);
    expect(
      await screen.findByRole('dialog', { name: 'Sources & freshness' }),
    ).toBeVisible();
    await openTab('Bodies');
    expect(bodies.scrollTop).toBe(225);
    expect(within(bodies).getByRole('article')).toBe(selectedCard);
    expect(body).toHaveAttribute('aria-pressed', 'true');
    expect(selectedCard).toBeVisible();
    await openTab('Stations');
    expect(stations.scrollTop).toBe(70);
    expect(mockedGetSystem).toHaveBeenCalledTimes(1);
    expect(mockedGetRatings).toHaveBeenCalledTimes(1);
    expect(mockedGetExplanation).not.toHaveBeenCalled();
  });

  it.each(['Overview', 'Bodies', 'Stations'] as const)(
    'has no axe violations on the %s task',
    async (name) => {
      mockedGetSystem.mockResolvedValue(referenceSystemDetail());
      const { container } = render(SystemDetailTestHost, {
        props: { id64: parseId64('10477373803') },
      });
      await screen.findByRole('button', { name: 'Why? Agriculture' });
      await openTab(name);
      const result = await axe.run(container, {
        rules: {
          'color-contrast': { enabled: false },
          region: { enabled: false },
        },
      });
      expect(result.violations).toEqual([]);
    },
  );

  it('moves focus out of a panel hidden by an external tab change', async () => {
    const id64 = parseId64('10477373803');
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    const view = render(SystemDetailTestHost, {
      props: { id64, tab: 'bodies' },
    });
    const body = await screen.findByRole('button', {
      name: 'Reference System 3 Planet',
    });
    body.focus();
    expect(body).toHaveFocus();
    await view.rerender({ id64, tab: 'stations' });
    expect(screen.getByRole('tab', { name: 'Stations' })).toHaveFocus();
    expect(body.closest('[role="tabpanel"]')).toHaveAttribute('hidden');
    expect(body).not.toHaveFocus();
  });

  it('renders V3 canonical fields with an exact unsafe id64', async () => {
    const id64 = parseId64('9007199254740993');
    mockedGetSystem.mockResolvedValue(
      referenceSystemDetail({
        id64: '9007199254740993',
        name: 'V3 Lossless Reach',
      }),
    );

    const { container } = render(SystemDetailTestHost, { props: { id64 } });

    expect(
      await screen.findByRole('heading', { name: 'V3 Lossless Reach' }),
    ).toBeInTheDocument();
    expect(screen.getByText('9007199254740993')).toBeInTheDocument();
    expect(detailValue('Coordinates')).toBe('1.00, 2.00, 3.00 ly');
    expect(detailValue('Main star')).toBe('G2 V');
    expect(detailValue('Bodies returned')).toBe('3');
    expect(detailValue('Stations returned')).toBe('1');
    expect(detailValue('Catalogue updated')).not.toBeNull();
    // Product gaps are explicit and distinct from unknown source values.
    const unavailable = container.querySelectorAll(
      '[data-unavailable-fields][data-availability="not-integrated"]',
    );
    expect(unavailable).toHaveLength(2);
    for (const row of unavailable)
      expect(within(row as HTMLElement).getByText('Not integrated yet'));
    expect(
      detailValue('Population, economy, security, allegiance, government'),
    ).toBe('Not integrated yet');
    expect(detailValue('Development fit')).toBe('Not integrated yet');
    expect(container.textContent).not.toMatch(/undefined|null|NaN/u);
  });

  it('labels a system that is no longer current in the catalogue', async () => {
    mockedGetSystem.mockResolvedValue(
      referenceSystemDetail({ lifecycle_state: 'RETIRED' }),
    );
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    await screen.findByRole('heading', { name: 'Reference System' });
    expect(detailValue('Catalogue status')).toBe('Retired from the catalogue');
  });

  it('shows exactly the sources used with their own known dates and generation identities', async () => {
    const detail = referenceSystemDetail();
    const latestObserved = '2026-10-31T16:20:00Z';
    mockedGetSystem.mockResolvedValue({
      ...detail,
      canonicalGenerationId: 'canonical-generation-id',
      canonicalGenerationLabel: 'canonical-november',
      stations: [
        {
          ...detail.stations[0]!,
          lastObservedAt: latestObserved,
          economies: [
            {
              economy: 'extraction',
              weight: null,
              isPrimary: true,
              isSecondary: false,
            },
          ],
        },
        {
          ...detail.stations[0]!,
          stationKey: '128000001',
          name: 'Older Port',
          lastObservedAt: '2026-10-30T12:00:00Z',
        },
      ],
    });
    render(SystemDetailTestHost, { props: { id64: detail.id64 } });
    await screen.findByRole('button', { name: 'Why? Agriculture' });
    const dialog = await openSources();

    expect(
      [...dialog.querySelectorAll('[data-source-product]')].map((row) =>
        row.getAttribute('data-source-product'),
      ),
    ).toEqual(['canonical', 'ratings', 'station-observations', 'commander']);
    const canonical = sourceRow(dialog, 'canonical');
    expect(canonical).toHaveTextContent(
      'Bodies, stations list and coordinates',
    );
    expect(canonical).toHaveTextContent('canonical-november');
    expect(canonical).toHaveTextContent('canonical-generation-id');
    expect(canonical.querySelector('time')).toHaveAttribute(
      'datetime',
      detail.sourceUpdatedAt,
    );
    expect(within(canonical).getByText('Available')).toBeVisible();
    const ratings = sourceRow(dialog, 'ratings');
    expect(ratings).toHaveTextContent('Seven economy potentials');
    expect(ratings).toHaveTextContent(ratingsIdentity().derivedGenerationId);
    expect(ratings).toHaveTextContent('Publication 1 · Scorer 4.0.0');
    // The API supplies the published identity, but no publication timestamp.
    // Neither the canonical date nor the fetch time may stand in for it.
    expect(ratings).toHaveTextContent('Published: Unknown');
    expect(ratings.querySelector('time')).toBeNull();
    const observations = sourceRow(dialog, 'station-observations');
    expect(observations.querySelector('time')).toHaveAttribute(
      'datetime',
      latestObserved,
    );
    expect(within(observations).getByText('Available')).toBeVisible();
    expect(observations).toHaveTextContent('other stations may be older');
    const commander = sourceRow(dialog, 'commander');
    expect(commander).toHaveTextContent(
      'Population, economy, security, allegiance, government and Development fit',
    );
    expect(commander).toHaveTextContent('Not integrated yet');
    expect(commander).toHaveTextContent(
      'ED-Finder does not read these fields yet',
    );
    // Opening provenance reuses the detail and ratings already on screen.
    expect(mockedGetSystem).toHaveBeenCalledTimes(1);
    expect(mockedGetRatings).toHaveBeenCalledTimes(1);
    expect(mockedGetExplanation).not.toHaveBeenCalled();
  });

  it.each(['no stations', 'fleet carriers only'] as const)(
    'omits station observations when the page uses no station economies: %s',
    async (scenario) => {
      const detail = referenceSystemDetail();
      mockedGetSystem.mockResolvedValue({
        ...detail,
        stations:
          scenario === 'no stations'
            ? []
            : [{ ...detail.stations[0]!, stationType: 'fleet_carrier' }],
      });
      render(SystemDetailTestHost, { props: { id64: detail.id64 } });
      const dialog = await openSources();
      expect(
        [...dialog.querySelectorAll('[data-source-product]')].map((row) =>
          row.getAttribute('data-source-product'),
        ),
      ).toEqual(['canonical', 'ratings', 'commander']);
      expect(
        within(dialog).queryByText('Station economies / observations'),
      ).toBeNull();
    },
  );

  it('keeps unknown source values distinct from fields that are not integrated', async () => {
    mockedGetSystem.mockResolvedValue(
      referenceSystemDetail({ source_updated_at: null }),
    );
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    const dialog = await openSources();
    const canonical = sourceRow(dialog, 'canonical');
    expect(
      canonical.querySelector('[data-availability="unknown"]'),
    ).toHaveTextContent('Unknown');
    expect(canonical.querySelector('time')).toBeNull();
    const observations = sourceRow(dialog, 'station-observations');
    expect(
      observations.querySelector('[data-availability="unknown"]'),
    ).toHaveTextContent(
      'Unknown — the source has no economy value for these stations',
    );
    expect(within(observations).getByText('No observation yet')).toBeVisible();
    expect(observations.querySelector('time')).toBeNull();
    expect(
      sourceRow(dialog, 'commander').querySelector(
        '[data-availability="not-integrated"]',
      ),
    ).toHaveTextContent('Not integrated yet');
    expect(dialog).not.toHaveTextContent('Not available yet');
    for (const row of dialog.querySelectorAll('[data-unavailable-fields]')) {
      expect(['unknown', 'not-integrated', 'unavailable']).toContain(
        row.getAttribute('data-availability'),
      );
    }
  });

  it('keeps returned station economies available without inventing an observation date', async () => {
    const detail = referenceSystemDetail();
    mockedGetSystem.mockResolvedValue({
      ...detail,
      stations: [
        {
          ...detail.stations[0]!,
          economies: [
            {
              economy: 'extraction',
              weight: null,
              isPrimary: true,
              isSecondary: false,
            },
          ],
        },
      ],
    });
    render(SystemDetailTestHost, { props: { id64: detail.id64 } });
    const observations = sourceRow(await openSources(), 'station-observations');
    expect(within(observations).getByText('Available')).toBeVisible();
    expect(within(observations).getByText('No observation yet')).toBeVisible();
    expect(observations.querySelector('time')).toBeNull();
    await openTab('Stations');
    expect(screen.getByText('Extraction (primary)')).toBeVisible();
  });

  it('marks unrated systems Unknown in sources without fabricating zero potentials or offering Retry', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    mockedGetRatings.mockRejectedValueOnce(
      new ApiError(404, '/api/ratings/v4/systems/10477373803', {}),
    );
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    await screen.findByText(
      /the published source has no ratings for this system\./u,
    );
    const ratings = sourceRow(await openSources(), 'ratings');
    expect(ratings.querySelector('[data-unavailable-fields]')).toHaveAttribute(
      'data-availability',
      'unknown',
    );
    expect(within(ratings).getByText('Unknown')).toBeVisible();
    expect(within(ratings).queryByRole('button', { name: 'Retry' })).toBeNull();
    expect(document.querySelector('[data-rating-economy]')).toBeNull();
    expect(mockedGetRatings).toHaveBeenCalledTimes(1);
  });

  it('retries a temporarily unavailable ratings source once from the pop-out', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    mockedGetRatings
      .mockRejectedValueOnce(
        new ApiError(503, '/api/ratings/v4/systems/10477373803', {}),
      )
      .mockRejectedValueOnce(
        new ApiError(503, '/api/ratings/v4/systems/10477373803', {}),
      );
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    await screen.findByText(
      /economy ratings could not be loaded\./u,
      {},
      { timeout: 3_000 },
    );
    const dialog = await openSources();
    const ratings = sourceRow(dialog, 'ratings');
    expect(ratings.querySelector('[data-unavailable-fields]')).toHaveAttribute(
      'data-availability',
      'unavailable',
    );
    expect(within(ratings).getByText('Temporarily unavailable')).toBeVisible();
    expect(ratings).toHaveTextContent('the ratings request failed');
    const callsBeforeRetry = mockedGetRatings.mock.calls.length;

    await fireEvent.click(
      within(ratings).getByRole('button', { name: 'Retry' }),
    );

    await within(ratings).findByText('Available');
    expect(ratings).toHaveTextContent(ratingsIdentity().derivedGenerationId);
    expect(within(ratings).queryByRole('button', { name: 'Retry' })).toBeNull();
    expect(mockedGetRatings).toHaveBeenCalledTimes(callsBeforeRetry + 1);
    expect(mockedGetSystem).toHaveBeenCalledTimes(1);
  });

  it.each(['Escape', 'Close', 'outside', 'trigger'] as const)(
    'opens a non-modal sources dialog and closes on %s with focus restored',
    async (method) => {
      mockedGetSystem.mockResolvedValue(referenceSystemDetail());
      render(SystemDetailTestHost, {
        props: { id64: parseId64('10477373803') },
      });
      const trigger = await screen.findByRole('button', {
        name: 'Sources & freshness',
      });
      trigger.focus();
      const dialog = await openSources();
      expect(dialog).toHaveAttribute('aria-modal', 'false');
      expect(dialog).toHaveFocus();
      expect(trigger).toHaveAttribute('aria-expanded', 'true');
      if (method === 'Escape')
        await fireEvent.keyDown(window, { key: 'Escape' });
      else if (method === 'Close')
        await fireEvent.click(
          within(dialog).getByRole('button', { name: 'Close' }),
        );
      else if (method === 'outside') await fireEvent.click(document.body);
      else await fireEvent.click(trigger);
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(trigger).toHaveAttribute('aria-expanded', 'false');
      expect(trigger).toHaveFocus();
    },
  );

  it('has no axe violations with Sources & freshness open', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    await screen.findByRole('button', { name: 'Why? Agriculture' });
    const dialog = await openSources();
    const result = await axe.run(dialog, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });

  it('allows focus to leave the non-modal pop-out and still handles Escape first', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    const dialog = await openSources();
    const overview = screen.getByRole('tab', { name: 'Overview' });
    overview.focus();
    await fireEvent.keyDown(overview, { key: 'Tab' });
    expect(overview).toHaveFocus();
    expect(dialog).toBeVisible();

    await fireEvent.keyDown(overview, { key: 'Escape' });

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(
      screen.getByRole('button', { name: 'Sources & freshness' }),
    ).toHaveFocus();
  });

  it('clears the open sources and previous ratings identity when moving to a cached system', async () => {
    const first = parseId64('10477373803');
    const next = parseId64('42');
    const nextIdentity = ratingsIdentity({
      derivedGenerationId: 'next-generation',
      publicationSequence: '2',
    });
    let complete!: (ratings: ReturnType<typeof referenceSystemRatings>) => void;
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    mockedGetRatings.mockImplementation((id64) =>
      id64 === first
        ? Promise.resolve(referenceSystemRatings())
        : new Promise((resolve) => {
            complete = resolve;
          }),
    );
    queryClient.setQueryData(queryKeys.system(next), {
      ...referenceSystemDetail({ id64: next, name: 'Second System' }),
      canonicalGenerationLabel: 'second-canonical',
      stations: [],
    });
    const view = render(SystemDetailTestHost, { props: { id64: first } });
    await screen.findByRole('button', { name: 'Why? Agriculture' });
    const original = await openSources();
    expect(sourceRow(original, 'ratings')).toHaveTextContent(
      ratingsIdentity().derivedGenerationId,
    );

    await view.rerender({ id64: next });
    await screen.findByRole('heading', { name: 'Second System' });
    expect(screen.queryByRole('dialog')).toBeNull();
    const dialog = await openSources();
    expect(sourceRow(dialog, 'canonical')).toHaveTextContent(
      'second-canonical',
    );
    expect(sourceRow(dialog, 'ratings')).toHaveTextContent('Loading');
    expect(dialog).not.toHaveTextContent(ratingsIdentity().derivedGenerationId);
    expect(
      dialog.querySelector('[data-source-product="station-observations"]'),
    ).toBeNull();
    await waitFor(() =>
      expect(mockedGetRatings).toHaveBeenCalledWith(next, expect.anything()),
    );
    complete(referenceSystemRatings(nextIdentity));
    await waitFor(() =>
      expect(sourceRow(dialog, 'ratings')).toHaveTextContent('next-generation'),
    );
    expect(dialog).not.toHaveTextContent(ratingsIdentity().derivedGenerationId);
    const callsBeforeReopening = mockedGetRatings.mock.calls.length;
    await fireEvent.click(
      within(dialog).getByRole('button', { name: 'Close' }),
    );
    await openSources();
    expect(mockedGetRatings).toHaveBeenCalledTimes(callsBeforeReopening);
  });

  it.each(['Earth-like world', 'Unrecognised catalogue class', null])(
    'shows the raw planet class %s in the body list and selection pane',
    async (bodyClass) => {
      mockedGetSystem.mockResolvedValue(
        systemDetailFromV3(
          v3System(),
          [
            v3Body({
              name: 'Catalogued planet',
              body_type: 'planet',
              body_class: bodyClass,
              body_class_source:
                bodyClass == null ? null : 'ratings-v4-derived',
              spectral_class: null,
              luminosity_class: null,
            }),
          ],
          [],
          [],
        ),
      );
      render(SystemDetailTestHost, {
        props: { id64: parseId64('10477373803') },
      });

      await openTab('Bodies');
      const bodies = await screen.findByRole('list', { name: 'System bodies' });
      const label = bodyClass ?? 'Planet';
      const button = within(bodies).getByRole('button', {
        name: `Catalogued planet ${label}`,
      });
      await fireEvent.click(button);
      const card = await screen.findByRole('article');
      expect(
        within(card).getByText(label, { selector: 'dd' }),
      ).toBeInTheDocument();
      const source = within(card).queryByText('Ratings V4 derived catalogue');
      if (bodyClass == null) expect(source).toBeNull();
      else expect(source).toBeInTheDocument();
    },
  );

  it('keeps Inspect usable when the facade omits unknown coordinates', async () => {
    const id64 = parseId64('9007199254740993');
    mockedGetSystem.mockResolvedValue({
      ...referenceSystemDetail({ id64, name: 'Unknown position' }),
      x: undefined,
      y: undefined,
      z: undefined,
    });
    const { container } = render(SystemDetailTestHost, { props: { id64 } });

    expect(
      await screen.findByRole('heading', { name: 'Unknown position' }),
    ).toBeInTheDocument();
    expect(screen.getByText(id64)).toBeInTheDocument();
    expect(container.querySelector('[data-system-id64]')).toHaveAttribute(
      'data-system-id64',
      id64,
    );
    expect(detailValue('Coordinates')).toBe('Unknown');
    expect(
      screen.getByText('Coordinates', { selector: 'dt' }).parentElement,
    ).toHaveAttribute('data-availability', 'unknown');
    expect(detailValue('Main star')).toBe('G2 V');
    expect(container.textContent).not.toMatch(
      /undefined|null|NaN|0\.00, 0\.00, 0\.00/u,
    );

    await openTab('Bodies');
    const bodies = screen.getByRole('list', { name: 'System bodies' });
    await fireEvent.click(
      within(bodies).getByRole('button', { name: 'Reference System 3 Planet' }),
    );
    expect(await screen.findByRole('article')).toHaveAttribute(
      'data-selected-body-id',
      '3',
    );
  });

  it('flags an incomplete body catalogue but not a lagging source total', async () => {
    mockedGetSystem.mockResolvedValueOnce(
      referenceSystemDetail({ source_body_count: 5 }),
    );
    const first = render(SystemDetailTestHost, {
      props: { id64: parseId64('10477373803') },
    });
    await screen.findByRole('heading', { name: 'Reference System' });
    expect(detailValue('Bodies returned')).toBe('3 of 5 known');
    first.unmount();

    queryClient.clear();
    mockedGetSystem.mockResolvedValueOnce(
      referenceSystemDetail({ source_body_count: 2 }),
    );
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    await screen.findByRole('heading', { name: 'Reference System' });
    expect(detailValue('Bodies returned')).toBe('3');
  });

  it('lists selectable bodies from V3 and shows their catalogued facts', async () => {
    const id64 = parseId64('10477373803');
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());

    render(SystemDetailTestHost, { props: { id64 } });

    await openTab('Bodies');
    const list = await screen.findByRole('list', { name: 'System bodies' });
    const buttons = within(list).getAllByRole('button');
    // The body without a Frontier BodyID is counted but not selectable.
    expect(buttons.map((button) => button.textContent?.trim())).toEqual([
      'Reference System Star · G2 V',
      'Reference System 3 Planet',
    ]);
    expect(
      screen.getByRole('list', { name: 'Bodies without a map identity' }),
    ).toHaveTextContent('Reference System 3 a');
    await fireEvent.click(buttons[1] as HTMLElement);
    const card = await screen.findByRole('article');
    expect(card).toHaveAttribute('data-selected-body-id', '3');
    expect(within(card).getByText('Planet')).toBeInTheDocument();
    expect(within(card).getByText('11,450 ls')).toBeInTheDocument();
    expect(within(card).getByText('70,000 km')).toBeInTheDocument();
    expect(within(card).getByText('1 known')).toBeInTheDocument();
    expect(within(card).getByText('No')).toBeInTheDocument();
    expect(
      within(card).getByText('2 biological · 1 geological'),
    ).toBeInTheDocument();

    await fireEvent.click(buttons[0] as HTMLElement);
    const star = await screen.findByRole('article');
    expect(star).toHaveAttribute('data-selected-body-id', '0');
    expect(within(star).getByText('Star · G2 V')).toBeInTheDocument();
    expect(within(star).getByText('Belts')).toBeInTheDocument();
    expect(within(star).getAllByText('None recorded')).toHaveLength(2);
  });

  it('explains the hierarchy schematic and lists unknown positions without changing selection', async () => {
    mockedGetSystem.mockResolvedValue(
      systemDetailFromV3(
        v3System(),
        [
          v3Body(),
          v3Body({
            body_id: '10',
            frontier_body_id: '1',
            direct_parent_body_id: '36028797018963968',
            name: 'Known planet',
            body_type: 'planet',
            is_main_star: false,
            spectral_class: null,
            luminosity_class: null,
            semi_major_axis_km: 150_000_000,
            distance_from_arrival_ls: 500,
          }),
          v3Body({
            body_id: '11',
            frontier_body_id: '2',
            direct_parent_body_id: '10',
            name: 'Moon without distance',
            body_type: 'planet',
            is_main_star: false,
            spectral_class: null,
            luminosity_class: null,
            semi_major_axis_km: null,
            distance_from_arrival_ls: null,
          }),
          v3Body({
            body_id: '12',
            frontier_body_id: '3',
            direct_parent_body_id: '999',
            name: 'Body without parent',
            body_type: 'planet',
            is_main_star: false,
            spectral_class: null,
            luminosity_class: null,
            semi_major_axis_km: 50_000,
            distance_from_arrival_ls: 750,
          }),
        ],
        [],
        [],
      ),
    );
    const { container } = render(SystemDetailTestHost, {
      props: { id64: parseId64('10477373803') },
    });

    await openTab('Bodies');
    const legend = await screen.findByRole('complementary', {
      name: 'System map legend',
    });
    expect(container.querySelector('.system-map-truth-note')).toHaveTextContent(
      'schematic; order by known orbital distance; moons grouped by known parent',
    );
    const unplaced = within(legend).getByRole('list', {
      name: 'Unplaced bodies',
    });
    const entries = within(unplaced).getAllByRole('listitem');
    expect(entries).toHaveLength(2);
    expect(unplaced).toHaveTextContent('Moon without distance');
    expect(unplaced).toHaveTextContent('Body without parent');
    expect(unplaced).not.toHaveTextContent('Known planet');
    expect(unplaced).not.toHaveTextContent('Reference System');
    for (const entry of entries) {
      expect(entry).toHaveTextContent('position unknown');
    }
    expect(
      container.querySelector('[data-unplaced-body-id="3"]'),
    ).toHaveTextContent('Unattached group');
    expect(
      container.querySelector('[data-unplaced-body-id="2"]'),
    ).not.toHaveTextContent('Unattached group');

    await openTab('Bodies');
    const bodies = screen.getByRole('list', { name: 'System bodies' });
    await fireEvent.click(
      within(bodies).getByRole('button', {
        name: 'Body without parent Planet',
      }),
    );
    expect(await screen.findByRole('article')).toHaveAttribute(
      'data-selected-body-id',
      '3',
    );
  });

  it('clears the selected body and every scene input when the next system is cached', async () => {
    const id64 = parseId64('10477373803');
    const next = parseId64('42');
    const firstSystem = referenceSystemDetail();
    const secondSystem = referenceSystemDetail({
      id64: next,
      name: 'Second System',
    });
    mockedGetSystem.mockResolvedValue(firstSystem);
    queryClient.setQueryData(queryKeys.system(next), secondSystem);
    queryClient.setQueryData(
      queryKeys.systemRatings(next),
      referenceSystemRatings(),
    );
    const view = render(SystemDetailTestHost, { props: { id64 } });
    await openTab('Bodies');
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Reference System 3 Planet' }),
    );
    expect(screen.getByRole('article')).toHaveAttribute(
      'data-selected-body-id',
      '3',
    );
    expect(mockedBuildSystemScene).toHaveBeenLastCalledWith(
      firstSystem,
      expect.any(Number),
      3,
    );
    mockedBuildSystemScene.mockClear();

    await view.rerender({ id64: next });
    await screen.findByRole('heading', { name: 'Second System' });

    expect(screen.queryByRole('article')).toBeNull();
    await openTab('Bodies');
    const bodies = screen.getByRole('list', { name: 'System bodies' });
    for (const button of within(bodies).getAllByRole('button')) {
      expect(button).toHaveAttribute('aria-pressed', 'false');
    }
    expect(mockedBuildSystemScene).toHaveBeenCalledWith(
      secondSystem,
      expect.any(Number),
      undefined,
    );
    // Inspect every call, including any render before the new query settles.
    expect(
      mockedBuildSystemScene.mock.calls.every(
        ([, , selectedBodyId]) => selectedBodyId === undefined,
      ),
    ).toBe(true);
  });

  it('clears the selected body and scene input while another system loads and after it resolves', async () => {
    const id64 = parseId64('10477373803');
    const next = parseId64('42');
    const secondSystem = referenceSystemDetail({
      id64: next,
      name: 'Second System',
    });
    let complete!: (system: typeof secondSystem) => void;
    const secondResponse = new Promise<typeof secondSystem>((resolve) => {
      complete = resolve;
    });
    mockedGetSystem.mockImplementation((requestedId) =>
      requestedId === next
        ? secondResponse
        : Promise.resolve(referenceSystemDetail()),
    );
    const view = render(SystemDetailTestHost, { props: { id64 } });
    await openTab('Bodies');
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Reference System 3 Planet' }),
    );
    expect(screen.getByRole('article')).toHaveAttribute(
      'data-selected-body-id',
      '3',
    );
    mockedBuildSystemScene.mockClear();

    await view.rerender({ id64: next });
    await screen.findByRole('heading', { name: 'Loading system…' });
    await waitFor(() =>
      expect(mockedGetSystem).toHaveBeenCalledWith(next, expect.anything()),
    );
    expect(screen.queryByRole('article')).toBeNull();
    expect(
      view.container
        .querySelector('.system-detail > [aria-live="polite"]')
        ?.textContent?.trim(),
    ).toBe('');
    expect(
      mockedBuildSystemScene.mock.calls.every(
        ([, , selectedBodyId]) => selectedBodyId === undefined,
      ),
    ).toBe(true);

    complete(secondSystem);
    await screen.findByRole('heading', { name: 'Second System' });

    expect(screen.queryByRole('article')).toBeNull();
    expect(mockedBuildSystemScene).toHaveBeenCalledWith(
      secondSystem,
      expect.any(Number),
      undefined,
    );
    expect(
      mockedBuildSystemScene.mock.calls.every(
        ([, , selectedBodyId]) => selectedBodyId === undefined,
      ),
    ).toBe(true);
  });

  it.each(['invalidate', 'refetch'] as const)(
    'keeps the selected body through a same-system %s',
    async (refresh) => {
      const id64 = parseId64('10477373803');
      const refreshedDetail = referenceSystemDetail({
        name: 'Refreshed System',
      });
      const refreshedSystem = {
        ...refreshedDetail,
        bodies: refreshedDetail.bodies.map((body) =>
          body.id === 3 ? { ...body, name: 'Updated body name' } : body,
        ),
      };
      let complete!: (system: typeof refreshedSystem) => void;
      mockedGetSystem
        .mockResolvedValueOnce(referenceSystemDetail())
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              complete = resolve;
            }),
        );
      const view = render(SystemDetailTestHost, { props: { id64 } });
      await openTab('Bodies');
      await fireEvent.click(
        await screen.findByRole('button', {
          name: 'Reference System 3 Planet',
        }),
      );
      const options = { queryKey: queryKeys.system(id64), exact: true };
      const refreshed =
        refresh === 'invalidate'
          ? queryClient.invalidateQueries(options)
          : queryClient.refetchQueries(options);
      await waitFor(() => expect(mockedGetSystem).toHaveBeenCalledTimes(2));
      expect(screen.getByRole('article')).toHaveAttribute(
        'data-selected-body-id',
        '3',
      );

      complete(refreshedSystem);
      await refreshed;
      await screen.findByRole('heading', { name: 'Refreshed System' });

      expect(screen.getByRole('article')).toHaveAttribute(
        'data-selected-body-id',
        '3',
      );
      expect(
        screen.getByRole('button', { name: 'Updated body name Planet' }),
      ).toHaveAttribute('aria-pressed', 'true');
      expect(
        within(screen.getByRole('article')).getByRole('heading', {
          name: 'Updated body name',
        }),
      ).toBeInTheDocument();
      expect(
        view.container
          .querySelector('.system-detail > [aria-live="polite"]')
          ?.textContent?.trim(),
      ).toBe('Selected Reference System 3');
      expect(mockedBuildSystemScene).toHaveBeenLastCalledWith(
        refreshedSystem,
        expect.any(Number),
        3,
      );
    },
  );

  it('does not restore a body selection when returning to a previously visited system', async () => {
    const id64 = parseId64('10477373803');
    const next = parseId64('42');
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    queryClient.setQueryData(
      queryKeys.system(next),
      referenceSystemDetail({ id64: next, name: 'Second System' }),
    );
    const view = render(SystemDetailTestHost, { props: { id64 } });
    await openTab('Bodies');
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Reference System 3 Planet' }),
    );
    expect(screen.getByRole('article')).toHaveAttribute(
      'data-selected-body-id',
      '3',
    );
    mockedBuildSystemScene.mockClear();

    await view.rerender({ id64: next });
    await screen.findByRole('heading', { name: 'Second System' });
    expect(screen.queryByRole('article')).toBeNull();

    await view.rerender({ id64 });
    await screen.findByRole('heading', { name: 'Reference System' });

    expect(screen.queryByRole('article')).toBeNull();
    expect(
      screen.getByRole('button', { name: 'Reference System 3 Planet' }),
    ).toHaveAttribute('aria-pressed', 'false');
    expect(
      mockedBuildSystemScene.mock.calls.every(
        ([, , selectedBodyId]) => selectedBodyId === undefined,
      ),
    ).toBe(true);
  });

  it('announces only the selected body name and silently clears the announcement on a system change', async () => {
    const id64 = parseId64('10477373803');
    const next = parseId64('42');
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    queryClient.setQueryData(
      queryKeys.system(next),
      referenceSystemDetail({ id64: next, name: 'Second System' }),
    );
    const view = render(SystemDetailTestHost, { props: { id64 } });
    await screen.findByRole('heading', { name: 'Reference System' });
    // SpatialCanvas has its own renderer-status live region. Body selection
    // gets exactly one persistent, visually hidden region owned by Inspect.
    const announcements = view.container.querySelectorAll(
      '.system-detail > [aria-live="polite"]',
    );
    expect(announcements).toHaveLength(1);
    const announcement = announcements[0] as HTMLElement;
    expect(announcement).toHaveClass('system-detail-announcement');
    expect(announcement.textContent?.trim()).toBe('');

    await openTab('Bodies');
    await fireEvent.click(
      screen.getByRole('button', { name: 'Reference System 3 Planet' }),
    );
    expect(announcement.textContent?.trim()).toBe(
      'Selected Reference System 3',
    );
    const card = screen.getByRole('article');
    expect(card).toHaveAttribute('data-selected-body-id', '3');
    expect(card).not.toHaveAttribute('aria-live');

    await fireEvent.click(
      screen.getByRole('button', { name: 'Reference System Star · G2 V' }),
    );
    expect(announcement.textContent?.trim()).toBe('Selected Reference System');

    await view.rerender({ id64: next });
    await screen.findByRole('heading', { name: 'Second System' });

    expect(announcement).toBeInTheDocument();
    expect(announcement.textContent?.trim()).toBe('');
    expect(screen.queryByRole('article')).toBeNull();
  });

  it('hides the selection announcement visually through a global rule', () => {
    // The announcement uses a global style; a class missing from app.css
    // would render the announcement as visible page text.
    const css = readFileSync(resolve(process.cwd(), 'src/app.css'), 'utf8');
    const rule = /\.system-detail-announcement\s*\{([^}]*)\}/.exec(css)?.[1];
    expect(rule).toBeDefined();
    expect(rule).toMatch(/position:\s*absolute/);
    expect(rule).toMatch(/width:\s*1px/);
    expect(rule).toMatch(/height:\s*1px/);
    expect(rule).toMatch(/overflow:\s*hidden/);
    expect(rule).toMatch(/clip-path:\s*inset\(50%\)/);
  });

  it('treats 404 and out-of-range 422 as not found and retries other API errors', async () => {
    const id64 = parseId64('42');
    mockedGetSystem.mockRejectedValueOnce(
      new ApiError(404, '/api/v1/systems/42', {}),
    );
    const first = render(SystemDetailTestHost, { props: { id64 } });
    expect(
      await screen.findByRole('heading', { name: 'System not found' }),
    ).toBeInTheDocument();
    first.unmount();
    expect(mockedGetSystem).toHaveBeenCalledTimes(1);

    queryClient.clear();
    const huge = parseId64('18446744073709551615');
    mockedGetSystem.mockRejectedValueOnce(
      new ApiError(422, '/api/v1/systems/18446744073709551615', {}),
    );
    const second = render(SystemDetailTestHost, { props: { id64: huge } });
    expect(
      await screen.findByRole('heading', { name: 'System not found' }),
    ).toBeInTheDocument();
    second.unmount();

    queryClient.clear();
    mockedGetSystem
      .mockRejectedValueOnce(new ApiError(503, '/api/v1/systems/42', {}))
      .mockRejectedValueOnce(new ApiError(503, '/api/v1/systems/42', {}))
      .mockResolvedValueOnce(
        referenceSystemDetail({ id64: '42', name: 'Recovered system' }),
      );
    render(SystemDetailTestHost, { props: { id64 } });
    expect(
      await screen.findByRole(
        'heading',
        { name: 'System detail unavailable' },
        { timeout: 3_000 },
      ),
    ).toBeInTheDocument();
    const failure = screen.getByRole('alert');
    expect(failure).toHaveAttribute('data-unavailable-fields');
    expect(failure).toHaveAttribute('data-availability', 'unavailable');
    expect(within(failure).getByText('Temporarily unavailable')).toBeVisible();
    const callsBeforeRetry = mockedGetSystem.mock.calls.length;
    await fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: 'Recovered system' }),
      ).toBeInTheDocument(),
    );
    expect(mockedGetSystem).toHaveBeenCalledTimes(callsBeforeRetry + 1);
  });
  it('lists each ring and belt with its type, reserves and mining hotspots', async () => {
    mockedGetSystem.mockResolvedValue(
      systemDetailFromV3(
        v3System(),
        [
          v3Body(),
          v3Body({
            body_id: '108086391056891904',
            frontier_body_id: '3',
            name: 'Reference System 3',
            body_type: 'planet',
            is_main_star: null,
            spectral_class: null,
            luminosity_class: null,
            distance_from_arrival_ls: 11_450,
          }),
        ],
        [
          v3Ring({
            ring_type: 'metallic',
            reserve_type: 'pristine',
            signals: [
              { signal_type: 'painite', count: 1 },
              { signal_type: 'platinum', count: 2 },
            ],
          }),
          v3Ring({
            ring_id: '900719925632971231',
            name: 'Reference System 3 B Ring',
            ring_type: 'icy',
            reserve_type: null,
            signals_complete: null,
          }),
          v3Ring({
            ring_id: '900719925632971232',
            kind: 'BELT',
            name: 'Reference System 3 A Belt',
            ring_type: 'rocky',
            reserve_type: null,
          }),
        ],
        [],
      ),
    );
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });

    await openTab('Bodies');
    const list = await screen.findByRole('list', { name: 'System bodies' });
    await fireEvent.click(
      within(list).getByRole('button', { name: /Reference System 3/u }),
    );
    const card = await screen.findByRole('article');
    const rings = within(card).getAllByRole('listitem');
    expect(rings.map(partsOf)).toEqual([
      [
        'Reference System 3 A Ring',
        'Metallic · Pristine reserves',
        'Hotspots: 2 Platinum · 1 Painite',
      ],
      ['Reference System 3 B Ring', 'Icy', 'Hotspots not recorded'],
      // A recorded, empty signal list is a real "none", not a gap.
      ['Reference System 3 A Belt', 'Rocky', 'No hotspots'],
    ]);
  });

  it('lists stations with their type, distance, body and economies', async () => {
    mockedGetSystem.mockResolvedValue(
      systemDetailFromV3(
        v3System(),
        [
          v3Body(),
          v3Body({
            body_id: '108086391056891904',
            frontier_body_id: '3',
            name: 'Reference System 3',
            body_type: 'planet',
            is_main_star: null,
            distance_from_arrival_ls: 11_450,
          }),
        ],
        [],
        [
          v3Station({
            station_id: '128000002',
            name: 'Far Outpost',
            station_type: 'outpost',
            distance_from_arrival_ls: 9_000,
          }),
          v3Station({
            station_id: '128000003',
            name: 'K7Q-1XZ',
            station_type: 'fleet_carrier',
            distance_from_arrival_ls: 12,
          }),
          v3Station({
            station_type: 'coriolis_starport',
            body_id: '108086391056891904',
            association_state: 'BODY_RESOLVED',
            economies: [
              {
                economy: 'refinery',
                economy_weight: 0.3,
                is_primary: false,
                is_secondary: true,
              },
              {
                economy: 'extraction',
                economy_weight: 0.7,
                is_primary: true,
                is_secondary: false,
              },
            ],
          }),
        ],
      ),
    );
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });

    await openTab('Stations');
    const heading = await screen.findByRole('heading', { name: 'Stations' });
    const section = heading.closest('section') as HTMLElement;
    // The first list is the fixed ports; carriers follow inside <details>.
    const ports = section.querySelector('ul') as HTMLElement;
    // Nearest first; the far outpost's placement and economy are unknown.
    expect(within(ports).getAllByRole('listitem').map(partsOf)).toEqual([
      [
        'Reference Port',
        'Coriolis Starport · 500 ls · Reference System 3',
        'Extraction 70% (primary) · Refinery 30% (secondary)',
      ],
      ['Far Outpost', 'Outpost · 9,000 ls', 'Economy: Unknown'],
    ]);
    // Fleet carriers are listed apart, behind a closed toggle.
    const carriers = section.querySelector(
      'details.station-carriers',
    ) as HTMLElement;
    expect(carriers.hasAttribute('open')).toBe(false);
    expect(carriers.querySelector('summary')?.textContent?.trim()).toBe(
      '1 fleet carrier',
    );
    expect(
      [...carriers.querySelectorAll('li')].map((item) => partsOf(item)),
    ).toEqual([['K7Q-1XZ', 'Fleet Carrier · 12 ls']]);
  });

  it('shows Ratings V4 potentials, highest first, without fetching an explanation', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });

    const heading = await screen.findByRole('heading', {
      name: 'Economy potential',
    });
    const section = heading.closest('section') as HTMLElement;
    await waitFor(() =>
      expect(section.querySelectorAll('[data-rating-economy]')).toHaveLength(7),
    );
    const economies = [
      ...section.querySelectorAll('[data-rating-economy]'),
    ].map((row) => row.getAttribute('data-rating-economy'));
    expect(economies).toEqual([
      'Agriculture',
      'Tourism',
      'HighTech',
      'Extraction',
      'Industrial',
      'Refinery',
      'Military',
    ]);
    const first = section.querySelector('[data-rating-economy="Agriculture"]');
    // Economy, potential, confidence, explanation control.
    expect(partsOf(first as HTMLElement)).toEqual([
      'Agriculture',
      '84',
      '72%',
      'Why?',
    ]);
    expect(
      within(section).getByRole('table', { name: 'Economy potential' }),
    ).toBeInTheDocument();
    expect(mockedGetRatings).toHaveBeenCalledWith(
      '10477373803',
      expect.anything(),
    );
    expect(mockedGetExplanation).not.toHaveBeenCalled();
    expect(section.textContent).not.toMatch(/undefined|null|NaN/u);
  });

  it.each(['Escape', 'Close', 'trigger', 'outside'] as const)(
    'opens non-modal evidence and closes on %s with focus restored to its Why button',
    async (method) => {
      mockedGetSystem.mockResolvedValue(referenceSystemDetail());
      render(SystemDetailTestHost, {
        props: { id64: parseId64('10477373803') },
      });
      const why = await screen.findByRole('button', {
        name: 'Why? Agriculture',
      });
      why.focus();
      await fireEvent.click(why);
      const dialog = await screen.findByRole('dialog', {
        name: 'Why Agriculture scores 84',
      });
      expect(dialog).toHaveAttribute('aria-modal', 'false');
      expect(dialog).toHaveFocus();
      expect(why).toHaveAttribute('aria-expanded', 'true');
      expect(why).toHaveAttribute('aria-controls', dialog.id);
      await within(dialog).findByRole('table', {
        name: 'Bodies that make up this score',
      });
      if (method === 'Escape') {
        const tab = screen.getByRole('tab', { name: 'Overview' });
        tab.focus();
        expect(tab).toHaveFocus();
        await fireEvent.keyDown(tab, { key: 'Escape' });
      } else if (method === 'Close') {
        await fireEvent.click(
          within(dialog).getByRole('button', { name: 'Close' }),
        );
      } else if (method === 'trigger') {
        await fireEvent.click(why);
      } else {
        await fireEvent.click(document.body);
      }
      expect(
        screen.queryByRole('dialog', { name: 'Why Agriculture scores 84' }),
      ).toBeNull();
      expect(why).toHaveAttribute('aria-expanded', 'false');
      expect(why).toHaveFocus();
      await fireEvent.click(why);
      await screen.findByRole('dialog', { name: 'Why Agriculture scores 84' });
      expect(mockedGetExplanation).toHaveBeenCalledTimes(1);
    },
  );

  it('shows evidence for one economy at a time and returns focus to the latest opener', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    const agriculture = await screen.findByRole('button', {
      name: 'Why? Agriculture',
    });
    await fireEvent.click(agriculture);
    const first = await screen.findByRole('dialog', {
      name: 'Why Agriculture scores 84',
    });
    await within(first).findByRole('table', {
      name: 'Bodies that make up this score',
    });
    const tourism = screen.getByRole('button', { name: 'Why? Tourism' });
    await fireEvent.click(tourism);
    const current = await screen.findByRole('dialog', {
      name: 'Why Tourism scores 70',
    });
    expect(screen.getAllByRole('dialog')).toHaveLength(1);
    expect(agriculture).toHaveAttribute('aria-expanded', 'false');
    expect(tourism).toHaveAttribute('aria-expanded', 'true');
    expect(current).not.toHaveTextContent('Native (water world)');
    expect(current).not.toHaveTextContent('Why Agriculture scores 84');
    expect(mockedGetExplanation).toHaveBeenCalledTimes(1);
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(tourism).toHaveFocus();
  });

  it('picks out only the economy row whose evidence is open', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    const rows = () =>
      [...document.querySelectorAll('tr[data-rating-economy]')].filter((row) =>
        row.classList.contains('open'),
      );
    await screen.findByRole('button', { name: 'Why? Agriculture' });
    expect(rows()).toHaveLength(0);
    await fireEvent.click(screen.getByRole('button', { name: 'Why? Tourism' }));
    expect(
      rows().map((row) => row.getAttribute('data-rating-economy')),
    ).toEqual(['Tourism']);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Why? Agriculture' }),
    );
    expect(
      rows().map((row) => row.getAttribute('data-rating-economy')),
    ).toEqual(['Agriculture']);
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(rows()).toHaveLength(0);
  });

  it('retains an open evidence selection across tabs without refetching', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Why? Agriculture' }),
    );
    const dialog = await screen.findByRole('dialog', {
      name: 'Why Agriculture scores 84',
    });
    await within(dialog).findByRole('table', {
      name: 'Bodies that make up this score',
    });
    await openTab('Bodies');
    expect(screen.queryByRole('dialog')).toBeNull();
    await openTab('Stations');
    await openTab('Overview');
    const restored = await screen.findByRole('dialog', {
      name: 'Why Agriculture scores 84',
    });
    expect(
      within(restored).getByRole('table', {
        name: 'Bodies that make up this score',
      }),
    ).toBeVisible();
    expect(mockedGetSystem).toHaveBeenCalledTimes(1);
    expect(mockedGetRatings).toHaveBeenCalledTimes(1);
    expect(mockedGetExplanation).toHaveBeenCalledTimes(1);
  });

  it('dismisses only the topmost tool when evidence and Sources are both open', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    const why = await screen.findByRole('button', { name: 'Why? Agriculture' });
    await openSources();
    await fireEvent.click(why);
    await screen.findByRole('dialog', { name: 'Why Agriculture scores 84' });
    expect(screen.getAllByRole('dialog')).toHaveLength(2);
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(
      screen.queryByRole('dialog', { name: 'Why Agriculture scores 84' }),
    ).toBeNull();
    expect(
      screen.getByRole('dialog', { name: 'Sources & freshness' }),
    ).toBeVisible();
    expect(why).toHaveFocus();
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(
      screen.getByRole('button', { name: 'Sources & freshness' }),
    ).toHaveFocus();
  });

  it('has no axe violations with evidence open and after closing it', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    const { container } = render(SystemDetailTestHost, {
      props: { id64: parseId64('10477373803') },
    });
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Why? Agriculture' }),
    );
    const dialog = await screen.findByRole('dialog', {
      name: 'Why Agriculture scores 84',
    });
    await within(dialog).findByRole('table', {
      name: 'Bodies that make up this score',
    });
    const options = {
      rules: {
        'color-contrast': { enabled: false },
        region: { enabled: false },
      },
    };
    expect((await axe.run(container, options)).violations).toEqual([]);
    await fireEvent.click(
      within(dialog).getByRole('button', { name: 'Close' }),
    );
    expect((await axe.run(container, options)).violations).toEqual([]);
  });

  it('explains a rating on demand with body names, links and unknowns', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });

    const why = await screen.findByRole('button', { name: 'Why? Agriculture' });
    expect(why).toHaveAttribute('aria-expanded', 'false');
    await fireEvent.click(why);
    expect(why).toHaveAttribute('aria-expanded', 'true');

    const region = await screen.findByRole('dialog', {
      name: 'Why Agriculture scores 84',
    });
    const table = await within(region).findByRole('table', {
      name: 'Bodies that make up this score',
    });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(
      rows.map((row) => {
        const [body, role, score, weight] = partsOf(row);
        return [body, score, weight, role?.startsWith('Native') ?? false];
      }),
    ).toEqual([
      ['Reference System 3', '95', '82%', true],
      ['Reference System 3 a', '55', '11%', false],
    ]);
    for (const text of [
      'Native (water world)',
      'Base 75; +20 Biological signals, Water world',
      'Modifier',
      'Base 55; +10 Biological signals; −10 Icy body',
    ])
      expect(within(table).getByText(text)).toBeInTheDocument();
    expect(
      within(region).getByText('Reference System 3 a: reserve level'),
    ).toBeInTheDocument();
    expect(
      within(region).getByText('Scorer 4.0.0 · mechanics v4-mechanics-2026-09'),
    ).toBeInTheDocument();
    // The explanation is requested for the exact ratings on screen.
    expect(mockedGetExplanation).toHaveBeenCalledWith(
      '10477373803',
      ratingsIdentity(),
      expect.anything(),
    );

    await fireEvent.click(why);
    expect(why).toHaveAttribute('aria-expanded', 'false');
    expect(region).not.toBeVisible();
  });

  it.each([404, 422])(
    'marks an absent explanation Unknown for a %s response',
    async (status) => {
      mockedGetSystem.mockResolvedValue(referenceSystemDetail());
      mockedGetExplanation.mockRejectedValueOnce(
        new ApiError(
          status,
          '/api/ratings/v4/systems/10477373803/explanation',
          {},
        ),
      );
      render(SystemDetailTestHost, {
        props: { id64: parseId64('10477373803') },
      });
      await fireEvent.click(
        await screen.findByRole('button', { name: 'Why? Agriculture' }),
      );
      const region = await screen.findByRole('dialog', {
        name: 'Why Agriculture scores 84',
      });
      await within(region).findByText('Unknown');
      expect(region.querySelector('[data-unavailable-fields]')).toHaveAttribute(
        'data-availability',
        'unknown',
      );
      expect(region).toHaveTextContent(
        'the published source has no explanation for this system',
      );
      expect(
        within(region).queryByRole('button', { name: 'Retry' }),
      ).toBeNull();
      expect(mockedGetExplanation).toHaveBeenCalledTimes(1);
    },
  );

  it('does not carry an open explanation over to the next system', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    // The next system is already cached, as after Back/Forward, so Inspect
    // keeps the ratings component mounted across the change.
    const next = parseId64('42');
    queryClient.setQueryData(
      queryKeys.system(next),
      referenceSystemDetail({ id64: '42', name: 'Second System' }),
    );
    const view = render(SystemDetailTestHost, {
      props: { id64: parseId64('10477373803') },
    });
    await fireEvent.click(
      await screen.findByRole('button', { name: 'Why? Agriculture' }),
    );
    await screen.findByRole('dialog', { name: 'Why Agriculture scores 84' });
    expect(mockedGetExplanation).toHaveBeenCalledTimes(1);

    await view.rerender({ id64: next });
    await screen.findByRole('heading', { name: 'Second System' });
    await waitFor(() =>
      expect(mockedGetRatings).toHaveBeenCalledWith('42', expect.anything()),
    );
    const why = await screen.findByRole('button', {
      name: 'Why? Agriculture',
    });
    expect(why).toHaveAttribute('aria-expanded', 'false');
    expect(mockedGetExplanation).toHaveBeenCalledTimes(1);
  });

  it('never shows an explanation from a different publication', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    mockedGetExplanation.mockResolvedValueOnce({
      kind: 'republished',
      identity: ratingsIdentity({ publicationSequence: '2' }),
    });
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });

    await fireEvent.click(
      await screen.findByRole('button', { name: 'Why? Agriculture' }),
    );
    const region = await screen.findByRole('dialog', {
      name: 'Why Agriculture scores 84',
    });
    expect(
      await within(region).findByText(
        'The ratings were republished while this page was open.',
      ),
    ).toBeInTheDocument();
    expect(within(region).queryByRole('table')).toBeNull();
    await fireEvent.click(
      within(region).getByRole('button', { name: 'Reload ratings' }),
    );
    // Both reload, even though the ratings come back with the same identity.
    // Agriculture is the fixture economy with contributing bodies, so the
    // explanation table is what proves the explained state came back.
    await waitFor(() => expect(mockedGetRatings).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockedGetExplanation).toHaveBeenCalledTimes(2));
    expect(
      await within(region).findByRole('table', {
        name: 'Bodies that make up this score',
      }),
    ).toBeInTheDocument();
  });

  it('keeps Inspect usable when a system is unrated or ratings are down', async () => {
    mockedGetSystem.mockResolvedValue(referenceSystemDetail());
    mockedGetRatings.mockRejectedValueOnce(
      new ApiError(404, '/api/ratings/v4/systems/10477373803', {}),
    );
    const first = render(SystemDetailTestHost, {
      props: { id64: parseId64('10477373803') },
    });
    expect(
      await screen.findByText(
        /the published source has no ratings for this system\./u,
      ),
    ).toBeInTheDocument();
    expect(mockedGetRatings).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole('heading', { name: 'Reference System' }),
    ).toBeInTheDocument();
    first.unmount();

    queryClient.clear();
    mockedGetRatings
      .mockRejectedValueOnce(new ApiError(503, '/api/ratings/v4/systems/1', {}))
      .mockRejectedValueOnce(
        new ApiError(503, '/api/ratings/v4/systems/1', {}),
      );
    render(SystemDetailTestHost, { props: { id64: parseId64('10477373803') } });
    expect(
      await screen.findByText(
        /economy ratings could not be loaded\./u,
        {},
        { timeout: 3_000 },
      ),
    ).toBeInTheDocument();
    await fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() =>
      expect(document.querySelectorAll('[data-rating-economy]')).toHaveLength(
        7,
      ),
    );
  });
});
