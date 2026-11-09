import { cleanup, render, screen, within } from '@testing-library/svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import WorkspaceHeader from './WorkspaceHeader.svelte';

const { page } = vi.hoisted(() => ({
  page: { url: new URL('http://localhost/') },
}));

vi.mock('$app/state', () => ({ page }));

afterEach(cleanup);

describe('WorkspaceHeader', () => {
  it.each(['/', '/explore', '/inspect', '/plan', '/plan?system=42'])(
    'shows the product journey and marks only the current page at %s',
    (path) => {
      page.url = new URL(path, 'http://localhost');
      render(WorkspaceHeader);

      const navigation = within(
        screen.getByRole('navigation', { name: 'Product journey' }),
      );
      const links = navigation.getAllByRole('link');
      expect(links.map((link) => link.textContent)).toEqual([
        'Explore',
        'Inspect',
        'Plan',
      ]);
      for (const [index, href] of ['/explore', '/inspect', '/plan'].entries()) {
        expect(links[index]).toHaveAttribute('href', href);
        if (page.url.pathname === href) {
          expect(links[index]).toHaveAttribute('aria-current', 'page');
        } else {
          expect(links[index]).not.toHaveAttribute('aria-current');
        }
      }
    },
  );
});
