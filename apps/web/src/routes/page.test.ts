import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import '@testing-library/jest-dom/vitest';
import TestShell from '$lib/components/TestShell.svelte';
import {
  autocompleteSystems,
  getAuthSession,
  getHealth,
} from '$lib/api/client';
import { parseId64 } from '$lib/domain/id64';
import { ARCHETYPES } from '$lib/features/explore/archetypes';

const { mockedGoto, featureGate } = vi.hoisted(() => ({
  mockedGoto: vi.fn(),
  featureGate: { enabled: false },
}));

vi.mock('$app/navigation', () => ({ goto: mockedGoto }));
vi.mock('$lib/config', () => ({
  get FINDER_F4_ENABLED() {
    return featureGate.enabled;
  },
}));
vi.mock('$lib/api/client', () => ({
  authLogout: vi.fn(),
  autocompleteSystems: vi.fn(),
  claimOwner: vi.fn(),
  frontierLoginUrl: vi.fn(() => '/api/auth/frontier/login'),
  getAuthSession: vi.fn(),
  getHealth: vi.fn(),
}));

const mockedGetHealth = vi.mocked(getHealth);
const mockedGetAuthSession = vi.mocked(getAuthSession);
const mockedAutocomplete = vi.mocked(autocompleteSystems);
const system = {
  id64: parseId64('9007199254740993'),
  name: 'Selected system',
  x: 0,
  y: 0,
  z: 0,
};
const frontierCredit =
  'ED-Finder was created using assets and imagery from Elite Dangerous, with the permission of Frontier Developments plc, for non-commercial purposes. It is not endorsed by nor reflects the views or opinions of Frontier Developments and no employee of Frontier Developments was involved in the making of it.';

const renderPage = () => render(TestShell);
const referenceInput = () =>
  screen.getByRole('combobox', { name: 'Reference system' });
const findForm = () => screen.getByRole('form', { name: 'Find a system' });

async function chooseReferenceSystem() {
  await fireEvent.input(referenceInput(), { target: { value: 'Selected' } });
  await fireEvent.click(
    await screen.findByRole('option', { name: /Selected system/ }),
  );
}

