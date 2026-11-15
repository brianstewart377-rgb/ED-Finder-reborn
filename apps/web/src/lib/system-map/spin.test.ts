import { describe, expect, it } from 'vitest';
import { cosmosAsk } from './cosmos-look';
import { lookOf } from './draw';
import { nodeNamed, treeOf } from './recorded-systems';
import {
  SPIN_EVERY_MS,
  SPIN_FRAMES_BEFORE_JUDGING,
  SPIN_RESTS_AFTER_MS,
  SPIN_TOO_SLOW_MS,
  startSpinning,
  type SpinTarget,
} from './spin';

/** A clock the test moves by hand, and a stand-in for Cosmos that takes as long as it is told. */
function bench(msPerBody: number) {
  let now = 0;
  let step: ((time: number) => void) | null = null;
  const clock = {
    now: () => now,
    frame: (next: (time: number) => void) => {
      step = next;
      return 1;
    },
    cancel: () => {
      step = null;
    },
  };
  const asked: { type: string; time: number; resolution?: number }[] = [];
  const generator = {
    render: (params: { type: string; resolution?: number }, time: number) => {
      asked.push({ type: params.type, time, resolution: params.resolution });
      now += msPerBody;
      return {} as HTMLCanvasElement;
    },
  };
  /** Moves the clock on to `time` and runs the frame waiting there. */
  const tick = (time: number) => {
    now = time;
    const waiting = step;
    step = null;
    waiting?.(time);
  };
  return { clock, generator, asked, tick, running: () => step !== null };
}

function pen() {
  const drawn: number[] = [];
  return {
    drawn,
    pen: {
      clearRect: () => {},
      drawImage: () => drawn.push(1),
    } as unknown as CanvasRenderingContext2D,
  };
}

function targetsOf(names: string[]): SpinTarget[] {
  const tree = treeOf('sol');
  return names.map((name) => {
    const node = nodeNamed(tree, name);
    const look = lookOf(node);
    return {
      id: node.id,
      ask: cosmosAsk({
        family: look.family,
        star: look.star,
        colour: look.colour,
        halo: look.halo,
      }),
      seed: 1,
      pixels: 64,
      pen: pen().pen,
    };
  });
}

