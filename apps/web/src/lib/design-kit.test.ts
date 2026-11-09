import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/svelte';
import { parse, parseCss, type AST } from 'svelte/compiler';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as api from '$lib/api/client';
import { queryClient } from '$lib/api/query';
import { auth } from '$lib/auth/auth';
import WorkspaceHeader from '$lib/components/WorkspaceHeader.svelte';
import { parseId64 } from '$lib/domain/id64';
import ExploreWorkspaceTestHost from '$lib/features/explore/ExploreWorkspaceTestHost.svelte';

const { mockedPage } = vi.hoisted(() => ({
  mockedPage: {
    url: new URL('http://localhost/explore'),
    state: {} as App.PageState,
  },
}));

vi.mock('$app/state', () => ({ page: mockedPage }));
vi.mock('$app/navigation', () => ({
  goto: vi.fn().mockResolvedValue(undefined),
}));
vi.mock('$lib/config', () => ({ FINDER_F4_ENABLED: false }));
vi.mock('$lib/api/client', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/api/client')>()),
  autocompleteSystems: vi.fn(),
  searchExploreSystems: vi.fn(),
  getCatalogueViewportSystems: vi.fn(),
  getAuthSession: vi.fn(),
  getMapHeatmap: vi.fn(),
}));
// This contract exercises the real list DOM; GPU rendering has its own lane.
vi.mock('$lib/spatial/SpatialCanvas.svelte', () => ({ default: () => {} }));
vi.mock('$lib/spatial/galaxy-regions', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/spatial/galaxy-regions')>()),
  fetchAuthoritativeGalaxyRegions: vi
    .fn()
    .mockRejectedValue(new Error('No map asset in style contract')),
}));
vi.mock('$lib/spatial/galaxy-nebulae', async (loadOriginal) => ({
  ...(await loadOriginal<typeof import('$lib/spatial/galaxy-nebulae')>()),
  fetchGalaxyNebulae: vi
    .fn()
    .mockRejectedValue(new Error('No map asset in style contract')),
}));

const sourceRoot = resolve(process.cwd(), 'src');
const appCss = readFileSync(resolve(sourceRoot, 'app.css'), 'utf8');
const stylesheet = parseCss(appCss);

const tokens = {
  '--bg-void': '#080d17',
  '--bg-deep': '#0b1220',
  '--panel': '#121c2e',
  '--panel-raised': '#172539',
  '--panel-sunken': '#0d1524',
  '--panel-gloss':
    'linear-gradient(180deg, rgba(255,255,255,.10), rgba(255,255,255,0) 38%)',
  '--line': '#26405f',
  '--line-strong': '#3a5a84',
  '--line-glow':
    '0 0 0 1px rgba(61,165,255,.18), 0 0 24px rgba(61,165,255,.10)',
  '--text': '#e8eef7',
  '--text-muted': '#9fb0c7',
  '--text-dim': '#6f84a0',
  '--accent': '#ff7a14',
  '--accent-hi': '#ff9a3c',
  '--accent-text': '#1a0d02',
  '--accent-glow': '0 0 22px rgba(255,122,20,.35)',
  '--neon': '#3da5ff',
  '--neon-hi': '#5ec8ff',
  '--neon-soft': 'rgba(61,165,255,.16)',
  '--ok': '#4ade80',
  '--warn': '#e9c46a',
  '--danger': '#f87171',
  '--focus': 'var(--accent)',
  '--radius-sm': '8px',
  '--radius': '14px',
  '--radius-lg': '20px',
  '--radius-pill': '999px',
  '--shadow-panel':
    '0 10px 30px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.06)',
  '--font-hud': "'Saira Semi Condensed', 'Inter', sans-serif",
  '--ease': 'cubic-bezier(.2,.8,.2,1)',
} as const;

const tierColors = {
  '--color-tier-s': '#ff7a14',
  '--color-tier-a': '#7dd3fc',
  '--color-tier-b': '#86d99c',
  '--color-tier-c': '#e9c46a',
  '--color-tier-d': '#a6acb4',
} as const;

