import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/svelte';
import axe from 'axe-core';
import { afterEach, describe, expect, it, vi } from 'vitest';
import SlotEditor from './SlotEditor.svelte';

afterEach(cleanup);

describe('commander slot editor', () => {
  it('retains the open editor when a Plan task tab receives a pointer press', async () => {
    render(SlotEditor, {
      label: 'A 1',
      kind: 'orbital',
      used: 0,
      value: 4,
      onchange: vi.fn(),
    });
    await fireEvent.click(
      screen.getByRole('button', {
        name: 'Orbital slots on A 1: 0 used, 4 total',
      }),
    );
    const tablist = document.createElement('div');
    tablist.setAttribute('role', 'tablist');
    tablist.setAttribute('aria-label', 'Plan tasks');
    const tab = document.createElement('button');
    tablist.append(tab);
    document.body.append(tablist);
    await fireEvent.pointerDown(tab);
    expect(
      screen.getByRole('dialog', { name: 'Orbital slots on A 1' }),
    ).toBeVisible();
    tablist.remove();
  });

  it('shows unknown counts and commits commander changes bounded to 0–64', async () => {
    const onchange = vi.fn();
    const { rerender } = render(SlotEditor, {
      label: 'A 1',
      kind: 'orbital',
      used: 2,
      value: null,
      onchange,
    });
    const trigger = screen.getByRole('button', {
      name: 'Orbital slots on A 1: 2 used, unknown total',
    });
    expect(trigger).toHaveTextContent('2/?');
    await fireEvent.click(trigger);
    expect(screen.getByText(/entered by the commander/)).toHaveTextContent(
      'Copy orbital slots from Architect Mode.',
    );
    await fireEvent.click(
      screen.getByRole('button', { name: 'Increase orbital slots' }),
    );
    expect(onchange).toHaveBeenLastCalledWith(1);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Decrease orbital slots' }),
    );
    expect(onchange).toHaveBeenLastCalledWith(0);
    expect(
      screen.getByRole('button', { name: 'Decrease orbital slots' }),
    ).toBeDisabled();
    await fireEvent.click(screen.getByRole('button', { name: 'Done' }));

    await rerender({ value: 63 });
    await fireEvent.click(trigger);
    await fireEvent.click(
      screen.getByRole('button', { name: 'Increase orbital slots' }),
    );
    expect(onchange).toHaveBeenLastCalledWith(64);
    expect(
      screen.getByRole('button', { name: 'Increase orbital slots' }),
    ).toBeDisabled();
    await fireEvent.click(screen.getByRole('button', { name: 'Unknown' }));
    expect(onchange).toHaveBeenLastCalledWith(null);
    expect(screen.getByRole('status')).toHaveTextContent('Unknown');
  });

  it('focuses the editor, traps Tab, and returns focus on Escape and Done', async () => {
    render(SlotEditor, {
      label: 'B 2',
      kind: 'surface',
      used: 0,
      value: 4,
      onchange: vi.fn(),
    });
    const trigger = screen.getByRole('button', {
      name: 'Surface slots on B 2: 0 used, 4 total',
    });
    trigger.focus();
    await fireEvent.click(trigger);
    const editor = screen.getByRole('dialog', {
      name: 'Surface slots on B 2',
    });
    await waitFor(() => expect(editor).toHaveFocus());
    await fireEvent.keyDown(window, { key: 'Tab' });
    expect(
      screen.getByRole('button', { name: 'Decrease surface slots' }),
    ).toHaveFocus();
    await fireEvent.keyDown(window, { key: 'Tab', shiftKey: true });
    expect(screen.getByRole('button', { name: 'Done' })).toHaveFocus();
    await fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    await fireEvent.click(trigger);
    await fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('marks over-capacity counts with words as well as styling', () => {
    render(SlotEditor, {
      label: 'A Belt',
      kind: 'orbital',
      used: 3,
      value: 1,
      onchange: vi.fn(),
    });
    expect(
      screen.getByRole('button', {
        name: 'Orbital slots on A Belt: 3 used, 1 total, over capacity',
      }),
    ).toHaveClass('over-capacity');
    expect(screen.getByText('Over capacity')).toBeInTheDocument();
  });

  it('keeps unavailable surface slots non-editable', async () => {
    render(SlotEditor, {
      label: 'A',
      kind: 'surface',
      used: 0,
      value: null,
      disabled: true,
      onchange: vi.fn(),
    });
    const trigger = screen.getByRole('button', {
      name: 'Surface slots on A: not available',
    });
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveTextContent('–');
    await fireEvent.click(trigger);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes when clicking elsewhere without taking focus from that control', async () => {
    render(SlotEditor, {
      label: 'A',
      kind: 'orbital',
      used: 0,
      value: 2,
      onchange: vi.fn(),
    });
    await fireEvent.click(screen.getByRole('button'));
    const outside = document.createElement('button');
    outside.textContent = 'Another body';
    document.body.append(outside);
    outside.focus();
    await fireEvent.pointerDown(outside);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(outside).toHaveFocus();
    outside.remove();
  });

  it('has no automated accessibility violations while editing', async () => {
    const { container } = render(SlotEditor, {
      label: 'A',
      kind: 'orbital',
      used: 0,
      value: null,
      onchange: vi.fn(),
    });
    await fireEvent.click(screen.getByRole('button'));
    const result = await axe.run(container, {
      rules: { 'color-contrast': { enabled: false } },
    });
    expect(result.violations).toEqual([]);
  });
});
