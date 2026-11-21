import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/svelte';
import axe from 'axe-core';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ArchetypePicker from './ArchetypePicker.svelte';
import {
  ANY_ARCHETYPE_DESCRIPTION,
  ARCHETYPES,
  type ArchetypeKey,
} from './archetypes';

afterEach(cleanup);

function picker(archetype: 'any' | ArchetypeKey = 'any', anyFloor = false) {
  const onArchetypeChange = vi.fn();
  const onMinimumTierChange = vi.fn();
  const result = render(ArchetypePicker, {
    archetype,
    minimumTier: archetype === 'any' ? 'C' : 'B',
    anyFloor,
    onArchetypeChange,
    onMinimumTierChange,
  });
  return { ...result, onArchetypeChange, onMinimumTierChange };
}

describe('native archetype picker', () => {
  const typeSelect = () =>
    screen.getByRole('combobox', { name: 'Colony archetype' });

  it('offers Any and the eight colony types in one dropdown, by their short labels', () => {
    picker();
    const select = typeSelect();
    expect(select).toHaveProperty('tagName', 'SELECT');
    expect(select).toHaveValue('any');
    expect(
      within(select)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Any', ...ARCHETYPES.map((entry) => entry.label)]);
    // The chosen type's description is shown under it, and read with it.
    expect(select).toHaveAccessibleDescription(ANY_ARCHETYPE_DESCRIPTION);
    expect(screen.getAllByText(ANY_ARCHETYPE_DESCRIPTION)).toHaveLength(1);
    // Galaxy-wide the list is best first already: no "Show only" for Any.
    expect(screen.queryByLabelText('Show only')).not.toBeInTheDocument();
  });

  it('commits the chosen type and describes it', async () => {
    const { onArchetypeChange, rerender } = picker();
    await fireEvent.change(typeSelect(), { target: { value: 'paradise' } });
    expect(onArchetypeChange).toHaveBeenCalledExactlyOnceWith('paradise');
    await rerender({ archetype: 'paradise', minimumTier: 'B' });
    const paradise = ARCHETYPES.find((entry) => entry.key === 'paradise')!;
    expect(typeSelect()).toHaveValue('paradise');
    expect(typeSelect()).toHaveAccessibleDescription(paradise.description);
    await fireEvent.change(typeSelect(), { target: { value: 'any' } });
    expect(onArchetypeChange).toHaveBeenLastCalledWith('any');
  });

  it('keeps type keyboard input native and commits only its change event', async () => {
    const { onArchetypeChange } = picker();
    const select = typeSelect();
    select.focus();
    expect(await fireEvent.keyDown(select, { key: 'ArrowDown' })).toBe(true);
    expect(onArchetypeChange).not.toHaveBeenCalled();
    // jsdom does not implement the native select's keyboard default action.
    await fireEvent.change(select, { target: { value: 'stronghold' } });
    expect(onArchetypeChange).toHaveBeenCalledExactlyOnceWith('stronghold');
    expect(select).toHaveFocus();
  });

  it('shows only B, A and S minimum tiers for a selected archetype', async () => {
    const { onMinimumTierChange } = picker('manufacturing_hub');
    const select = screen.getByRole('combobox', { name: 'Show only' });
    expect(select).toHaveValue('B');
    expect(
      within(select)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Tier B or better', 'Tier A or better', 'Tier S']);
    await fireEvent.change(select, { target: { value: 'A' } });
    expect(onMinimumTierChange).toHaveBeenCalledExactlyOnceWith('A');
  });

  it('offers Any all systems or C, B, A and S near a system, starting at C', async () => {
    const { onMinimumTierChange } = picker('any', true);
    const select = screen.getByRole('combobox', { name: 'Show only' });
    expect(select).toHaveValue('C');
    expect(
      within(select)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual([
      'All systems',
      'Tier C or better',
      'Tier B or better',
      'Tier A or better',
      'Tier S',
    ]);
    await fireEvent.change(select, { target: { value: 'all' } });
    expect(onMinimumTierChange).toHaveBeenCalledExactlyOnceWith('all');
  });

  it('keeps native controls and descriptions accessible', async () => {
    const { container } = picker('flexible');
    expect(
      (
        await axe.run(container, {
          rules: { 'color-contrast': { enabled: false } },
        })
      ).violations,
    ).toEqual([]);
  });

  it('keeps tier keyboard input native and commits only its change event', async () => {
    const { onMinimumTierChange, onArchetypeChange } = picker('paradise');
    const select = screen.getByRole('combobox', { name: 'Show only' });
    select.focus();
    expect(select).toHaveFocus();
    expect(await fireEvent.keyDown(select, { key: 'ArrowDown' })).toBe(true);
    expect(onMinimumTierChange).not.toHaveBeenCalled();
    expect(onArchetypeChange).not.toHaveBeenCalled();
    // jsdom does not implement the native select's keyboard default action.
    await fireEvent.change(select, { target: { value: 'S' } });
    expect(onMinimumTierChange).toHaveBeenCalledExactlyOnceWith('S');
    expect(select).toHaveFocus();
  });

  it('disables the type and minimum tier together', async () => {
    const { rerender, onArchetypeChange, onMinimumTierChange } =
      picker('flexible');
    await rerender({ disabled: true });
    expect(typeSelect()).toBeDisabled();
    expect(screen.getByRole('combobox', { name: 'Show only' })).toBeDisabled();
    expect(onArchetypeChange).not.toHaveBeenCalled();
    expect(onMinimumTierChange).not.toHaveBeenCalled();
  });

  it('does not turn body counts or economies into speculative weight controls', async () => {
    const { rerender, container } = picker();
    for (const archetype of ['any', 'manufacturing_hub'] as const) {
      await rerender({ archetype });
      expect(within(typeSelect()).getAllByRole('option')).toHaveLength(9);
      expect(screen.queryByRole('slider')).not.toBeInTheDocument();
      expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
      expect(container.querySelector('[name*="weight"]')).toBeNull();
      expect(screen.queryByText(/weight/i)).not.toBeInTheDocument();
    }
  });
});