function normalize(value: string): string {
  return value
    .replace(/\s+/g, '')
    .replace(/"/g, "'")
    .replace(
      /(?:\b0)?\.(\d+)/g,
      (_, digits: string) => `.${digits.replace(/0+$/, '')}`,
    )
    .toLowerCase();
}

function selector(rule: AST.CSS.Rule, source: string): string {
  return source.slice(rule.prelude.start, rule.prelude.end).trim();
}

const rootDeclarations = new Map<string, string>();
for (const rule of stylesheet.children) {
  if (rule.type !== 'Rule' || selector(rule, appCss) !== ':root') continue;
  for (const declaration of rule.block.children) {
    if (declaration.type === 'Declaration') {
      rootDeclarations.set(declaration.property, declaration.value);
    }
  }
}

function resolveToken(name: string, visited = new Set<string>()): string {
  if (visited.has(name)) throw new Error(`Circular design token: ${name}`);
  const value = rootDeclarations.get(name);
  if (value === undefined) throw new Error(`Missing design token: ${name}`);
  return value.replace(/var\((--[\w-]+)\)/g, (_, reference: string) =>
    resolveToken(reference, new Set([...visited, name])),
  );
}

type CssNode = AST.CSS.Rule | AST.CSS.Atrule | AST.CSS.Declaration;

function hardCodedColors(
  nodes: CssNode[],
  source: string,
  allowTokenBlocks = false,
  context = '',
): string[] {
  return nodes.flatMap((node) => {
    if (node.type === 'Declaration') {
      const colors = node.value.match(
        /#[\da-f]{3,8}\b|\bhsla?\s*\([^)]*\)|\bwhite\b/gi,
      );
      if (!colors) return [];
      return colors.flatMap((color) => {
        const allowedTier =
          node.property in tierColors &&
          normalize(color) ===
            tierColors[node.property as keyof typeof tierColors];
        const allowedSkipLink =
          /(?:^|[\s,])\.skip-link(?:$|[:\s,])/.test(context) &&
          /^(?:white|#fff|#ffffff)$/i.test(color);
        return allowedTier || allowedSkipLink
          ? []
          : [`${context}: ${node.property}: ${node.value}`];
      });
    }
    const nextContext =
      node.type === 'Rule' ? selector(node, source) : `@${node.name}`;
    if (
      allowTokenBlocks &&
      (nextContext === ':root' || nextContext === '@theme')
    ) {
      return [];
    }
    return hardCodedColors(
      node.block?.children ?? [],
      source,
      allowTokenBlocks,
      nextContext,
    );
  });
}

const adoptedComponents = [
  'lib/components/WorkspaceHeader.svelte',
  'routes/+page.svelte',
  'lib/features/explore/ExploreWorkspace.svelte',
  'lib/features/explore/ExploreMapWindow.svelte',
  'lib/features/explore/BodyFilters.svelte',
  'lib/features/explore/ArchetypePicker.svelte',
  'lib/features/explore/FinderResultRanking.svelte',
  'lib/components/SystemDetail.svelte',
  'lib/components/GalaxyImpactPanel.svelte',
  'lib/features/explore/WatchlistPanel.svelte',
  'lib/spatial/SpatialCanvas.svelte',
  ...readdirSync(resolve(sourceRoot, 'lib/features/plan'))
    .filter((name) => name.endsWith('.svelte'))
    .map((name) => `lib/features/plan/${name}`),
];

describe('shared design kit', () => {
  it.each(Object.entries(tokens))('defines %s in :root', (name, value) => {
    expect(normalize(rootDeclarations.get(name) ?? '')).toBe(normalize(value));
  });

  it.each([
    ['--color-void', '--bg-void'],
    ['--color-panel', '--panel'],
    ['--color-signal', '--accent'],
    ['--color-cyan', '--neon'],
    ['--muted', '--text-muted'],
  ])('keeps the %s alias connected to %s', (alias, token) => {
    expect(normalize(rootDeclarations.get(alias) ?? '')).toBe(`var(${token})`);
  });

  it('keeps the existing metal surface connected to shared tokens', () => {
    const metal = rootDeclarations.get('--metal');
    expect(metal).toBeDefined();
    expect(metal).toContain('var(--panel');
    expect(() => resolveToken('--metal')).not.toThrow();
  });

  it.each(Object.entries(tierColors))(
    'preserves the existing %s color',
    (name, color) => {
      expect(normalize(resolveToken(name))).toBe(color);
    },
  );

  it.each(adoptedComponents)('%s uses shared colors in styles', (filename) => {
    const source = readFileSync(resolve(sourceRoot, filename), 'utf8');
    // Svelte's parser excludes scripts, template blocks, and inline data, so
    // IDs, literal text, and renderer-owned colors cannot masquerade as CSS.
    const style = parse(source, { modern: true, filename }).css;
    expect(hardCodedColors(style?.children ?? [], source)).toEqual([]);
  });

  it('uses shared colors in the Plan stylesheet', () => {
    const source = readFileSync(
      resolve(sourceRoot, 'lib/features/plan/plan.css'),
      'utf8',
    );
    expect(hardCodedColors(parseCss(source).children, source)).toEqual([]);
  });

  it('uses shared colors outside the global token definitions', () => {
    expect(hardCodedColors(stylesheet.children, appCss, true)).toEqual([]);
  });
});

describe('design-kit action opt-in', () => {
  let main: HTMLElement;
  let style: HTMLStyleElement;

  beforeEach(() => {
    queryClient.clear();
    localStorage.clear();
    style = document.createElement('style');
    // jsdom does not resolve custom properties. Resolve the real token values
    // while keeping the application's selectors, declarations and source order.
    // These are the non-media rules; responsive layout remains a browser check.
    style.textContent = stylesheet.children
      .filter((rule) => rule.type === 'Rule')
      .map((rule) => appCss.slice(rule.start, rule.end))
      .join('\n')
      .replace(/var\((--[\w-]+)\)/g, (expression, name: string) =>
        rootDeclarations.has(name) ? resolveToken(name) : expression,
      );
    document.head.append(style);
    main = document.createElement('main');
    main.id = 'main-content';
    document.body.append(main);
  });

  afterEach(() => {
    cleanup();
    queryClient.clear();
    main.remove();
    style.remove();
    vi.clearAllMocks();
  });

  it('keeps catalogue rows and autocomplete options soft while Inspect is a compact secondary pill', async () => {
    const anchor = {
      id64: parseId64('9007199254740993'),
      name: 'Achenar',
      x: 10,
      y: 20,
      z: 30,
    };
    mockedPage.url = new URL(`http://localhost/explore?near=${anchor.id64}`);
    mockedPage.state = { anchor };
    vi.mocked(api.getAuthSession).mockResolvedValue({
      authenticated: false,
      user: null,
    });
    await auth.bootstrap();
    vi.mocked(api.autocompleteSystems).mockResolvedValue({ results: [anchor] });
    vi.mocked(api.searchExploreSystems).mockResolvedValue({
      results: [
        {
          id64: parseId64('9007199254740994'),
          name: 'Nearby catalogue system',
          coords: { x: 11, y: 21, z: 31 },
          distance: 1.73,
          main_star_type: 'G',
        },
      ],
      page: {
        page_size: 24,
        count: 1,
        total: 1,
        navigable_total: 1,
        offset: 0,
        navigation_limit: 10_000,
        truncation: { source: 'local-search', total_is_capped: false },
      },
      ranking: {
        ranking_version: 'v3-colony-potential-2',
        ranking_sha256: 'ranking-sha',
        derived_generation_id: 'generation-id',
        publication_sequence: 1,
        archetype_version: 'archetype-version',
        archetype_manifest_sha256: 'manifest-sha',
        archetype_model_sha256: 'model-sha',
      },
    });
    vi.mocked(api.getCatalogueViewportSystems).mockResolvedValue({
      systems: [],
      truncated: false,
    });
    vi.mocked(api.getMapHeatmap).mockResolvedValue({
      source: 'legacy-fallback',
    });

    render(ExploreWorkspaceTestHost, { target: main });
    const results = await screen.findByRole('list', { name: 'System results' });
    const row = results.querySelector<HTMLButtonElement>('.result-select')!;
    expect(row).toHaveTextContent('Nearby catalogue system');
    expect(row).toHaveTextContent('G star');

    await fireEvent.input(
      screen.getByRole('combobox', { name: 'System name' }),
      {
        target: { value: 'Ach' },
      },
    );
    const suggestion = await screen.findByRole('option', { name: /Achenar/ });
    expect(suggestion.matches('.suggestions > button')).toBe(true);
    for (const selection of [row, suggestion]) {
      const computed = getComputedStyle(selection);
      expect(computed.borderRadius).toBe(resolveToken('--radius-sm'));
      expect(computed.borderRadius).not.toBe(resolveToken('--radius-pill'));
      expect(computed.backgroundColor).toBe('rgba(0, 0, 0, 0)');
    }

    const inspect = within(results).getByRole('link', {
      name: 'Inspect Nearby catalogue system',
    });
    expect(inspect).toHaveClass('inspect-link', 'secondary-button');
    const inspectStyle = getComputedStyle(inspect);
    expect(inspectStyle.borderRadius).toBe(resolveToken('--radius-pill'));
    expect(inspectStyle.alignSelf).toBe('center');
    expect(inspectStyle.height).toBe('auto');
    expect(inspectStyle.backgroundColor).toBe('rgba(0, 0, 0, 0)');
    expect(inspectStyle.backgroundImage).toBe('none');
    expect(inspectStyle.borderTopColor).toBe('rgb(61, 165, 255)');
    // Positive control: an opted-in action really receives the mounted kit CSS.
    expect(
      getComputedStyle(
        within(results).getByRole('button', {
          name: 'Pin Nearby catalogue system',
        }),
      ).borderRadius,
    ).toBe(resolveToken('--radius-pill'));
  });

  it.each(['/', '/explore', '/inspect', '/plan'])(
    'keeps the shared header action and active journey underline consistent on %s',
    async (pathname) => {
      mockedPage.url = new URL(`http://localhost${pathname}`);
      vi.mocked(api.getAuthSession).mockResolvedValue({
        authenticated: false,
        user: null,
      });
      await auth.bootstrap();
      // The live header sits outside #main-content and must receive the kit
      // through its account-controls scope, independently of the route body.
      main.removeAttribute('id');
      render(WorkspaceHeader, { target: main });
      const signIn = screen.getByRole('button', {
        name: 'Sign in with Frontier',
      });
      const signInStyle = getComputedStyle(signIn);
      expect(signInStyle.borderRadius).toBe(resolveToken('--radius-pill'));
      expect(signInStyle.backgroundImage).toContain('linear-gradient');
      expect(signInStyle.borderTopColor).toBe('rgb(255, 122, 20)');
      expect(signInStyle.color).toBe('rgb(26, 13, 2)');

      const nav = screen.getByRole('navigation', { name: 'Product journey' });
      const activeLinks = nav.querySelectorAll('a[aria-current="page"]');
      expect(activeLinks).toHaveLength(pathname === '/' ? 0 : 1);
      if (pathname !== '/') {
        const activeLink = activeLinks[0];
        expect(activeLink).toHaveAttribute('href', pathname);
        const activeStyle = getComputedStyle(activeLink);
        expect(activeStyle.textDecorationLine).toBe('underline');
        expect(activeStyle.textDecorationColor).toBe(resolveToken('--accent'));
        expect(activeStyle.textDecorationThickness).toBe('2px');
      }
    },
  );

  it.each([
    ['home CTA', '<a class="primary-button" href="/explore">Explore</a>'],
    ['Plan CTA', '<button class="primary-button">Start a plan</button>'],
    [
      'camera toolbar',
      '<div class="camera-toolbar"><button data-map-interactive>Reset camera</button></div>',
    ],
    ['pop-out trigger', '<button aria-haspopup="dialog">Filters</button>'],
    [
      'tab',
      '<div role="tablist"><button role="tab" aria-selected="true">Details</button></div>',
    ],
    [
      'watchlist action',
      '<section class="saved-systems"><button class="kit-pill">Watch</button></section>',
    ],
  ])('retains the pill skin for an opted-in %s', (_name, markup) => {
    main.innerHTML = markup;
    const action = main.querySelector('button, a')!;
    expect(getComputedStyle(action).borderRadius).toBe(
      resolveToken('--radius-pill'),
    );
    if (action.classList.contains('primary-button')) {
      expect(getComputedStyle(action).backgroundImage).toContain(
        'linear-gradient',
      );
      expect(getComputedStyle(action).color).toBe('rgb(26, 13, 2)');
    }
  });

  it.each([
    ['bare button', '<button>Unclassified control</button>'],
    [
      'Inspect body row',
      '<div class="system-map-browser"><ol><li><button>Body</button></li></ol></div>',
    ],
    [
      'watchlist row',
      '<section class="saved-systems"><div class="row-actions"><button>Saved system</button></div></section>',
    ],
    [
      'compare system name',
      '<section class="compare-panel"><button class="system-name">Compared system</button></section>',
    ],
    [
      'nearby system row',
      '<div class="nearby-systems"><button>Nearby system</button></div>',
    ],
    [
      'map label',
      '<button class="galaxy-label--system" data-map-interactive>Map system</button>',
    ],
    [
      'unclassified map control',
      '<button data-map-interactive>Map control</button>',
    ],
  ])(
    'does not apply global pill or raised-panel skins to a %s',
    (_name, markup) => {
      main.innerHTML = markup;
      const button = main.querySelector('button')!;
      const matchingSkins = stylesheet.children.flatMap((rule) => {
        if (rule.type !== 'Rule') return [];
        const paintsAction = rule.block.children.some(
          (declaration) =>
            declaration.type === 'Declaration' &&
            ((declaration.property === 'border-radius' &&
              declaration.value.includes('var(--radius-pill)')) ||
              (/^background(?:-color)?$/.test(declaration.property) &&
                declaration.value.includes('var(--panel-raised)'))),
        );
        if (!paintsAction) return [];
        return rule.prelude.children.flatMap((part) => {
          const ruleSelector = appCss.slice(part.start, part.end);
          // Pseudo-elements cannot style the button itself; jsdom also rejects
          // Firefox's range-track pseudo-element in Element.matches().
          return !ruleSelector.includes('::') && button.matches(ruleSelector)
            ? [ruleSelector]
            : [];
        });
      });
      expect(matchingSkins).toEqual([]);
      expect(getComputedStyle(button).borderRadius).not.toBe(
        resolveToken('--radius-pill'),
      );
    },
  );

  it('keeps the generated switch thumb opt-in so existing switches keep one indicator', () => {
    main.innerHTML =
      '<button class="layer-switch" role="switch" aria-checked="true">' +
      '<span class="switch-track" aria-hidden="true"><span></span></span>Known-system density</button>' +
      '<button class="kit-switch" role="switch" aria-checked="false">Kit</button>';
    const existing = main.querySelector('.layer-switch')!;
    const kit = main.querySelector('.kit-switch')!;
    const switchSelectors = stylesheet.children.flatMap((rule) => {
      if (rule.type !== 'Rule') return [];
      return rule.prelude.children.flatMap((part) => {
        const ruleSelector = appCss.slice(part.start, part.end);
        return ruleSelector.includes("[role='switch']") ? [ruleSelector] : [];
      });
    });
    expect(switchSelectors.length).toBeGreaterThan(0);
    for (const selector of switchSelectors) {
      const base = selector.replace(/::after$/, '');
      expect(existing.matches(base)).toBe(false);
      expect(base.includes('.kit-switch')).toBe(true);
    }
    expect(
      kit.matches(":where(#main-content, .overlay) .kit-switch[role='switch']"),
    ).toBe(true);
  });
});
