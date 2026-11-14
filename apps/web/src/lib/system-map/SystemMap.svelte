<!--
  The System Map: one system drawn in the manner of the game's own map, as an
  SVG picture the browser draws itself. Each star heads a row, its planets
  follow along a line, each planet's moons hang below it. Bodies are drawn at
  once as shaded balls; their pictures (Cosmos, or painted where there is no
  WebGL) follow one at a time, and may turn (spin.ts).

  Moving about: drag to pan, the wheel or a pinch to zoom. Every body is a
  button: Tab reaches the map, the arrow keys go from body to body in reading
  order (the view follows), Enter or Space chooses one, + and - zoom, 0 shows
  the whole system.
-->
<script lang="ts">
  import { onDestroy, tick, untrack } from 'svelte';
  import { SvelteMap, SvelteSet } from 'svelte/reactivity';
  import {
    RING_TILT,
    STATION_GLYPHS,
    beltColour,
    lookOf,
    n,
    rgb1,
    ringShadow,
    ringShapes,
    rocksOf,
    seedOf,
    spokenOf,
    spokenOfBelt,
    spokenOfStation,
    starStops,
    stripesOf,
    type Look,
  } from './draw';
  import {
    layoutOf,
    type BeltItem,
    type BodyItem,
    type StationItem,
  } from './layout';
  import {
    askOf,
    cosmos,
    makePictures,
    pictureMaker,
    pictureName,
    type Picture,
    type PictureJob,
    type PictureMaker,
  } from './pictures';
  import { SPIN_PIXELS, startSpinning, type SpinTarget } from './spin';
  import type { MapTree } from './tree';
  import {
    centredOn,
    openingView,
    pannedView,
    resizedView,
    shows,
    touches,
    wholeOf,
    wholeView,
    zoomedView,
    type Size,
    type View,
  } from './view';

  let {
    tree,
    selectedKey = null,
    onSelect,
  }: {
    tree: MapTree;
    selectedKey?: string | null;
    onSelect?: (bodyKey: string) => void;
  } = $props();

  const uid = $props.id();
  const ref = (name: string) => `sm-${uid}-${name}`;

  const layout = $derived(layoutOf(tree));
  const whole = $derived(wholeOf(layout.box));
  const bodies = $derived(
    layout.items.filter((item): item is BodyItem => item.kind === 'body'),
  );
  const stations = $derived(
    layout.items.filter((item): item is StationItem => item.kind === 'station'),
  );
  const belts = $derived(
    layout.items.filter((item): item is BeltItem => item.kind === 'belt'),
  );
  const looks = $derived(
    new Map<string, Look>(bodies.map((item) => [item.id, lookOf(item.node)])),
  );
  const lookFor = (item: BodyItem): Look =>
    looks.get(item.id) ?? lookOf(item.node);

  // ---- the part on screen ---------------------------------------------------
  let stage = $state<HTMLDivElement>();
  let size = $state<Size>({ width: 0, height: 0 });
  let view = $state<View | null>(null);
  let dragging = $state(false);
  let openedFor: string | null = null;
  let lastSize: Size | null = null;
  const shown = $derived(view ?? whole);
  const viewBox = $derived(
    [shown.x, shown.y, shown.w, shown.h].map((value) => n(value)).join(' '),
  );

  $effect(() => {
    const element = stage;
    if (!element) return;
    const measure = () => {
      const box = element.getBoundingClientRect();
      const known = untrack(() => size);
      if (box.width !== known.width || box.height !== known.height)
        size = { width: box.width, height: box.height };
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  });

  // A new system opens on its readable view; the same system refreshed keeps
  // the view the person has moved to; a resized map keeps its middle.
  $effect(() => {
    const current = size;
    const id = tree.id64;
    const box = whole;
    if (current.width <= 0 || current.height <= 0) return;
    untrack(() => {
      if (openedFor !== id || view === null) {
        view = openingView(box, current);
        openedFor = id;
      } else if (
        lastSize &&
        (lastSize.width !== current.width || lastSize.height !== current.height)
      ) {
        view = resizedView(view, lastSize, current);
      }
    });
    lastSize = current;
  });

  function showWhole(): void {
    if (size.width > 0 && size.height > 0) view = wholeView(whole, size);
  }

  /** Zoom by `factor` about a body, or about the middle. */
  function zoomBy(factor: number, about: BodyItem | null = null): void {
    if (!view || size.width <= 0) return;
    const at = about
      ? { x: (about.x - view.x) / view.w, y: (about.y - view.y) / view.h }
      : { x: 0.5, y: 0.5 };
    view = zoomedView(view, factor, whole, size, at);
  }

  // ---- moving about with the pointer -----------------------------------------
  type Point = { x: number; y: number };
  // eslint-disable-next-line svelte/prefer-svelte-reactivity -- read only by the pointer handlers, never drawn
  const pointers = new Map<number, Point>();
  let drag: { from: Point; view: View; moved: boolean } | null = null;
  let pinch: { distance: number; view: View; at: Point } | null = null;
  let movedSincePress = false;
  // The click a browser sends straight after a drag that ends on a body is
  // not a choice. Only that one click is let go.
  let swallowClick = false;

  function pinchFrom(current: View): typeof pinch {
    const [a, b] = [...pointers.values()];
    if (!a || !b || !stage) return null;
    const box = stage.getBoundingClientRect();
    return {
      distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
      view: current,
      at: {
        x: ((a.x + b.x) / 2 - box.left) / box.width,
        y: ((a.y + b.y) / 2 - box.top) / box.height,
      },
    };
  }

  function pressed(event: PointerEvent): void {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    movedSincePress = false;
    if (!view) return;
    if (pointers.size === 1)
      drag = {
        from: { x: event.clientX, y: event.clientY },
        view,
        moved: false,
      };
    else if (pointers.size === 2) {
      drag = null;
      pinch = pinchFrom(view);
    }
  }

  $effect(() => {
    const moved = (event: PointerEvent) => {
      if (!pointers.has(event.pointerId)) return;
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (pinch && pointers.size >= 2 && size.width > 0) {
        const [a, b] = [...pointers.values()] as [Point, Point];
        const distance = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
        view = zoomedView(
          pinch.view,
          pinch.distance / distance,
          whole,
          size,
          pinch.at,
        );
        movedSincePress = true;
        return;
      }
      if (!drag || size.width <= 0) return;
      const dx = event.clientX - drag.from.x;
      const dy = event.clientY - drag.from.y;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      drag.moved = true;
      movedSincePress = true;
      dragging = true;
      view = pannedView(drag.view, dx, dy, size);
    };
    const released = (event: PointerEvent) => {
      if (!pointers.delete(event.pointerId)) return;
      if (movedSincePress) {
        swallowClick = true;
        setTimeout(() => {
          swallowClick = false;
        }, 0);
      }
      drag = null;
      dragging = false;
      if (pointers.size < 2) pinch = null;
    };
    window.addEventListener('pointermove', moved);
    window.addEventListener('pointerup', released);
    window.addEventListener('pointercancel', released);
    return () => {
      window.removeEventListener('pointermove', moved);
      window.removeEventListener('pointerup', released);
      window.removeEventListener('pointercancel', released);
    };
  });

  // Dragging and the wheel are for the pointer only; the keyboard has the
  // bodies themselves and the buttons above. The wheel zooms about the
  // pointer, and the page must not scroll as well, so it is listened for
  // here rather than with onwheel.
  $effect(() => {
    const element = stage;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      if (!view || size.width <= 0) return;
      event.preventDefault();
      const box = element.getBoundingClientRect();
      view = zoomedView(view, event.deltaY > 0 ? 1.18 : 1 / 1.18, whole, size, {
        x: (event.clientX - box.left) / box.width,
        y: (event.clientY - box.top) / box.height,
      });
    };
    element.addEventListener('pointerdown', pressed);
    element.addEventListener('wheel', wheel, { passive: false });
    return () => {
      element.removeEventListener('pointerdown', pressed);
      element.removeEventListener('wheel', wheel);
    };
  });

  // ---- choosing a body, by pointer or keyboard -------------------------------
  let focusKey = $state<string | null>(null);
  const tabStop = $derived.by(() => {
    if (focusKey && bodies.some((item) => item.id === focusKey))
      return focusKey;
    if (selectedKey && bodies.some((item) => item.id === selectedKey))
      return selectedKey;
    return bodies[0]?.id ?? null;
  });

  function choose(item: BodyItem): void {
    if (swallowClick) return;
    onSelect?.(item.id);
  }

  /** Keeps a body that the keyboard has reached on screen. */
  function reveal(item: BodyItem): void {
    focusKey = item.id;
    if (view && !shows(view, item.x, item.y, Math.min(view.w, view.h) * 0.08))
      view = centredOn(view, item.x, item.y);
  }

  async function focusBody(item: BodyItem): Promise<void> {
    focusKey = item.id;
    await tick();
    for (const element of stage?.querySelectorAll<SVGGElement>(
      '[data-body-key]',
    ) ?? [])
      if (element.dataset.bodyKey === item.id) element.focus();
  }

  function keyOnBody(event: KeyboardEvent, index: number): void {
    const item = bodies[index];
    if (!item) return;
    let next: number;
    switch (event.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = Math.min(bodies.length - 1, index + 1);
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        next = Math.max(0, index - 1);
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = bodies.length - 1;
        break;
      case 'Enter':
      case ' ':
        event.preventDefault();
        onSelect?.(item.id);
        return;
      case '+':
      case '=':
        event.preventDefault();
        zoomBy(1 / 1.4, item);
        return;
      case '-':
      case '_':
        event.preventDefault();
        zoomBy(1.4, item);
        return;
      case '0':
        event.preventDefault();
        showWhole();
        return;
      default:
        return;
    }
    event.preventDefault();
    const target = bodies[next];
    if (target) void focusBody(target);
  }

  // ---- pictures ----------------------------------------------------------------
  // Made only once the map is on screen: a map in a hidden tab costs nothing.
  let onScreen = $state(false);
  $effect(() => {
    const element = stage;
    if (!element) return;
    if (typeof IntersectionObserver === 'undefined') {
      onScreen = true;
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      onScreen = entries.some((entry) => entry.isIntersecting);
    });
    observer.observe(element);
    return () => observer.disconnect();
  });

  let maker = $state<PictureMaker | null>(null);
  $effect(() => {
    if (!onScreen || maker !== null) return;
    let current = true;
    void pictureMaker().then((found) => {
      if (current) maker = found;
    });
    return () => {
      current = false;
    };
  });

  const jobs = $derived(
    bodies.map((item): PictureJob => ({
      id: item.id,
      look: lookFor(item),
      r: item.r,
      surfaceK: item.node.surfaceK,
    })),
  );
  /** Each body's picture name, in the way pictures are made here. */
  const names = $derived(
    maker && maker !== 'none'
      ? new Map(
          jobs.map((job) => [job.id, pictureName(maker as PictureMaker, job)]),
        )
      : new Map<string, string>(),
  );
  const pictures = new SvelteMap<string, Picture>();

  function pictureOf(item: BodyItem): Picture | null {
    const name = names.get(item.id);
    return (name && pictures.get(name)) || null;
  }

  $effect(() => {
    const current = maker;
    const wanted = new Map(
      current && current !== 'none'
        ? jobs.map((job) => [pictureName(current, job), job] as const)
        : [],
    );
    if (!onScreen || !current || current === 'none') return;
    const missing = untrack(() => {
      // Pictures no longer drawn are let go.
      for (const [name, picture] of [...pictures]) {
        if (wanted.has(name)) continue;
        URL.revokeObjectURL(picture.href);
        pictures.delete(name);
      }
      const onView = view;
      const needed = [...wanted]
        .filter(([name]) => !pictures.has(name))
        .map(([, job]) => job);
      // What is on screen first.
      const near = (job: PictureJob) => {
        const item = bodies.find((one) => one.id === job.id);
        return item && onView && touches(onView, item.x, item.y, item.r)
          ? 0
          : 1;
      };
      return needed.sort((one, other) => near(one) - near(other));
    });
    const stop = new AbortController();
    void makePictures(
      current,
      missing,
      (name, picture) => {
        if (wanted.has(name) && !stop.signal.aborted)
          pictures.set(name, picture);
        else URL.revokeObjectURL(picture.href);
      },
      stop.signal,
    );
    return () => stop.abort();
  });

  onDestroy(() => {
    for (const picture of pictures.values()) URL.revokeObjectURL(picture.href);
    pictures.clear();
  });

  // ---- spinning ------------------------------------------------------------------
  const calm =
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia('(prefers-reduced-motion: reduce)')
      : null;
  let spinWanted = $state(!(calm?.matches ?? false));
  let spinNote = $state<string | null>(null);
  const spinning = $derived(
    spinWanted && onScreen && maker === 'cosmos' && pictures.size > 0,
  );
  // eslint-disable-next-line svelte/prefer-svelte-reactivity -- read only by the spinning loop, never drawn
  const pens = new Map<string, CanvasRenderingContext2D>();
  const turned = new SvelteSet<string>();

  $effect(() => {
    if (!calm) return;
    const changed = () => {
      if (calm.matches) spinWanted = false;
    };
    calm.addEventListener('change', changed);
    return () => calm.removeEventListener('change', changed);
  });

  function penFor(id: string) {
    return (canvas: HTMLCanvasElement) => {
      const pen = canvas.getContext('2d');
      if (pen) pens.set(id, pen);
      return () => {
        pens.delete(id);
        turned.delete(id);
      };
    };
  }

  $effect(() => {
    if (!spinning) return;
    let lastStir = performance.now();
    const stirred = () => {
      lastStir = performance.now();
    };
    const kinds = ['pointermove', 'pointerdown', 'wheel', 'keydown'] as const;
    for (const kind of kinds)
      window.addEventListener(kind, stirred, { passive: true });
    let stop: (() => void) | null = null;
    let ended = false;
    void cosmos().then((generator) => {
      if (ended || !generator) return;
      stop = startSpinning(generator, {
        targets: () => {
          const onView = view;
          const found: SpinTarget[] = [];
          for (const item of bodies) {
            const pen = pens.get(item.id);
            if (!pen || !pictureOf(item)) continue;
            if (onView && !touches(onView, item.x, item.y, item.r * 2.5))
              continue;
            found.push({
              id: item.id,
              ask: askOf({
                id: item.id,
                look: lookFor(item),
                r: item.r,
                surfaceK: item.node.surfaceK,
              }),
              seed: seedOf(item.id),
              pen,
            });
          }
          return found;
        },
        lastStir: () => lastStir,
        drew: (id) => turned.add(id),
        tooSlow: (msEach) => {
          spinWanted = false;
          spinNote = `Spinning switched itself off: this computer took ${msEach} ms to draw each step, and the limit is 8 ms.`;
        },
        failed: () => {
          spinWanted = false;
          spinNote =
            'Spinning stopped: the graphics card stopped answering. The still pictures stay.';
        },
      });
    });
    return () => {
      ended = true;
      stop?.();
      for (const kind of kinds) window.removeEventListener(kind, stirred);
      turned.clear();
    };
  });
