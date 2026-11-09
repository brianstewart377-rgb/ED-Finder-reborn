import { QueryClient } from '@tanstack/svelte-query';
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { tick } from 'svelte';
import axe from 'axe-core';
import {
  autocompleteSystems,
  type AutocompleteSystem,
  type AutocompleteSystemsResponse,
} from '$lib/api/client';
import { queryKeys } from '$lib/api/query';
import { parseId64 } from '$lib/domain/id64';
import ReferenceSystemComboboxTestHost from './ReferenceSystemComboboxTestHost.svelte';

vi.mock('$lib/api/client', () => ({ autocompleteSystems: vi.fn() }));

const sol: AutocompleteSystem = {
  id64: parseId64('10477373803'),
  name: 'Sol',
  x: 0,
  y: 0,
  z: 0,
};
const solaris: AutocompleteSystem = {
  id64: parseId64('9007199254740993'),
  name: 'Solaris',
  x: 10,
  y: 20,
  z: 30,
};
const mockedAutocomplete = vi.mocked(autocompleteSystems);
let client: QueryClient;

function renderCombobox(
  props: { invalid?: boolean; describedBy?: string } = {},
) {
  render(ReferenceSystemComboboxTestHost, { client, ...props });
  return screen.getByRole('combobox', { name: 'Reference system' });
}

async function showSuggestions(input: HTMLElement) {
  await fireEvent.input(input, { target: { value: 'So' } });
  return screen.findAllByRole('option');
}