describe('spinning', () => {
  it('draws each body on screen twelve times a second, one step on each time', () => {
    const { clock, generator, asked, tick } = bench(0.1);
    const drew: string[] = [];
    const targets = targetsOf(['Earth', 'Jupiter']);
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => 0,
        drew: (id) => drew.push(id),
        tooSlow: () => {},
        failed: () => {},
      },
      clock,
    );
    tick(0);
    tick(SPIN_EVERY_MS / 2); // too soon: nothing drawn
    tick(SPIN_EVERY_MS);
    tick(SPIN_EVERY_MS * 2);
    expect(asked.map((one) => one.type)).toEqual([
      'terrain_wet',
      'gas_giant_ringed',
      'terrain_wet',
      'gas_giant_ringed',
      'terrain_wet',
      'gas_giant_ringed',
    ]);
    expect(asked.map((one) => one.time)).toEqual(
      [0, 0, 1 / 12, 1 / 12, 2 / 12, 2 / 12].map((t) => expect.closeTo(t)),
    );
    expect(drew).toEqual(targets.map((target) => target.id));
  });

  it('draws bodies of one size together, so Cosmos makes its drawing surface anew as seldom as it can', () => {
    // Cosmos draws on one surface and makes it anew whenever the size asked
    // for differs from the last. Seen on a gaming PC: Maia's 56 bodies took
    // 9.7 ms a step, and spinning switched itself off.
    const { clock, generator, asked, tick } = bench(0.1);
    const sizes = [128, 32, 512, 32, 128, 64, 32, 512];
    const names = [
      'Sol',
      'Mercury',
      'Venus',
      'Earth',
      'Mars',
      'Jupiter',
      'Saturn',
      'Uranus',
    ];
    const targets = targetsOf(names).map((target, at) => ({
      ...target,
      pixels: sizes[at],
    }));
    const drew: string[] = [];
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => 0,
        drew: (id) => drew.push(id),
        tooSlow: () => {},
        failed: () => {},
      },
      clock,
    );
    tick(0);
    const order = asked.map((one) => one.resolution);
    expect(order).toEqual([32, 32, 32, 64, 128, 128, 512, 512]);
    // Listed as they came, the size would change seven times; now three.
    const changes = (list: (number | undefined)[]) =>
      list.filter((size, at) => at > 0 && size !== list[at - 1]).length;
    expect(changes(sizes)).toBe(7);
    expect(changes(order)).toBe(3);
    // Every body is still drawn, and the page's own list is left as it was.
    expect([...drew].sort()).toEqual(targets.map((t) => t.id).sort());
    expect(targets.map((target) => target.pixels)).toEqual(sizes);
  });

  it('rests after 20 seconds without the mouse, the wheel or a key, and carries on from where it stopped', () => {
    const { clock, generator, asked, tick } = bench(0.1);
    let stirred = 0;
    startSpinning(
      generator,
      {
        targets: () => targetsOf(['Earth']),
        lastStir: () => stirred,
        drew: () => {},
        tooSlow: () => {},
        failed: () => {},
      },
      clock,
    );
    tick(0);
    tick(SPIN_RESTS_AFTER_MS + 1000);
    tick(SPIN_RESTS_AFTER_MS + 2000);
    expect(asked).toHaveLength(1);
    stirred = SPIN_RESTS_AFTER_MS + 2500;
    tick(SPIN_RESTS_AFTER_MS + 3000);
    expect(asked).toHaveLength(2);
    expect(asked[1]!.time).toBeCloseTo(1 / 12);
  });

  it('stops itself, and says how slow it was, when a step takes more than 8 ms on average', () => {
    // A whole system on screen on a machine with no graphics card to speak of.
    const { clock, generator, tick, running } = bench(3);
    let slow: number | null = null;
    const targets = targetsOf(['Earth', 'Jupiter', 'Saturn', 'Uranus']);
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => Infinity,
        drew: () => {},
        tooSlow: (ms) => (slow = ms),
        failed: () => {},
      },
      clock,
    );
    for (let frame = 0; frame < SPIN_FRAMES_BEFORE_JUDGING; frame += 1)
      tick(frame * SPIN_EVERY_MS * 2);
    expect(slow).toBeCloseTo(12);
    expect(slow!).toBeGreaterThan(SPIN_TOO_SLOW_MS);
    expect(running()).toBe(false);
  });

  it('carries on where a step is cheap', () => {
    const { clock, generator, tick, running } = bench(1);
    let slow: number | null = null;
    const targets = targetsOf(['Earth', 'Jupiter']);
    startSpinning(
      generator,
      {
        targets: () => targets,
        lastStir: () => Infinity,
        drew: () => {},
        tooSlow: (ms) => (slow = ms),
        failed: () => {},
      },
      clock,
    );
    for (let frame = 0; frame < SPIN_FRAMES_BEFORE_JUDGING * 3; frame += 1)
      tick(frame * SPIN_EVERY_MS * 2);
    expect(slow).toBeNull();
    expect(running()).toBe(true);
  });

  it('stops, leaving the still pictures, when Cosmos cannot go on', () => {
    const { clock, tick, running } = bench(0);
    let failed = false;
    startSpinning(
      {
        render: () => {
          throw new Error('context lost');
        },
      },
      {
        targets: () => targetsOf(['Earth']),
        lastStir: () => 0,
        drew: () => {},
        tooSlow: () => {},
        failed: () => (failed = true),
      },
      clock,
    );
    tick(0);
    expect(failed).toBe(true);
    expect(running()).toBe(false);
  });
});