</script>

<div class="system-map">
  <div class="system-map-tools">
    <button type="button" onclick={showWhole} disabled={!view}>
      Show the whole system
    </button>
    <button
      type="button"
      aria-label="Zoom in"
      onclick={() => zoomBy(1 / 1.4)}
      disabled={!view}>+</button
    >
    <button
      type="button"
      aria-label="Zoom out"
      onclick={() => zoomBy(1.4)}
      disabled={!view}>−</button
    >
    {#if maker === 'cosmos'}
      <label
        class="system-map-spin"
        title="Turns the bodies while you use the page. They rest after 20 seconds without the mouse or a key."
      >
        <input
          type="checkbox"
          bind:checked={spinWanted}
          onchange={() => (spinNote = null)}
        />
        Spin
      </label>
    {/if}
    <p class="system-map-hint">
      Drag to move, wheel or pinch to zoom. With the keyboard: Tab into the map,
      then the arrow keys go from body to body.
    </p>
  </div>
  {#if spinNote}
    <p class="system-map-note" role="status">{spinNote}</p>
  {/if}
  <div
    class="system-map-stage"
    class:dragging
    bind:this={stage}
    data-system-id64={tree.id64}
  >
    <svg
      xmlns="http://www.w3.org/2000/svg"
      class="sm"
      {viewBox}
      role="group"
      aria-label={`System map of ${tree.name}`}
    >
      <defs>
        <!-- One light for every ball, from the upper left: a bright spot, then
             the colour itself, then shadow deepening toward the far edge. -->
        <radialGradient id={ref('shade')} cx="0.3" cy="0.27" r="0.86">
          <stop offset="0" stop-color="#fff" stop-opacity="0.62" />
          <stop offset="0.24" stop-color="#fff" stop-opacity="0.06" />
          <stop offset="0.46" stop-color="#000" stop-opacity="0.04" />
          <stop offset="0.8" stop-color="#000" stop-opacity="0.62" />
          <stop offset="1" stop-color="#000" stop-opacity="0.92" />
        </radialGradient>
        <filter id={ref('glow')} x="-1" y="-1" width="3" height="3">
          <feGaussianBlur stdDeviation="5" />
        </filter>
        {#each bodies as item, index (item.id)}
          {@const look = lookFor(item)}
          {#if look.star && !look.dark}
            {@const stops = starStops(look)}
            <radialGradient
              id={ref(`star-${index}`)}
              cx="0.42"
              cy="0.4"
              r="0.62"
            >
              <stop offset="0" stop-color={stops[0]} />
              <stop offset="0.55" stop-color={stops[1]} />
              <stop offset="1" stop-color={stops[2]} />
            </radialGradient>
          {/if}
        {/each}
      </defs>

      <g class="sm-lines" aria-hidden="true">
        {#each layout.lines as line, index (index)}
          <line
            class={line.kind === 'pair' ? 'sm-pair' : 'sm-line'}
            x1={n(line.x1)}
            y1={n(line.y1)}
            x2={n(line.x2)}
            y2={n(line.y2)}
          />
        {/each}
      </g>

      {#each belts as item, index (index)}
        <g
          class="sm-belt"
          fill={beltColour(item)}
          transform={`translate(${n(item.x)} ${n(item.y)})`}
          aria-hidden="true"
        >
          <title>{spokenOfBelt(item)}</title>
          {#each rocksOf(item) as rock, rockIndex (rockIndex)}
            <circle
              cx={rock.cx}
              cy={rock.cy}
              r={rock.r}
              opacity={rock.opacity}
            />
          {/each}
        </g>
      {/each}

      {#each stations as item (item.station.id)}
        {@const glyph = item.station.mark
          ? STATION_GLYPHS[item.station.mark]
          : null}
        <g
          class="sm-station"
          class:sm-guess={item.station.guess}
          transform={`translate(${n(item.x)} ${n(item.y)})`}
          data-station-key={item.station.id}
          aria-hidden="true"
        >
          <title>{spokenOfStation(item.station)}</title>
          <rect
            x={-item.size / 2}
            y={-item.size / 2}
            width={item.size}
            height={item.size}
            rx="2"
          />
          {#if glyph}
            <path
              d={glyph.d}
              class:sm-glyph-filled={glyph.filled}
              class:sm-glyph-heavy={glyph.heavy}
            />
          {/if}
        </g>
      {/each}

      {#each bodies as item, index (item.id)}
        {@const look = lookFor(item)}
        {@const picture = pictureOf(item)}
        {@const spoken = spokenOf(item.node)}
        {@const selected = item.id === selectedKey}
        {@const shadow = look.star ? null : ringShadow(item)}
        <g
          class="sm-body"
          class:sm-selected={selected}
          class:sm-guessed={item.node.guessedParent !== false}
          data-body-key={item.id}
          data-look={picture ? 'picture' : 'plain'}
          transform={`translate(${n(item.x)} ${n(item.y)})`}
          role="button"
          tabindex={item.id === tabStop ? 0 : -1}
          aria-label={spoken}
          aria-pressed={selected}
          onclick={() => choose(item)}
          onkeydown={(event) => keyOnBody(event, index)}
          onfocus={() => reveal(item)}
        >
          <title>{spoken}</title>
          {#if item.node.rings.length > 0}
            <g class="sm-ring" transform={`rotate(${RING_TILT})`} opacity="0.5">
              {#each ringShapes(item, false) as shape, shapeIndex (shapeIndex)}
                <path d={shape.d} fill={shape.fill} opacity={shape.opacity} />
              {/each}
            </g>
          {/if}
          {#if picture}
            {@const half = (item.r * picture.width) / 2}
            {#if look.star && !picture.whole}
              <circle
                r={n(item.r * 1.3)}
                fill={rgb1(look.halo ?? look.colour)}
                opacity={look.dark ? 0.35 : 0.55}
                filter={`url(#${ref('glow')})`}
              />
            {/if}
            <image
              class="sm-surface"
              href={picture.href}
              x={n(-half)}
              y={n(-half)}
              width={n(2 * half)}
              height={n(2 * half)}
              style:visibility={turned.has(item.id) ? 'hidden' : null}
            />
            {#if spinning}
              <foreignObject
                class="sm-spin"
                x={n(-half)}
                y={n(-half)}
                width={n(2 * half)}
                height={n(2 * half)}
              >
                <canvas
                  width={SPIN_PIXELS}
                  height={SPIN_PIXELS}
                  {@attach penFor(item.id)}
                ></canvas>
              </foreignObject>
            {/if}
            {#if look.dark && !picture.whole}
              <circle
                r={n(item.r)}
                fill="none"
                stroke={rgb1(look.halo ?? look.colour)}
                stroke-width={n(Math.max(1.5, item.r * 0.08))}
              />
            {/if}
          {:else if look.star && look.dark}
            <circle
              r={n(item.r * 1.25)}
              fill={rgb1(look.halo ?? look.colour)}
              opacity="0.35"
              filter={`url(#${ref('glow')})`}
            />
            <circle
              r={n(item.r)}
              fill="#000"
              stroke={rgb1(look.halo ?? look.colour)}
              stroke-width={n(Math.max(1.5, item.r * 0.08))}
            />
          {:else if look.star}
            <circle
              r={n(item.r * 1.3)}
              fill={rgb1(look.halo ?? look.colour)}
              opacity="0.55"
              filter={`url(#${ref('glow')})`}
            />
            <circle r={n(item.r)} fill={`url(#${ref(`star-${index}`)})`} />
          {:else}
            <circle r={n(item.r)} fill={rgb1(look.colour)} />
            {#if look.banded}
              <clipPath id={ref(`bands-${index}`)}
                ><circle r={n(item.r)} /></clipPath
              >
              <g clip-path={`url(#${ref(`bands-${index}`)})`}>
                {#each stripesOf(item) as stripe, stripeIndex (stripeIndex)}
                  <rect
                    x={n(-item.r)}
                    y={stripe.y}
                    width={n(2 * item.r)}
                    height={stripe.height}
                    fill={stripe.light ? '#fff' : '#000'}
                    opacity={stripe.light ? 0.1 : 0.16}
                  />
                {/each}
              </g>
            {/if}
          {/if}
          {#if shadow}
            <clipPath id={ref(`shadow-${index}`)}
              ><circle r={n(item.r)} /></clipPath
            >
            <g clip-path={`url(#${ref(`shadow-${index}`)})`}>
              <g
                transform={`rotate(${RING_TILT}) translate(0 ${n(-0.14 * item.r)})`}
                fill="#000"
                opacity="0.34"
              >
                <path d={shadow} />
              </g>
            </g>
          {/if}
          {#if !picture && !look.star}
            <circle r={n(item.r)} fill={`url(#${ref('shade')})`} />
          {/if}
          {#if item.node.rings.length > 0}
            <g
              class="sm-ring"
              transform={`rotate(${RING_TILT})`}
              opacity="0.92"
            >
              {#each ringShapes(item, true) as shape, shapeIndex (shapeIndex)}
                <path d={shape.d} fill={shape.fill} opacity={shape.opacity} />
              {/each}
            </g>
          {/if}
          {#if item.mark}
            <text class="sm-ground" x={n(item.mark.x)} y={n(item.mark.y)}
              >{#if item.mark.built !== null}<tspan
                  >&#9650;{item.mark.built}</tspan
                >{/if}{#if item.mark.slots !== null}<tspan
                  class="sm-slots"
                  dx={item.mark.built === null ? undefined : 3}
                  >&#9633;{item.mark.slots}</tspan
                >{/if}</text
            >
          {/if}
          <circle
            class="sm-hit"
            r={n(Math.max(item.r, 7) + 3)}
            fill="transparent"
          />
        </g>
      {/each}

      <g class="sm-labels" aria-hidden="true">
        {#each layout.labels as label, index (index)}
          <text
            class={label.small ? 'sm-caption' : 'sm-label'}
            x={n(label.x)}
            y={n(label.y)}
            text-anchor={label.anchor}>{label.text}</text
          >
        {/each}
      </g>
    </svg>
  </div>
</div>

<style>
  .system-map {
    --sm-line: var(--line, #26405f);
    --sm-pair: var(--line-strong, #3a5a84);
    --sm-built: var(--accent-hi, #ff9a3c);
    --sm-slots: #7fe0c0;
    --sm-picked: var(--neon, #3da5ff);
    --sm-hover: var(--neon-hi, #5ec8ff);
  }

  .system-map-tools {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem 0.75rem;
    padding: 0.5rem 1rem;
  }

  .system-map-tools button {
    min-width: 2rem;
    padding: 0.3rem 0.7rem;
    border: 1px solid var(--line-strong);
    border-radius: var(--radius-sm, 8px);
    background: var(--panel-raised);
    color: var(--text);
    font: inherit;
    font-size: 0.8rem;
    cursor: pointer;
  }

  .system-map-tools button:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .system-map-tools button:focus-visible,
  .system-map-spin input:focus-visible {
    outline: 2px solid var(--neon);
    outline-offset: 2px;
  }

  .system-map-spin {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    color: var(--text-muted);
    font-size: 0.8rem;
  }

  .system-map-hint,
  .system-map-note {
    margin: 0;
    color: var(--text-dim);
    font-size: 0.74rem;
  }

  .system-map-note {
    padding: 0 1rem 0.5rem;
    color: var(--warn);
  }

  .system-map-stage {
    height: clamp(22rem, 58vh, 40rem);
    border-block: 1px solid var(--line);
    background-color: var(--bg-deep, #0b1220);
    background-image:
      linear-gradient(#0d1524 1px, transparent 1px),
      linear-gradient(90deg, #0d1524 1px, transparent 1px);
    background-size: 44px 44px;
    cursor: grab;
    overflow: hidden;
    touch-action: none;
    user-select: none;
  }

  .system-map-stage.dragging {
    cursor: grabbing;
  }

  .sm {
    display: block;
    width: 100%;
    height: 100%;
    font-family: var(--font-sans, 'Inter', sans-serif);
  }

  .sm :global(.sm-line) {
    stroke: var(--sm-line);
    stroke-width: 1;
  }

  .sm :global(.sm-pair) {
    fill: none;
    stroke: var(--sm-pair);
    stroke-width: 1;
  }

  .sm :global(.sm-label) {
    fill: var(--text-muted, #9fb0c7);
    font-size: 9.5px;
    pointer-events: none;
  }

  .sm :global(.sm-caption) {
    fill: var(--text-dim, #6f84a0);
    font-size: 7.5px;
    pointer-events: none;
  }

  .sm-ground {
    fill: var(--sm-built);
    font-size: 7.5px;
    pointer-events: none;
  }

  .sm-slots {
    fill: var(--sm-slots);
  }

  .sm-body {
    cursor: pointer;
    outline: none;
  }

  .sm-body:hover .sm-hit,
  .sm-body:focus-visible .sm-hit {
    stroke: var(--sm-hover);
    stroke-width: 1.2;
  }

  .sm-body.sm-selected .sm-hit {
    stroke: var(--sm-picked);
    stroke-width: 2;
  }

  .sm-body.sm-selected:focus-visible .sm-hit {
    stroke-dasharray: 3 2;
  }

  .sm-spin {
    pointer-events: none;
  }

  .sm-spin canvas {
    display: block;
    width: 100%;
    height: 100%;
  }

  .sm-station {
    color: var(--sm-built);
  }

  .sm-station rect {
    fill: var(--panel-sunken, #0d1524);
    stroke: var(--sm-built);
    stroke-width: 1;
  }

  .sm-station path {
    fill: none;
    stroke: var(--sm-built);
    stroke-width: 1.2;
  }

  .sm-station path.sm-glyph-filled {
    fill: currentColor;
    stroke: none;
  }

  .sm-station path.sm-glyph-heavy {
    stroke-width: 2.2;
  }

  .sm-station.sm-guess rect {
    stroke-dasharray: 2 1.6;
  }
</style>
