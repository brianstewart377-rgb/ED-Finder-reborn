import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/svelte';
import axe from 'axe-core';
import { tick } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { layoutOf, type BodyItem } from './layout';
import { nodeNamed, treeOf } from './recorded-systems';
import SystemMap from './SystemMap.svelte';
import { spokenOf } from './draw';
import { wholeOf, wholeView } from './view';

const SCREEN = { width: 1000, height: 560 };

function viewBoxOf(container: HTMLElement): number[] {
  return (container.querySelector('svg.sm')?.getAttribute('viewBox') ?? '')
    .split(' ')
    .map(Number);
}

function body(container: HTMLElement, key: string): SVGGElement {
  const found = container.querySelector<SVGGElement>(
    `.sm-body[data-body-key="${key}"]`,
  );
  if (!found) throw new Error(`body ${key} is not drawn`);
  return found;
}

describe('the System Map', () => {
  beforeEach(() => {
    // jsdom lays nothing out: give the map's stage a size, as a browser would.
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: SCREEN.width,
      bottom: SCREEN.height,
      width: SCREEN.width,
      height: SCREEN.height,
      toJSON: () => ({}),
    } as DOMRect);
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('draws every body as a button that says what it is, with plain balls where pictures cannot be made', () => {
    const tree = treeOf('sol');
    const { container } = render(SystemMap, { props: { tree } });
    const drawn = layoutOf(tree).items.filter(
      (item): item is BodyItem => item.kind === 'body',
    );
    const buttons = within(
      container.querySelector('svg.sm') as HTMLElement,
    ).getAllByRole('button');
    expect(buttons).toHaveLength(drawn.length);
    const earth = nodeNamed(tree, 'Earth');
    expect(screen.getByRole('button', { name: spokenOf(earth) })).toBe(
      body(container, earth.id),
    );
    expect(spokenOf(earth)).toMatch(/^Earth, Earth-like world; \d+ in orbit/u);
    // The test browser has neither WebGL nor a canvas to paint on.
    for (const button of buttons)
      expect(button).toHaveAttribute('data-look', 'plain');
    expect(screen.queryByRole('checkbox', { name: 'Spin' })).toBeNull();
  });

  it('shows the chosen body as chosen, and tells its parent page when another is chosen', async () => {
    const tree = treeOf('sol');
    const earth = nodeNamed(tree, 'Earth');
    const mars = nodeNamed(tree, 'Mars');
    const onSelect = vi.fn();
    const { container } = render(SystemMap, {
      props: { tree, selectedKey: earth.id, onSelect },
    });
    expect(body(container, earth.id)).toHaveAttribute('aria-pressed', 'true');
    expect(body(container, earth.id)).toHaveClass('sm-selected');
    expect(body(container, mars.id)).toHaveAttribute('aria-pressed', 'false');
    await fireEvent.click(body(container, mars.id));
    expect(onSelect).toHaveBeenCalledWith(mars.id);
  });

  it('is one stop for Tab; the arrow keys go from body to body, and Enter or Space chooses', async () => {
    const tree = treeOf('sol');
    const onSelect = vi.fn();
    const { container } = render(SystemMap, { props: { tree, onSelect } });
    const order = layoutOf(tree).items.filter(
      (item): item is BodyItem => item.kind === 'body',
    );
    const stops = container.querySelectorAll('.sm-body[tabindex="0"]');
    expect(stops).toHaveLength(1);
    expect(stops[0]).toBe(body(container, order[0]!.id));

    body(container, order[0]!.id).focus();
    await fireEvent.keyDown(document.activeElement as Element, {
      key: 'ArrowRight',
    });
    await tick();
    expect(document.activeElement).toBe(body(container, order[1]!.id));
    expect(body(container, order[1]!.id)).toHaveAttribute('tabindex', '0');
    expect(body(container, order[0]!.id)).toHaveAttribute('tabindex', '-1');

    await fireEvent.keyDown(document.activeElement as Element, { key: 'End' });
    await tick();
    expect(document.activeElement).toBe(body(container, order.at(-1)!.id));
    await fireEvent.keyDown(document.activeElement as Element, {
      key: 'Enter',
    });
    expect(onSelect).toHaveBeenLastCalledWith(order.at(-1)!.id);
    await fireEvent.keyDown(document.activeElement as Element, { key: 'Home' });
    await tick();
    await fireEvent.keyDown(document.activeElement as Element, { key: ' ' });
    expect(onSelect).toHaveBeenLastCalledWith(order[0]!.id);
  });

  it('brings a body reached with the keyboard into view', async () => {
    const tree = treeOf('sol');
    const { container } = render(SystemMap, { props: { tree } });
    await tick();
    const order = layoutOf(tree).items.filter(
      (item): item is BodyItem => item.kind === 'body',
    );
    const last = order.at(-1)!;
    const [x, , w] = viewBoxOf(container);
    // Sol opens at its left end: its last body is far off to the right.
    expect(last.x).toBeGreaterThan(x! + w!);
    body(container, order[0]!.id).focus();
    await fireEvent.keyDown(document.activeElement as Element, { key: 'End' });
    await tick();
    const [x2, , w2] = viewBoxOf(container);
    expect(last.x).toBeGreaterThan(x2!);
    expect(last.x).toBeLessThan(x2! + w2!);
  });

  it('moves with a drag, and a drag that ends on a body does not choose it', async () => {
    const tree = treeOf('sol');
    const onSelect = vi.fn();
    const { container } = render(SystemMap, { props: { tree, onSelect } });
    await tick();
    const stage = container.querySelector('.system-map-stage') as HTMLElement;
    const before = viewBoxOf(container);
    await fireEvent.pointerDown(stage, {
      pointerId: 1,
      clientX: 500,
      clientY: 300,
      button: 0,
      pointerType: 'mouse',
    });
    await fireEvent.pointerMove(window, {
      pointerId: 1,
      clientX: 380,
      clientY: 300,
    });
    await fireEvent.pointerUp(window, {
      pointerId: 1,
      clientX: 380,
      clientY: 300,
    });
    const after = viewBoxOf(container);
    expect(after[0]).toBeGreaterThan(before[0]!);
    expect(after[2]).toBe(before[2]);
    const earth = nodeNamed(tree, 'Earth');
    await fireEvent.click(body(container, earth.id));
    expect(onSelect).not.toHaveBeenCalled();
    // A plain click afterwards chooses as usual.
    await new Promise((resolve) => setTimeout(resolve, 0));
    await fireEvent.click(body(container, earth.id));
    expect(onSelect).toHaveBeenCalledWith(earth.id);
  });

  it('zooms with the wheel and the buttons, and shows the whole system when asked', async () => {
    const tree = treeOf('sol');
    const { container } = render(SystemMap, { props: { tree } });
    await tick();
    const stage = container.querySelector('.system-map-stage') as HTMLElement;
    const opened = viewBoxOf(container);
    const wheel = new WheelEvent('wheel', {
      deltaY: -100,
      clientX: 500,
      clientY: 280,
      cancelable: true,
    });
    stage.dispatchEvent(wheel);
    await tick();
    expect(wheel.defaultPrevented).toBe(true);
    expect(viewBoxOf(container)[2]).toBeLessThan(opened[2]!);
    await fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }));
    await fireEvent.click(
      screen.getByRole('button', { name: 'Show the whole system' }),
    );
    const whole = wholeView(wholeOf(layoutOf(tree).box), SCREEN);
    expect(viewBoxOf(container)).toEqual(
      [whole.x, whole.y, whole.w, whole.h].map((value) =>
        Number(value.toFixed(2)),
      ),
    );
  });

  it('marks what is built at a body, and the ground slots the site predicts there', () => {
    const tree = treeOf('lhs3447');
    const { container } = render(SystemMap, { props: { tree } });
    const a3 = nodeNamed(tree, 'LHS 3447 A 3');
    expect(
      body(container, a3.id).querySelector('.sm-ground')?.textContent,
    ).toBe('▲5□4');
    expect(body(container, a3.id).getAttribute('aria-label')).toContain(
      '4 ground slots predicted',
    );
    const stations = container.querySelectorAll('.sm-station');
    const inOrbit = [...tree.nodes.values()].flatMap((node) => node.orbit);
    expect(stations).toHaveLength(inOrbit.length);
    expect(container.querySelectorAll('.sm-station.sm-guess')).toHaveLength(
      inOrbit.filter((station) => station.guess).length,
    );
  });

  it('draws a planet’s rings in front of and behind it, and a star’s belt among its planets', () => {
    const tree = treeOf('sol');
    const { container } = render(SystemMap, { props: { tree } });
    const saturn = body(container, nodeNamed(tree, 'Saturn').id);
    expect(saturn.querySelectorAll('.sm-ring')).toHaveLength(2);
    const belt = container.querySelector('.sm-belt title');
    expect(belt?.textContent).toBe('Asteroid Belt, rocky belt');
  });

  it('marks a body placed by a guess', () => {
    const tree = treeOf('polaris');
    const { container } = render(SystemMap, { props: { tree } });
    const guessed = body(container, nodeNamed(tree, 'Polaris B 1 d').id);
    expect(guessed).toHaveClass('sm-guessed');
    expect(guessed.getAttribute('aria-label')).toContain('placed by its name');
    expect(
      body(container, nodeNamed(tree, 'Polaris B 1 a').id),
    ).not.toHaveClass('sm-guessed');
  });

  it('has no axe violations', async () => {
    const { container } = render(SystemMap, {
      props: { tree: treeOf('lhs3447') },
    });
    const result = await axe.run(container, {
      rules: {
        'color-contrast': { enabled: false },
        region: { enabled: false },
      },
    });
    expect(result.violations).toEqual([]);
  });
});