describe('ED-Finder V3 home and shell', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    featureGate.enabled = false;
    mockedGoto.mockResolvedValue(undefined);
    mockedGetHealth.mockResolvedValue({
      status: 'ok',
      database: 'connected',
      version: 'test',
      build_sha: 'abc',
    });
    mockedGetAuthSession.mockResolvedValue({
      authenticated: false,
      user: null,
      owner_claim_available: false,
    });
    mockedAutocomplete.mockResolvedValue({ results: [system] });
  });
  afterEach(cleanup);

  it('renders the new single headline, ordered landmarks and guest status', async () => {
    renderPage();
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(
      screen.getByRole('heading', {
        name: 'Find your next frontier.',
        level: 1,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText('Your colony starts here')).toBeInTheDocument();
    const header = screen.getByRole('banner');
    const main = screen.getByRole('main');
    expect(header.compareDocumentPosition(main)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(
      within(header).getByRole('navigation', { name: 'Product journey' }),
    ).toBeInTheDocument();
    expect(
      within(header).getByRole('link', { name: 'Explore' }),
    ).toHaveAttribute('href', '/explore');
    await waitFor(() =>
      expect(screen.getByText('Connected')).toBeInTheDocument(),
    );
    expect(screen.getByText('Guest')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Sign in with Frontier' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Explore freely. Sign in with Frontier when you need your commander account.',
      ),
    ).toBeInTheDocument();
    expect(mockedGetHealth).toHaveBeenCalledTimes(1);
    expect(mockedAutocomplete).not.toHaveBeenCalled();
  });

  it('offers the labeled GET form and optional reference-free Explore link', () => {
    renderPage();
    const form = findForm();
    expect(form).toHaveAttribute('method', 'get');
    expect(within(form).getByLabelText('Reference system')).toBe(
      referenceInput(),
    );
    expect(
      within(form).getByRole('button', { name: 'Find a system →' }),
    ).toHaveAttribute('type', 'submit');
    expect(
      screen.getByRole('link', {
        name: 'Or open Explore without a reference system',
      }),
    ).toHaveAttribute('href', '/explore');
    expect(
      screen.queryByRole('combobox', { name: 'Colony archetype' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows Any followed by the eight canonical archetypes only when enabled', () => {
    featureGate.enabled = true;
    renderPage();
    const select = screen.getByRole('combobox', { name: 'Colony archetype' });
    expect(select.tagName).toBe('SELECT');
    expect(select).toHaveValue('any');
    const options = within(select).getAllByRole('option');
    expect(options.map((option) => option.textContent?.trim())).toEqual([
      'Any archetype',
      ...ARCHETYPES.map(({ label }) => label),
    ]);
    expect(options.map((option) => option.getAttribute('value'))).toEqual([
      'any',
      ...ARCHETYPES.map(({ key }) => key),
    ]);
  });

  it.each([false, true])(
    'submits a chosen exact id64 without an Any archetype (flag %s)',
    async (enabled) => {
      featureGate.enabled = enabled;
      renderPage();
      await chooseReferenceSystem();
      await fireEvent.submit(findForm());
      expect(mockedGoto).toHaveBeenCalledExactlyOnceWith(
        '/explore?near=9007199254740993',
      );
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    },
  );

  it('submits an enabled archetype with the chosen system in canonical order', async () => {
    featureGate.enabled = true;
    renderPage();
    await chooseReferenceSystem();
    await fireEvent.change(
      screen.getByRole('combobox', { name: 'Colony archetype' }),
      { target: { value: 'mining_hub' } },
    );
    await fireEvent.submit(findForm());
    expect(mockedGoto).toHaveBeenCalledExactlyOnceWith(
      '/explore?archetype=mining_hub&near=9007199254740993',
    );
  });

  it.each(['', 'Selected system', '9007199254740993'])(
    'requires an explicit suggestion selection for typed value %j',
    async (value) => {
      renderPage();
      if (value) await fireEvent.input(referenceInput(), { target: { value } });
      await fireEvent.submit(findForm());
      const alert = screen.getByRole('alert');
      expect(alert).toHaveTextContent('Choose a system from the list');
      expect(referenceInput()).toHaveFocus();
      expect(referenceInput()).toHaveAttribute('aria-invalid', 'true');
      expect(referenceInput()).toHaveAccessibleDescription(
        /Choose a system from the list/,
      );
      expect(mockedGoto).not.toHaveBeenCalled();
    },
  );

  it('invalidates the selection when its display text is edited', async () => {
    renderPage();
    await chooseReferenceSystem();
    await fireEvent.input(referenceInput(), { target: { value: 'Different' } });
    await fireEvent.submit(findForm());
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Choose a system from the list',
    );
    expect(referenceInput()).toHaveFocus();
    expect(mockedGoto).not.toHaveBeenCalled();
  });

  it('recovers from an empty submit after choosing a suggestion', async () => {
    renderPage();
    await fireEvent.submit(findForm());
    expect(screen.getByRole('alert')).toBeInTheDocument();
    await chooseReferenceSystem();
    await fireEvent.submit(findForm());
    expect(mockedGoto).toHaveBeenCalledExactlyOnceWith(
      '/explore?near=9007199254740993',
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('provides three ordered journey steps, each as one whole accessible link', () => {
    renderPage();
    const journey = screen.getByRole('region', { name: 'Your colony journey' });
    const links = within(journey).getAllByRole('link');
    expect(links).toHaveLength(3);
    const steps = [
      {
        number: '01',
        label: 'Find your system',
        detail: 'Choose an archetype and refine your search.',
        href: '/explore',
      },
      {
        number: '02',
        label: 'Understand its potential',
        detail: 'Inspect bodies, resources and rating evidence.',
        href: '/inspect',
      },
      {
        number: '03',
        label: 'Plan your colony',
        detail: 'Choose sites, order builds and review your plan.',
        href: '/plan',
      },
    ];
    steps.forEach(({ number, label, detail, href }, index) => {
      const link = links[index];
      expect(link).toHaveAttribute('href', href);
      expect(link).not.toHaveAttribute('tabindex');
      expect(within(link).getByText(number)).toBeInTheDocument();
      expect(
        within(link).getByRole('heading', { name: label, level: 2 }),
      ).toBeInTheDocument();
      expect(within(link).getByText(detail)).toBeInTheDocument();
      expect(link.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
      expect(link.querySelectorAll('a, button, input, select')).toHaveLength(0);
    });
    expect(
      screen.getByRole('region', { name: 'Connection status' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Review & export')).not.toBeInTheDocument();
  });

  it('uses only the responsive decorative black hole image', () => {
    renderPage();
    const main = screen.getByRole('main');
    const image = main.querySelector('picture img');
    expect(image).toHaveAttribute('alt', '');
    expect(image).toHaveAttribute('aria-hidden', 'true');
    expect(image).toHaveAttribute(
      'src',
      '/assets/home-hero-black-hole-1920.webp',
    );
    const source = main.querySelector('picture source');
    expect(source).toHaveAttribute('media', '(max-width: 640px)');
    expect(source).toHaveAttribute(
      'srcset',
      '/assets/home-hero-black-hole-960.webp',
    );
    expect(main.querySelectorAll('img')).toHaveLength(1);
    expect(within(main).queryByRole('img')).not.toBeInTheDocument();
    expect(main.querySelector('.galaxy-motif')).toBeNull();
    expect(main.querySelector('.orbital')).toBeNull();
  });

  it('keeps the existing footer line and displays the full Frontier credit', () => {
    renderPage();
    const footer = screen.getByRole('contentinfo');
    expect(footer).toHaveTextContent('Your Elite Dangerous companion.');
    expect(footer).toHaveTextContent('Explore. Discover. Make it yours.');
    expect(within(footer).getByText(frontierCredit)).toBeVisible();
  });

  it('reports failed bootstrap requests without hiding the shell', async () => {
    mockedGetHealth.mockRejectedValue(new Error('Request failed (503)'));
    mockedGetAuthSession.mockRejectedValue(new Error('Request failed (503)'));
    renderPage();
    await waitFor(() =>
      expect(screen.getAllByText('Unavailable')).toHaveLength(2),
    );
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(
      screen.getByRole('navigation', { name: 'Product journey' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Find your next frontier.' }),
    ).toBeInTheDocument();
  });
});
