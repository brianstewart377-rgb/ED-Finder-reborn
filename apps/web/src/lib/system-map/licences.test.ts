import { render } from '@testing-library/svelte';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import SystemMapLegend from './SystemMapLegend.svelte';

const fromWeb = (path: string) => resolve(process.cwd(), path);

describe('the licences of what draws the System Map', () => {
  it('ships Cosmos’s licence exactly as the installed package has it', () => {
    expect(
      readFileSync(fromWeb('static/licences/cosmos-LICENSE.txt'), 'utf8'),
    ).toBe(
      readFileSync(fromWeb('node_modules/@eluvade/cosmos/LICENSE'), 'utf8'),
    );
  });

  it('ships the licence of Pixel Planets, which Cosmos’s planets come from', () => {
    const text = readFileSync(
      fromWeb('static/licences/pixel-planets-LICENSE.txt'),
      'utf8',
    );
    expect(text).toContain('MIT License');
    expect(text).toContain('Copyright (c) 2020 Deep-Fold');
  });

  it('links both from the map’s legend, to files the site serves', () => {
    const { container } = render(SystemMapLegend);
    const links = [...container.querySelectorAll('a')]
      .map((link) => link.getAttribute('href') ?? '')
      .filter((href) => href.includes('/licences/'));
    expect(links).toHaveLength(2);
    for (const href of links)
      expect(
        existsSync(fromWeb(`static${href.slice(href.indexOf('/licences/'))}`)),
      ).toBe(true);
  });
});
