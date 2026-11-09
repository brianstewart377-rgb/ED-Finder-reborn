import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { parseId64 } from '$lib/domain/id64';
import SourcesFreshness from './SourcesFreshness.svelte';

const props = {
  systemId: parseId64('42'),
  ratings: null,
  stations: [],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function openSources() {
  await fireEvent.click(
    screen.getByRole('button', { name: 'Sources & freshness' }),
  );
  return screen.getByRole('dialog', { name: 'Sources & freshness' });
}

describe('shared Inspect pop-out lifecycle', () => {
  it('retains its scroll and open state while inactive without handling dismissal or stealing focus on return', async () => {
    const view = render(SourcesFreshness, props);
    const dialog = await openSources();
    dialog.scrollTop = 150;
    const outside = document.createElement('button');
    outside.textContent = 'Other task';
    document.body.append(outside);
    try {
      await view.rerender({ active: false });
      outside.focus();
      expect(dialog).toHaveAttribute('hidden');
      expect(dialog).toHaveProperty('inert', true);
      await fireEvent.keyDown(outside, { key: 'Escape' });
      await fireEvent.click(outside);
      expect(dialog).toBeInTheDocument();

      await view.rerender({ active: true });

      expect(dialog).toBeVisible();
      expect(dialog).toHaveProperty('inert', false);
      expect(dialog.scrollTop).toBe(150);
      expect(outside).toHaveFocus();
      expect(
        screen.getByRole('button', { name: 'Sources & freshness' }),
      ).toHaveAttribute('aria-expanded', 'true');
    } finally {
      outside.remove();
    }
  });

  it('bounds the tool to the visual viewport and recomputes it after zoom or resize', async () => {
    const viewport = Object.assign(new EventTarget(), {
      width: 320,
      height: 480,
      offsetLeft: 20,
      offsetTop: 30,
    });
    vi.stubGlobal('visualViewport', viewport);
    render(SourcesFreshness, props);
    const dialog = await openSources();
    expect(dialog).toHaveStyle({ maxWidth: '304px', maxHeight: '464px' });
    viewport.width = 240;
    viewport.height = 300;
    viewport.dispatchEvent(new Event('resize'));
    await waitFor(() =>
      expect(dialog).toHaveStyle({ maxWidth: '224px', maxHeight: '284px' }),
    );
  });

  it('dismisses only the topmost tool on Escape and restores its own opener', async () => {
    render(SourcesFreshness, props);
    render(SourcesFreshness, { ...props, systemId: parseId64('43') });
    const triggers = screen.getAllByRole('button', {
      name: 'Sources & freshness',
    });
    await fireEvent.click(triggers[0]);
    const first = screen.getByRole('dialog');
    await fireEvent.click(triggers[1]);
    const second = screen
      .getAllByRole('dialog')
      .find((item) => item !== first)!;
    const overlayEscape = vi.fn();
    window.addEventListener('keydown', overlayEscape);
    try {
      await fireEvent.keyDown(window, { key: 'Escape' });
      expect(second).not.toBeInTheDocument();
      expect(first).toBeVisible();
      expect(triggers[1]).toHaveFocus();
      expect(overlayEscape).not.toHaveBeenCalled();

      await fireEvent.click(
        within(first).getByRole('button', { name: 'Close' }),
      );
      expect(triggers[0]).toHaveFocus();
    } finally {
      window.removeEventListener('keydown', overlayEscape);
    }
  });

  it('returns to the selected task control if the original trigger is removed', async () => {
    const context = document.createElement('section');
    context.dataset.systemId64 = '42';
    const task = document.createElement('button');
    task.setAttribute('role', 'tab');
    task.setAttribute('aria-selected', 'true');
    task.textContent = 'Overview';
    context.append(task);
    document.body.append(context);
    render(SourcesFreshness, { target: context, props });
    try {
      const dialog = await openSources();
      screen.getByRole('button', { name: 'Sources & freshness' }).remove();
      await fireEvent.click(
        within(dialog).getByRole('button', { name: 'Close' }),
      );
      expect(task).toHaveFocus();
    } finally {
      context.remove();
    }
  });
});