describe('home reference-system combobox', () => {
  beforeEach(() => {
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: Infinity } },
    });
    mockedAutocomplete.mockReset();
    mockedAutocomplete.mockResolvedValue({ results: [sol, solaris] });
  });

  afterEach(() => {
    cleanup();
    client.clear();
  });

  it('waits for two trimmed characters and sends the trimmed query to the existing API', async () => {
    const input = renderCombobox();
    await tick();
    expect(mockedAutocomplete).not.toHaveBeenCalled();
    for (const value of [' ', 'S', ' S ']) {
      await fireEvent.input(input, { target: { value } });
      expect(mockedAutocomplete).not.toHaveBeenCalled();
      expect(input).toHaveAttribute('aria-expanded', 'false');
    }

    await fireEvent.input(input, { target: { value: ' So ' } });
    await screen.findAllByRole('option');
    expect(mockedAutocomplete).toHaveBeenCalledExactlyOnceWith(
      'So',
      expect.any(AbortSignal),
    );
    expect(input).toHaveAttribute('aria-autocomplete', 'list');
    expect(input).toHaveAttribute(
      'aria-controls',
      screen.getByRole('listbox').id,
    );
  });

  it('shares Explore’s lowercase, trimmed cache key and one-minute freshness', async () => {
    client.setQueryData(queryKeys.autocomplete('sol'), { results: [sol] });
    const input = renderCombobox();
    await fireEvent.input(input, { target: { value: '  SoL ' } });
    expect(await screen.findByRole('option', { name: /Sol/ })).toBeVisible();
    expect(mockedAutocomplete).not.toHaveBeenCalled();

    await fireEvent.input(input, { target: { value: 'sol' } });
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(mockedAutocomplete).not.toHaveBeenCalled();
  });

  it('supports arrow wrapping, Home/End, and Enter selection without submitting the form', async () => {
    const input = renderCombobox();
    input.focus();
    const options = await showSuggestions(input);
    expect(input).not.toHaveAttribute('aria-activedescendant');

    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(input).toHaveAttribute('aria-activedescendant', options[0].id);
    expect(options[0]).toHaveAttribute('aria-selected', 'true');
    await fireEvent.keyDown(input, { key: 'ArrowUp' });
    expect(input).toHaveAttribute('aria-activedescendant', options[1].id);
    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(input).toHaveAttribute('aria-activedescendant', options[0].id);
    await fireEvent.keyDown(input, { key: 'End' });
    expect(input).toHaveAttribute('aria-activedescendant', options[1].id);
    await fireEvent.keyDown(input, { key: 'Home' });
    expect(input).toHaveAttribute('aria-activedescendant', options[0].id);
    await fireEvent.keyDown(input, { key: 'ArrowDown' });

    expect(await fireEvent.keyDown(input, { key: 'Enter' })).toBe(false);
    expect(screen.getByLabelText('Selected system')).toHaveTextContent(
      solaris.id64,
    );
    expect(input).toHaveValue('Solaris');
    expect(input).toHaveFocus();
    expect(input).toHaveAttribute('aria-expanded', 'false');
    expect(input).not.toHaveAttribute('aria-activedescendant');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(mockedAutocomplete).toHaveBeenCalledTimes(1);
    // A subsequent Enter belongs to the parent form.
    expect(await fireEvent.keyDown(input, { key: 'Enter' })).toBe(true);
  });

  it('dismisses with Escape, reopens with arrow keys, and closes on blur', async () => {
    const input = renderCombobox();
    await showSuggestions(input);
    await fireEvent.keyDown(input, { key: 'ArrowDown' });
    expect(await fireEvent.keyDown(input, { key: 'Escape' })).toBe(false);
    expect(input).toHaveAttribute('aria-expanded', 'false');
    expect(input).not.toHaveAttribute('aria-activedescendant');

    await fireEvent.keyDown(input, { key: 'ArrowUp' });
    const options = screen.getAllByRole('option');
    expect(input).toHaveAttribute('aria-activedescendant', options[1].id);
    await fireEvent.blur(input);
    expect(input).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('selects an exact id64 on click and immediately clears it on any later edit', async () => {
    const input = renderCombobox();
    const options = await showSuggestions(input);
    expect(await fireEvent.mouseDown(options[1])).toBe(false);
    await fireEvent.click(options[1]);
    expect(input).toHaveValue('Solaris');
    expect(screen.getByLabelText('Selected system')).toHaveTextContent(
      '9007199254740993',
    );
    expect(mockedAutocomplete).toHaveBeenCalledTimes(1);

    // Even typing the same visible name is an edit, not a new selection.
    await fireEvent.input(input, { target: { value: 'Solaris' } });
    expect(screen.getByLabelText('Selected system')).toHaveTextContent('None');
    await fireEvent.input(input, { target: { value: '' } });
    expect(input).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
  });

  it('never offers a stale query response as the current selection', async () => {
    let finishOldQuery!: (result: AutocompleteSystemsResponse) => void;
    mockedAutocomplete.mockImplementation((query) =>
      query === 'Old'
        ? new Promise((resolve) => {
            finishOldQuery = resolve;
          })
        : Promise.resolve({ results: [solaris] }),
    );
    const input = renderCombobox();
    await fireEvent.input(input, { target: { value: 'Old' } });
    expect(await screen.findByRole('status', { name: '' })).toHaveTextContent(
      'Searching catalogue…',
    );
    await fireEvent.input(input, { target: { value: 'New' } });
    await screen.findByRole('option', { name: /Solaris/ });
    finishOldQuery({ results: [sol] });
    await tick();
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByRole('option')).toHaveTextContent('Solaris');
    await fireEvent.keyDown(input, { key: 'Enter' });
    expect(screen.getByLabelText('Selected system')).toHaveTextContent(
      solaris.id64,
    );
  });

  it('keeps cleared input closed when an in-flight response completes', async () => {
    let finishQuery!: (result: AutocompleteSystemsResponse) => void;
    mockedAutocomplete.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishQuery = resolve;
        }),
    );
    const input = renderCombobox();
    await fireEvent.input(input, { target: { value: 'So' } });
    await screen.findByRole('status', { name: '' });
    await fireEvent.input(input, { target: { value: '' } });
    finishQuery({ results: [sol] });
    await tick();
    expect(input).toHaveValue('');
    expect(input).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Selected system')).toHaveTextContent('None');
  });

  it('announces an empty result without accepting typed text as a selection', async () => {
    mockedAutocomplete.mockResolvedValue({ results: [] });
    const input = renderCombobox();
    await fireEvent.input(input, { target: { value: 'Missing system' } });
    await waitFor(() =>
      expect(screen.getByRole('status', { name: '' })).toHaveTextContent(
        'No matching systems.',
      ),
    );
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
    expect(await fireEvent.keyDown(input, { key: 'Enter' })).toBe(true);
    expect(screen.getByLabelText('Selected system')).toHaveTextContent('None');
    expect(
      (
        await axe.run(document.body, {
          runOnly: [
            'aria-required-attr',
            'aria-required-children',
            'aria-valid-attr-value',
            'aria-input-field-name',
          ],
        })
      ).violations,
    ).toEqual([]);
  });

  it('announces lookup errors and allows the next query to recover', async () => {
    mockedAutocomplete.mockRejectedValueOnce(new Error('Unavailable'));
    const input = renderCombobox();
    await fireEvent.input(input, { target: { value: 'So' } });
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'System suggestions are unavailable. Try again later.',
    );
    expect(screen.queryByRole('option')).not.toBeInTheDocument();
    await fireEvent.input(input, { target: { value: 'Sol' } });
    expect(await screen.findAllByRole('option')).toHaveLength(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('exposes invalid descriptions and returns focus for parent-form validation', async () => {
    const input = renderCombobox({
      invalid: true,
      describedBy: 'reference-error',
    });
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input).toHaveAttribute('aria-describedby', 'reference-error');
    await fireEvent.click(screen.getByRole('button', { name: 'Focus field' }));
    expect(input).toHaveFocus();
    expect(mockedAutocomplete).not.toHaveBeenCalled();
  });
});
