import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import axe from 'axe-core';
import { createRawSnippet } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import MapLayers from './MapLayers.svelte';
import { isTopmostPopout, registerPopout } from '$lib/components/popout-stack';

function status(text: string) {
  return createRawSnippet(() => ({ render: () => `<span>${text}</span>` }));
}

function renderLayers() {
  const callbacks = {
    onDensityToggle: vi.fn(),
    onJourneysToggle: vi.fn(),
    onNebulaeToggle: vi.fn(),
  };
  return {
    ...render(MapLayers, {
      densityEnabled: true,
      journeysEnabled: false,
      nebulaeEnabled: true,
      densityCellSizeLy: 320,
      densityGeneration: 'catalogue-generation-7',
      densityStatus: status('Density ready'),
      journeysStatus: status('Sign in and import your journals'),
      nebulaeStatus: status('10 catalogue records · 3 landmarks shown'),
      ...callbacks,
    }),
    ...callbacks,
  };
}

async function openLayers() {
  const trigger = screen.getByRole('button', { name: /^Layers · / });
  trigger.focus();
  await fireEvent.click(trigger);
  return {
    trigger,
    dialog: screen.getByRole('dialog', { name: 'Map layers' }),
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('Map layers pop-out', () => {
  it('shares layer order and Escape ownership with the enclosing map and other tools', async () => {
    const map = document.createElement('div');
    const laterTool = document.createElement('div');
    const mapLayer = vi.fn();
    const laterLayer = vi.fn();
    const unregisterMap = registerPopout(map, mapLayer);
    renderLayers();
    const { trigger, dialog } = await openLayers();
    expect(mapLayer).toHaveBeenLastCalledWith(30);
    expect(dialog.style.zIndex).toBe('31');
    expect(isTopmostPopout(dialog)).toBe(true);
    const unregisterLater = registerPopout(laterTool, laterLayer);
    expect(laterLayer).toHaveBeenLastCalledWith(32);
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(dialog).toBeInTheDocument();
    unregisterLater();
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(dialog).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(isTopmostPopout(map)).toBe(true);
    unregisterMap();
  });

  it('names three independent switches and their descriptions in a non-modal dialog', async () => {
    renderLayers();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    const { trigger, dialog } = await openLayers();
    expect(trigger).toHaveAccessibleName('Layers · 2');
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(trigger).toHaveAttribute('aria-controls', dialog.id);
    expect(dialog).toHaveAttribute('aria-modal', 'false');
    expect(within(dialog).getAllByRole('switch')).toHaveLength(3);
    for (const [name, checked, hook, description] of [
      [
        'Known-system density',
        true,
        'data-density-toggle',
        'Public aggregate of catalogue systems per cell in this view; detail changes with zoom.',
      ],
      [
        'My journeys',
        false,
        'data-commander-history-toggle',
        'Your imported journal travel, private to your account.',
      ],
      [
        'Nebulae',
        true,
        'data-nebula-toggle',
        'Catalogue nebula landmarks, shown at the appropriate zoom.',
      ],
    ] as const) {
      const control = within(dialog).getByRole('switch', { name, checked });
      expect(control).toHaveAttribute(hook);
      expect(control).toHaveAccessibleDescription(description);
    }
    await waitFor(() =>
      expect(
        within(dialog).getByRole('switch', { name: 'Known-system density' }),
      ).toHaveFocus(),
    );
    expect(within(dialog).getByText('Density ready')).toBeVisible();
    expect(
      within(dialog).getByText('Sign in and import your journals'),
    ).toBeVisible();
    expect(
      within(dialog).getByText('10 catalogue records · 3 landmarks shown'),
    ).toBeVisible();
    expect(within(dialog).queryByText(/colony potential/i)).toBeNull();
  });

  it('calls only the selected switch handler and updates its state and the enabled count', async () => {
    const { rerender, onDensityToggle, onJourneysToggle, onNebulaeToggle } =
      renderLayers();
    const { dialog, trigger } = await openLayers();
    await fireEvent.click(
      within(dialog).getByRole('switch', { name: 'Known-system density' }),
    );
    expect(onDensityToggle).toHaveBeenCalledExactlyOnceWith();
    expect(onJourneysToggle).not.toHaveBeenCalled();
    expect(onNebulaeToggle).not.toHaveBeenCalled();
    await rerender({ densityEnabled: false });
    expect(trigger).toHaveAccessibleName('Layers · 1');
    expect(
      within(dialog).getByRole('switch', { name: 'Known-system density' }),
    ).not.toBeChecked();
    await fireEvent.click(
      within(dialog).getByRole('switch', { name: 'My journeys' }),
    );
    expect(onJourneysToggle).toHaveBeenCalledExactlyOnceWith();
    await rerender({ journeysEnabled: true });
    expect(trigger).toHaveAccessibleName('Layers · 2');
    expect(
      within(dialog).getByRole('switch', { name: 'My journeys' }),
    ).toBeChecked();
    await fireEvent.click(
      within(dialog).getByRole('switch', { name: 'Nebulae' }),
    );
    expect(onNebulaeToggle).toHaveBeenCalledExactlyOnceWith();
    await rerender({ nebulaeEnabled: false });
    expect(trigger).toHaveAccessibleName('Layers · 1');
    expect(
      within(dialog).getByRole('switch', { name: 'Nebulae' }),
    ).not.toBeChecked();
    expect(dialog).toBeVisible();
  });

  it('shows density metadata and separate legends, with journey counts only when known', async () => {
    const { rerender } = renderLayers();
    const { dialog } = await openLayers();
    const densityLegend = within(dialog).getByLabelText(
      'Known-system density legend',
    );
    expect(densityLegend).toHaveTextContent('fewer systems');
    expect(densityLegend).toHaveTextContent('more systems');
    expect(densityLegend).toHaveTextContent('320 LY cells');
    expect(densityLegend).toHaveTextContent(
      'Generation: catalogue-generation-7',
    );
    expect(within(dialog).getByLabelText('Nebulae legend')).toHaveTextContent(
      'Diffuse clouds',
    );
    expect(within(dialog).getByLabelText('Nebulae legend')).toHaveTextContent(
      'Planetary markers',
    );
    expect(
      within(dialog).queryByLabelText('My journeys visit-count legend'),
    ).not.toBeInTheDocument();
    await rerender({ journeysMaxVisits: 12, densityGeneration: null });
    const visitsLegend = within(dialog).getByLabelText(
      'My journeys visit-count legend',
    );
    expect(visitsLegend).toHaveTextContent('1 visit');
    expect(visitsLegend).toHaveTextContent('12 visits');
    expect(densityLegend).not.toHaveTextContent('Generation:');
    await rerender({ densityCellSizeLy: null });
    expect(densityLegend).toHaveTextContent('Cell size available when loaded');
    expect(densityLegend).not.toHaveTextContent('320 LY cells');
  });

  it('shows complete coverage at the served cell size and numbers for truncation', async () => {
    const { rerender } = renderLayers();
    await rerender({ densityCoverage: 'Complete at 320 LY' });
    const { dialog } = await openLayers();
    const legend = within(dialog).getByLabelText('Known-system density legend');
    expect(legend).toHaveTextContent('Complete at 320 LY');
    await rerender({
      densityCoverage: 'Truncated · 40,000 cells shown at 320 LY',
    });
    expect(legend).toHaveTextContent(
      'Truncated · 40,000 cells shown at 320 LY',
    );
    expect(legend).not.toHaveTextContent('Complete at');
    expect(legend).not.toHaveTextContent('bounded view');
  });

  it('keeps source attribution and the full rights notice reachable when nebulae are hidden', async () => {
    const { rerender } = renderLayers();
    const attribution = 'EDAstro / CMDR Orvidius';
    const rights = 'Copyright © CMDR Orvidius — All Rights Reserved';
    await rerender({
      nebulaeEnabled: false,
      nebulaeStatus: createRawSnippet(() => ({
        render: () =>
          `<span><a href="https://edastro.com/mapcharts/files.html">${attribution}</a> · <a href="https://edastro.com/mapcharts/files/nebulae-coordinates.csv">source CSV</a> · ${rights}</span>`,
      })),
    });
    const { dialog } = await openLayers();
    expect(
      within(dialog).getByRole('switch', { name: 'Nebulae' }),
    ).not.toBeChecked();
    expect(
      within(dialog).getByRole('link', { name: attribution }),
    ).toHaveAttribute('href', 'https://edastro.com/mapcharts/files.html');
    expect(
      within(dialog).getByRole('link', { name: 'source CSV' }),
    ).toHaveAttribute(
      'href',
      'https://edastro.com/mapcharts/files/nebulae-coordinates.csv',
    );
    expect(dialog).toHaveTextContent(rights);
  });

  it.each(['Escape', 'Close', 'outside', 'trigger'] as const)(
    'closes on %s and returns focus to the Layers trigger',
    async (method) => {
      renderLayers();
      const { trigger, dialog } = await openLayers();
      await waitFor(() =>
        expect(
          within(dialog).getByRole('switch', { name: 'Known-system density' }),
        ).toHaveFocus(),
      );
      if (method === 'Escape')
        await fireEvent.keyDown(window, { key: 'Escape' });
      else if (method === 'Close')
        await fireEvent.click(
          within(dialog).getByRole('button', { name: 'Close map layers' }),
        );
      else if (method === 'outside') await fireEvent.click(document.body);
      else await fireEvent.click(trigger);
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(trigger).toHaveAttribute('aria-expanded', 'false');
      expect(trigger).toHaveFocus();
    },
  );

  it('positions beside its trigger with room in the layout viewport', async () => {
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
      function (this: HTMLElement) {
        return this.hasAttribute('data-map-layers-toggle')
          ? DOMRect.fromRect({ x: 100, y: 100, width: 80, height: 32 })
          : DOMRect.fromRect({ width: 432, height: 400 });
      },
    );
    renderLayers();
    const { dialog } = await openLayers();
    await waitFor(() => expect(dialog.style.left).toBe('188px'));
    expect(dialog.style.top).toBe('100px');
  });

  it('clamps both dimensions and position to the visual viewport and follows its changes', async () => {
    const listeners = new Map<string, EventListener>();
    const visualViewport = {
      width: 320,
      height: 240,
      offsetLeft: 50,
      offsetTop: 100,
      addEventListener: (type: string, listener: EventListener) =>
        listeners.set(type, listener),
      removeEventListener: (type: string) => listeners.delete(type),
    };
    const original = Object.getOwnPropertyDescriptor(window, 'visualViewport');
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: visualViewport,
    });
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
      function (this: HTMLElement) {
        return this.hasAttribute('data-map-layers-toggle')
          ? DOMRect.fromRect({ x: 900, y: 700, width: 80, height: 32 })
          : DOMRect.fromRect({ width: 432, height: 640 });
      },
    );
    try {
      renderLayers();
      const { dialog } = await openLayers();
      await waitFor(() => expect(dialog.style.maxWidth).toBe('304px'));
      expect(dialog.style.maxHeight).toBe('224px');
      expect(dialog.style.left).toBe('58px');
      expect(dialog.style.top).toBe('108px');
      visualViewport.width = 390;
      visualViewport.height = 700;
      listeners.get('resize')?.(new Event('resize'));
      await waitFor(() => expect(dialog.style.maxWidth).toBe('374px'));
      expect(dialog.style.maxHeight).toBe('684px');
      expect(dialog.style.top).toBe('152px');
      visualViewport.offsetLeft = 70;
      visualViewport.offsetTop = 120;
      listeners.get('scroll')?.(new Event('scroll'));
      await waitFor(() => expect(dialog.style.left).toBe('78px'));
      expect(dialog.style.top).toBe('172px');
      await fireEvent.keyDown(window, { key: 'Escape' });
      expect(listeners.size).toBe(0);
    } finally {
      cleanup();
      if (original) Object.defineProperty(window, 'visualViewport', original);
      else delete (window as { visualViewport?: unknown }).visualViewport;
    }
  });

  it('has no axe violations with the pop-out open', async () => {
    const { container, rerender } = renderLayers();
    await rerender({ journeysEnabled: true, journeysMaxVisits: 12 });
    await openLayers();
    const result = await axe.run(container, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });
});
