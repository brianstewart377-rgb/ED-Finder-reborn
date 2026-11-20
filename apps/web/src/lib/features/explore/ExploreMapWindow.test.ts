import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/svelte';
import axe from 'axe-core';
import { createRawSnippet } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ExploreMapWindow from './ExploreMapWindow.svelte';

// The window is the frame only: a heading, the page's own tools, the map the
// page puts inside it, and the page's own footer.
const snippet = (html: string) =>
  createRawSnippet(() => ({ render: () => html }));
const frame = (props: Record<string, unknown> = {}) =>
  render(ExploreMapWindow, {
    finderPanelOpen: true,
    onShowFinder: vi.fn(),
    children: snippet('<div data-the-map>the map</div>'),
    ...props,
  });

describe('Explore map window', () => {
  afterEach(() => cleanup());

  it('is a page region holding the map once, with no window controls, and never takes focus', () => {
    const opener = document.createElement('button');
    document.body.append(opener);
    opener.focus();
    frame();
    const region = screen.getByRole('region', { name: 'Galaxy map' });
    expect(region).toHaveAttribute('id', 'explore-map-window');
    expect(region.querySelectorAll('[data-the-map]')).toHaveLength(1);
    // Part of the page, not a pop-out: nothing to open, close or expand.
    expect(within(region).queryAllByRole('button')).toHaveLength(0);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(opener);
    opener.remove();
  });

  it('names the map for the selected system', () => {
    frame({ selectedSystemName: 'Wregoe SJ-I d9-89' });
    expect(
      screen.getByRole('region', { name: 'Wregoe SJ-I d9-89 · Galaxy map' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Wregoe SJ-I d9-89 · Galaxy map' }),
    ).toBeInTheDocument();
  });

  it('behind the results keeps the map but takes no focus, and offers Show results in front', async () => {
    const onShowResults = vi.fn();
    const { rerender, container } = frame({
      finderPanelOpen: false,
      inFront: false,
      onShowResults,
    });
    const region = container.querySelector<HTMLElement>(
      '[data-explore-map-window]',
    )!;
    const map = container.querySelector('[data-the-map]');
    expect(region).toHaveAttribute('data-map-in-front', 'false');
    expect(region.inert).toBe(true);
    // Neither button is offered from behind the results.
    expect(region.querySelectorAll('button')).toHaveLength(0);

    await rerender({ inFront: true });
    expect(region).toHaveAttribute('data-map-in-front', 'true');
    expect(region.inert).toBe(false);
    expect(container.querySelector('[data-the-map]')).toBe(map);
    const show = screen.getByRole('button', { name: 'Show results' });
    expect(show).toHaveAttribute('aria-controls', 'explore-results');
    await fireEvent.click(show);
    expect(onShowResults).toHaveBeenCalledOnce();
    expect(
      screen.getByRole('button', { name: 'Show Finder' }),
    ).toBeInTheDocument();
  });

  it('offers Show Finder only while the Finder panel is hidden, without rebuilding the map', async () => {
    const onShowFinder = vi.fn();
    const { rerender, container } = frame({ onShowFinder });
    const map = container.querySelector('[data-the-map]');
    expect(screen.queryByRole('button', { name: 'Show Finder' })).toBeNull();
    expect(
      container.querySelector('[data-explore-map-window]'),
    ).toHaveAttribute('data-finder-panel-open', 'true');

    await rerender({ finderPanelOpen: false });
    const show = screen.getByRole('button', { name: 'Show Finder' });
    expect(show).toHaveAttribute('aria-controls', 'finder-panel');
    expect(show).toHaveAttribute('aria-expanded', 'false');
    // In the row above the map, before the page's tools: the map's own
    // panels fill the map's corners, so nothing of ours sits over it.
    expect(container.querySelector('.map-window-heading')).toContainElement(
      show,
    );
    expect(container.querySelector('.map-window-content')).not.toContainElement(
      show,
    );
    await fireEvent.click(show);
    expect(onShowFinder).toHaveBeenCalledTimes(1);
    // The same element: hiding and showing the Finder never restarts the map.
    expect(container.querySelector('[data-the-map]')).toBe(map);

    await rerender({ finderPanelOpen: true });
    expect(screen.queryByRole('button', { name: 'Show Finder' })).toBeNull();
    expect(container.querySelector('[data-the-map]')).toBe(map);
  });

  it('puts the page’s tools in its heading and the page’s own footer under the map', () => {
    const { container } = frame({
      beforeCanvas: snippet('<button type="button">Whole galaxy</button>'),
      afterCanvas: snippet('<a href="/inspect">Inspect Sol</a>'),
    });
    const heading = container.querySelector('.map-window-heading')!;
    const footer = container.querySelector('.map-window-footer')!;
    expect(
      within(heading as HTMLElement).getByRole('button', {
        name: 'Whole galaxy',
      }),
    ).toBeInTheDocument();
    expect(
      within(footer as HTMLElement).getByRole('link', { name: 'Inspect Sol' }),
    ).toBeInTheDocument();
    // In reading order: tools, then the map, then the footer.
    const map = container.querySelector('[data-the-map]')!;
    expect(
      heading.compareDocumentPosition(map) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      map.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('has no automated accessibility violations, with the Finder shown or hidden', async () => {
    const { container, rerender } = frame({
      selectedSystemName: 'Sol',
      beforeCanvas: snippet('<button type="button">Whole galaxy</button>'),
    });
    expect((await axe.run(container)).violations).toEqual([]);
    await rerender({ finderPanelOpen: false });
    expect((await axe.run(container)).violations).toEqual([]);
  });
});
