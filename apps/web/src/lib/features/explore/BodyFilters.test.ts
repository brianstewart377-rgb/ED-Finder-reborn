import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import axe from 'axe-core';
import { afterEach, describe, expect, it, vi } from 'vitest';
import BodyFilters from './BodyFilters.svelte';
import { BODY_FILTER_GROUPS, type BodyFilterValues } from './body-filters';

afterEach(cleanup);

function filters(
  value: BodyFilterValues = {},
  options: { disabled?: boolean; paused?: boolean } = {},
) {
  const onapply = vi.fn();
  const onremove = vi.fn();
  const onclear = vi.fn();
  return {
    ...render(BodyFilters, { value, ...options, onapply, onremove, onclear }),
    onapply,
    onremove,
    onclear,
  };
}

async function open() {
  const trigger = screen.getByRole('button', { name: 'Body filters' });
  trigger.focus();
  await fireEvent.click(trigger);
  return trigger;
}

async function number(label: string, value: string) {
  await fireEvent.input(screen.getByRole('spinbutton', { name: label }), {
    target: { value },
  });
}

describe('body filter pop-out', () => {
  it('opens a named non-modal dialog and gives all 19 filters accessible controls across three tabs', async () => {
    filters();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    const trigger = await open();
    const dialog = screen.getByRole('dialog', { name: 'Body filters' });
    expect(dialog).toHaveAttribute('aria-modal', 'false');
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Planets 0' })).toHaveFocus(),
    );
    expect(screen.getAllByRole('tab')).toHaveLength(3);
    for (const group of BODY_FILTER_GROUPS) {
      await fireEvent.click(
        screen.getByRole('tab', { name: `${group.label} 0` }),
      );
      expect(
        screen.getByRole('tabpanel', { name: `${group.label} 0` }),
      ).toBeVisible();
      expect(screen.getAllByRole('group')).toHaveLength(group.filters.length);
      for (const option of group.filters) {
        const fieldset = screen.getByRole('group', { name: option.label });
        expect(fieldset).toBeVisible();
        const controls = within(fieldset);
        expect(fieldset.querySelectorAll('input')).toHaveLength(5);
        expect(
          controls.getByRole('slider', {
            name: `${option.label} minimum slider`,
          }),
        ).toHaveAttribute('max', String(option.sliderMax));
        expect(
          controls.getByRole('slider', {
            name: `${option.label} maximum slider`,
          }),
        ).toBeDisabled();
        expect(
          controls.getByRole('spinbutton', { name: `${option.label} minimum` }),
        ).toHaveAttribute('max', '999');
        expect(
          controls.getByRole('spinbutton', { name: `${option.label} maximum` }),
        ).toHaveAttribute('step', '1');
        expect(
          controls.getByRole('checkbox', {
            name: `${option.label} Any maximum`,
          }),
        ).toBeChecked();
      }
    }
  });

  it('supports arrow, Home and End keys across the tabs', async () => {
    filters();
    await open();
    await fireEvent.keyDown(screen.getByRole('tab', { name: 'Planets 0' }), {
      key: 'ArrowRight',
    });
    expect(screen.getByRole('tab', { name: 'Stars 0' })).toHaveFocus();
    expect(screen.getByRole('tab', { name: 'Stars 0' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await fireEvent.keyDown(screen.getByRole('tab', { name: 'Stars 0' }), {
      key: 'End',
    });
    expect(screen.getByRole('tab', { name: 'Features 0' })).toHaveFocus();
    await fireEvent.keyDown(screen.getByRole('tab', { name: 'Features 0' }), {
      key: 'ArrowRight',
    });
    expect(screen.getByRole('tab', { name: 'Planets 0' })).toHaveFocus();
    await fireEvent.keyDown(screen.getByRole('tab', { name: 'Planets 0' }), {
      key: 'ArrowLeft',
    });
    expect(screen.getByRole('tab', { name: 'Features 0' })).toHaveFocus();
    await fireEvent.keyDown(screen.getByRole('tab', { name: 'Features 0' }), {
      key: 'Home',
    });
    expect(screen.getByRole('tab', { name: 'Planets 0' })).toHaveFocus();
  });

  it('preserves typed drafts across keyboard tab changes without committing or exposing hidden controls', async () => {
    const { onapply, onremove, onclear } = filters();
    await open();
    const minimum = screen.getByRole('spinbutton', {
      name: 'Rocky ice minimum',
    });
    minimum.focus();
    const nativeKey = new KeyboardEvent('keydown', {
      key: 'ArrowUp',
      bubbles: true,
      cancelable: true,
    });
    minimum.dispatchEvent(nativeKey);
    expect(nativeKey.defaultPrevented).toBe(false);
    await number('Rocky ice minimum', '2');
    expect(minimum).toHaveFocus();
    expect(
      screen.getByRole('slider', { name: 'Rocky ice minimum slider' }),
    ).toHaveAttribute('aria-valuetext', '2');

    const planets = screen.getByRole('tab', { name: 'Planets 1' });
    planets.focus();
    await fireEvent.keyDown(planets, { key: 'ArrowRight' });
    const stars = screen.getByRole('tab', { name: 'Stars 0' });
    expect(stars).toHaveFocus();
    expect(stars).toHaveAttribute('tabindex', '0');
    expect(planets).toHaveAttribute('tabindex', '-1');
    expect(
      document.getElementById(planets.getAttribute('aria-controls')!),
    ).not.toBeVisible();
    expect(
      screen.queryByRole('spinbutton', { name: 'Rocky ice minimum' }),
    ).not.toBeInTheDocument();
    await number('Black holes maximum', '0');
    await fireEvent.keyDown(screen.getByRole('tab', { name: 'Stars 1' }), {
      key: 'Home',
    });
    expect(minimum).toHaveValue(2);
    expect(planets).toHaveFocus();
    expect(screen.getByText('2 filters selected')).toBeVisible();
    expect(onapply).not.toHaveBeenCalled();
    expect(onremove).not.toHaveBeenCalled();
    expect(onclear).not.toHaveBeenCalled();

    await fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(onapply).toHaveBeenCalledExactlyOnceWith({
      rocky_ice_count: { min: 2 },
      black_hole_count: { max: 0 },
    });
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
  });

  it('keeps slider and number changes in the draft, updates badges, and commits once on Apply', async () => {
    const { onapply } = filters();
    const trigger = await open();
    await fireEvent.input(
      screen.getByRole('slider', { name: 'Earth-like minimum slider' }),
      { target: { value: '1' } },
    );
    expect(
      screen.getByRole('spinbutton', { name: 'Earth-like minimum' }),
    ).toHaveValue(1);
    await number('Water worlds minimum', '3');
    await number('Water worlds maximum', '6');
    expect(
      screen.getByRole('slider', { name: 'Water worlds maximum slider' }),
    ).toHaveValue('6');
    expect(
      screen.getByRole('checkbox', { name: 'Water worlds Any maximum' }),
    ).not.toBeChecked();
    expect(screen.getByRole('tab', { name: 'Planets 2' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await fireEvent.click(screen.getByRole('tab', { name: 'Features 0' }));
    await number('Rings maximum', '3');
    expect(screen.getByRole('tab', { name: 'Features 1' })).toBeVisible();
    expect(screen.getByText('3 filters selected')).toBeVisible();
    expect(onapply).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(onapply).toHaveBeenCalledExactlyOnceWith({
      elw_count: { min: 1 },
      ww_count: { min: 3, max: 6 },
      ring_count: { max: 3 },
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('uses Any to remove the maximum while retaining the minimum and omits empty filters', async () => {
    const { onapply } = filters({ elw_count: { min: 2, max: 4 } });
    await open();
    const any = screen.getByRole('checkbox', {
      name: 'Earth-like Any maximum',
    });
    expect(any).not.toBeChecked();
    await fireEvent.click(any);
    expect(
      screen.getByRole('spinbutton', { name: 'Earth-like maximum' }),
    ).toHaveValue(null);
    expect(
      screen.getByRole('slider', { name: 'Earth-like maximum slider' }),
    ).toBeDisabled();
    await fireEvent.click(
      screen.getByRole('checkbox', { name: 'Water worlds Any maximum' }),
    );
    expect(
      screen.getByRole('spinbutton', { name: 'Water worlds maximum' }),
    ).toHaveValue(20);
    await fireEvent.click(
      screen.getByRole('checkbox', { name: 'Water worlds Any maximum' }),
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(onapply).toHaveBeenCalledExactlyOnceWith({ elw_count: { min: 2 } });
  });

  it('drops a zero minimum with no maximum but keeps an exact zero range', async () => {
    const { onapply } = filters({ elw_count: { min: 3 } });
    await open();
    await fireEvent.input(
      screen.getByRole('slider', { name: 'Earth-like minimum slider' }),
      { target: { value: '0' } },
    );
    expect(screen.getByRole('tab', { name: 'Planets 0' })).toBeInTheDocument();
    await number('Water worlds minimum', '0');
    await number('Water worlds maximum', '0');
    expect(screen.getByRole('tab', { name: 'Planets 1' })).toBeInTheDocument();
    await fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(onapply).toHaveBeenCalledExactlyOnceWith({
      ww_count: { min: 0, max: 0 },
    });
  });

  it('accepts typed counts above slider limits, bounds them at 999 and keeps endpoints ordered', async () => {
    const { onapply } = filters();
    await open();
    await number('Earth-like minimum', '100');
    expect(
      screen.getByRole('spinbutton', { name: 'Earth-like minimum' }),
    ).toHaveValue(100);
    expect(
      screen.getByRole('slider', { name: 'Earth-like minimum slider' }),
    ).toHaveValue('20');
    await number('Earth-like maximum', '50');
    expect(
      screen.getByRole('spinbutton', { name: 'Earth-like minimum' }),
    ).toHaveValue(50);
    await number('Earth-like minimum', '1200');
    expect(
      screen.getByRole('spinbutton', { name: 'Earth-like minimum' }),
    ).toHaveValue(999);
    expect(
      screen.getByRole('spinbutton', { name: 'Earth-like maximum' }),
    ).toHaveValue(999);
    await number('Water worlds minimum', '1.5');
    await number('Ammonia worlds maximum', '-1');
    await fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(onapply).toHaveBeenCalledExactlyOnceWith({
      elw_count: { min: 999, max: 999 },
    });
  });

  it('Reset clears only the draft, Cancel discards it, and reopening restores the applied value', async () => {
    const { onapply } = filters({
      elw_count: { min: 1 },
      neutron_count: { min: 2 },
    });
    const trigger = await open();
    await number('Earth-like minimum', '4');
    await fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    expect(screen.getByText('0 filters selected')).toBeVisible();
    expect(
      screen.getByRole('spinbutton', { name: 'Earth-like minimum' }),
    ).toHaveValue(null);
    expect(screen.getByRole('tab', { name: 'Stars 0' })).toBeVisible();
    expect(screen.getByText('Earth-like ≥ 1')).toBeVisible();
    await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onapply).not.toHaveBeenCalled();
    expect(trigger).toHaveFocus();
    await open();
    expect(
      screen.getByRole('spinbutton', { name: 'Earth-like minimum' }),
    ).toHaveValue(1);
    expect(screen.getByText('2 filters selected')).toBeVisible();
    await fireEvent.click(screen.getByRole('button', { name: 'Reset' }));
    await fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(onapply).toHaveBeenCalledExactlyOnceWith({});
  });

  it.each(['Escape', 'Close', 'outside', 'trigger'] as const)(
    'discards changes on %s and returns focus to the trigger',
    async (method) => {
      const { onapply } = filters();
      const trigger = await open();
      await number('Earth-like minimum', '1');
      if (method === 'Escape')
        await fireEvent.keyDown(window, { key: 'Escape' });
      else if (method === 'Close')
        await fireEvent.click(screen.getByRole('button', { name: 'Close' }));
      else if (method === 'outside') await fireEvent.click(document.body);
      else await fireEvent.click(trigger);
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(trigger).toHaveAttribute('aria-expanded', 'false');
      expect(trigger).toHaveFocus();
      expect(onapply).not.toHaveBeenCalled();
      await open();
      expect(
        screen.getByRole('spinbutton', { name: 'Earth-like minimum' }),
      ).toHaveValue(null);
    },
  );

  it('fits the visible viewport during keyboard resize and centers a narrow sheet', async () => {
    const listeners = new Map<string, EventListener>();
    const viewport = {
      width: 390,
      height: 300,
      offsetLeft: 4,
      offsetTop: 20,
      addEventListener: (type: string, listener: EventListener) =>
        listeners.set(type, listener),
      removeEventListener: (type: string) => listeners.delete(type),
    };
    const original = Object.getOwnPropertyDescriptor(window, 'visualViewport');
    Object.defineProperty(window, 'visualViewport', {
      configurable: true,
      value: viewport,
    });
    try {
      const { unmount } = filters();
      await open();
      const dialog = screen.getByRole('dialog');
      vi.spyOn(dialog, 'getBoundingClientRect').mockReturnValue({
        width: 360,
        height: 260,
        left: 0,
        top: 0,
        right: 360,
        bottom: 260,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      });
      listeners.get('resize')?.(new Event('resize'));
      await waitFor(() =>
        expect(dialog.style.maxHeight).toBe('min(42rem, 284px)'),
      );
      expect(dialog.style.maxWidth).toBe('374px');
      expect(dialog.style.left).toBe('19px');
      expect(dialog.style.top).toBe('40px');
      viewport.height = 200;
      viewport.offsetTop = 40;
      listeners.get('resize')?.(new Event('resize'));
      await waitFor(() =>
        expect(dialog.style.maxHeight).toBe('min(42rem, 184px)'),
      );
      expect(dialog.style.top).toBe('48px');
      viewport.height = 700;
      listeners.get('resize')?.(new Event('resize'));
      await waitFor(() =>
        expect(dialog.style.maxHeight).toBe('min(42rem, 684px)'),
      );
      unmount();
      expect(listeners.size).toBe(0);
    } finally {
      if (original) Object.defineProperty(window, 'visualViewport', original);
      else delete (window as { visualViewport?: unknown }).visualViewport;
    }
  });

  it('has no axe accessibility violations in the open pop-out', async () => {
    const { container } = filters();
    await open();
    for (const group of BODY_FILTER_GROUPS) {
      await fireEvent.click(
        screen.getByRole('tab', { name: `${group.label} 0` }),
      );
      expect(
        (
          await axe.run(container, {
            rules: { 'color-contrast': { enabled: false } },
          })
        ).violations,
      ).toEqual([]);
    }
  });
});

describe('applied body filters', () => {
  it('renders range chips, removes one filter and clears all through separate callbacks', async () => {
    const { onapply, onremove, onclear } = filters({
      elw_count: { min: 1 },
      landable_count: { min: 2, max: 5 },
      ring_count: { max: 3 },
    });
    expect(screen.getByText('Earth-like ≥ 1')).toBeVisible();
    expect(screen.getByText('Landable 2–5')).toBeVisible();
    expect(screen.getByText('Rings ≤ 3')).toBeVisible();
    const chips = screen.getByRole('group', { name: 'Applied body filters' });
    expect(chips).toHaveClass('kit-chip-row');
    expect(within(chips).getAllByText('Applied')).toHaveLength(3);
    expect(chips.querySelectorAll('.kit-chip')).toHaveLength(3);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    const accessibility = await axe.run(
      screen.getByRole('group', { name: 'Applied body filters' }),
      {
        rules: { 'color-contrast': { enabled: false } },
      },
    );
    expect(accessibility.violations).toEqual([]);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Remove Earth-like filter' }),
    );
    expect(onremove).toHaveBeenCalledExactlyOnceWith('elw_count');
    await fireEvent.click(screen.getByRole('button', { name: 'Clear all' }));
    expect(onclear).toHaveBeenCalledExactlyOnceWith();
    expect(onapply).not.toHaveBeenCalled();
  });

  it('explains the disabled trigger and retains removable paused chips', async () => {
    const { onremove, rerender } = filters(
      { elw_count: { min: 1 } },
      { disabled: true, paused: true },
    );
    const trigger = screen.getByRole('button', { name: 'Body filters' });
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveAccessibleDescription(
      'Body filters work when you search near a system with Any selected.',
    );
    const chips = screen.getByRole('group', { name: 'Paused body filters' });
    expect(chips).toHaveAccessibleDescription(
      'Body filters work when you search near a system with Any selected.',
    );
    expect(within(chips).queryByText('Applied')).not.toBeInTheDocument();
    expect(screen.getByText('Paused')).toBeVisible();
    await fireEvent.click(trigger);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await fireEvent.click(
      screen.getByRole('button', { name: 'Remove Earth-like filter' }),
    );
    expect(onremove).toHaveBeenCalledExactlyOnceWith('elw_count');
    await rerender({ disabled: false, paused: false });
    expect(trigger).toBeEnabled();
    expect(screen.queryByText('Paused')).not.toBeInTheDocument();
    expect(
      within(
        screen.getByRole('group', { name: 'Applied body filters' }),
      ).getByText('Applied'),
    ).toBeVisible();
    await open();
    const dialog = screen.getByRole('dialog');
    expect(dialog).not.toContainElement(chips);
    await number('Earth-like minimum', '7');
    expect(within(chips).getByText('Earth-like ≥ 1')).toBeVisible();
    await fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(chips).toBeVisible();
    expect(within(chips).getByText('Applied')).toBeVisible();
    await open();
    await rerender({ disabled: true });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
