/**
 * The bodies' pictures on the System Map, made in the browser.
 *
 * Where the browser has WebGL, the Cosmos library draws each body (planets
 * from its ports of Deep-Fold's Pixel Planets). Where it has none, each body
 * is painted by arithmetic (surface.ts). Where it cannot do either (the test
 * runner), there are no pictures and the map draws plain shaded balls.
 *
 * The map is drawn at once with plain balls; the pictures follow one body at
 * a time, giving the browser a turn between bodies, so the page never stops
 * to wait for them. Cosmos's drawing programs for the kinds a system needs are
 * prepared first, in the same small steps: on the trial page, preparing them
 * all inside the first drawing held the page still for over a second.
 *
 * Cosmos is fetched only when a map is first shown, so pages without one do
 * not carry it. One Cosmos drawing surface serves the whole app.
 */
import type { CelestialGenerator, CelestialType } from '@eluvade/cosmos';
import { cosmosAsk, cosmosPixels, type CosmosAsk } from './cosmos-look';
import { seedOf, type Look } from './draw';
import { pixelsFor, surface } from './surface';

export type Picture = Readonly<{
  href: string;
  /** How wide to draw it, in body radii. */
  width: number;
  /** A whole picture brings its own glow or edge. */
  whole: boolean;
}>;

/** One body's picture to make. */
export type PictureJob = Readonly<{
  /** The body's key. */
  id: string;
  look: Look;
  /** How big the body is drawn, in the map's units. */
  r: number;
  surfaceK: number | null;
}>;

export type PictureMaker = 'cosmos' | 'painted' | 'none';

let generator: CelestialGenerator | null = null;
let loading: Promise<CelestialGenerator | null> | null = null;

/** Whether the browser could have WebGL at all. The test runner has none. */
function webglLikely(): boolean {
  return (
    typeof document !== 'undefined' &&
    typeof WebGLRenderingContext !== 'undefined'
  );
}

/** Whether the browser can paint pixels into a canvas. */
function canPaint(): boolean {
  return (
    typeof document !== 'undefined' &&
    typeof CanvasRenderingContext2D !== 'undefined'
  );
}

/**
 * The app's one Cosmos drawing surface, made on first use; null where this
 * browser cannot draw with WebGL. If the graphics card is taken away, the
 * surface is dropped and made afresh next time.
 */
export function cosmos(): Promise<CelestialGenerator | null> {
  if (generator) return Promise.resolve(generator);
  if (!webglLikely()) return Promise.resolve(null);
  loading ??= import('@eluvade/cosmos')
    .then(({ CelestialGenerator }) => {
      try {
        const made = new CelestialGenerator();
        made.getCanvas().addEventListener('webglcontextlost', () => {
          if (generator === made) generator = null;
          loading = null;
        });
        generator = made;
        return made;
      } catch {
        return null;
      }
    })
    .catch(() => null);
  return loading;
}

/** What Cosmos is asked for, for one body. */
export function askOf(job: PictureJob): CosmosAsk {
  return cosmosAsk({
    family: job.look.family,
    star: job.look.star,
    dark: job.look.dark,
    colour: job.look.colour,
    halo: job.look.halo,
    surfaceK: job.surfaceK,
  });
}

/**
 * A name for one body's picture: the same name for the same picture, so a
 * picture is kept while the system is refreshed and made again only when
 * what it shows changes (a temperature arriving can make rock molten).
 */
export function pictureName(maker: PictureMaker, job: PictureJob): string {
  if (maker === 'cosmos') {
    const ask = askOf(job);
    return `cosmos/${job.id}/${ask.kind}/${cosmosPixels(job.r, ask.width)}/${job.look.colour.join(',')}`;
  }
  return `painted/${job.id}/${job.look.family}/${pixelsFor(job.r)}/${job.look.colour.join(',')}`;
}

/** Which way pictures will be made in this browser: by Cosmos, by painting, or not at all. */
export async function pictureMaker(): Promise<PictureMaker> {
  if (await cosmos()) return 'cosmos';
  return canPaint() ? 'painted' : 'none';
}

/** A turn for the browser: anything waiting to be drawn or handled goes first. */
function breathe(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function blobOf(
  canvas: HTMLCanvasElement | OffscreenCanvas,
): Promise<Blob | null> {
  if ('convertToBlob' in canvas)
    return canvas.convertToBlob({ type: 'image/png' }).catch(() => null);
  return new Promise((resolve) => {
    try {
      canvas.toBlob((blob) => resolve(blob), 'image/png');
    } catch {
      resolve(null);
    }
  });
}

async function cosmosPicture(
  maker: CelestialGenerator,
  job: PictureJob,
): Promise<Picture | null> {
  const ask = askOf(job);
  const canvas = maker.render(
    {
      type: ask.kind as CelestialType,
      seed: seedOf(job.id),
      resolution: cosmosPixels(job.r, ask.width),
      ...(ask.colours
        ? {
            colors: ask.colours as Record<
              string,
              [number, number, number, number]
            >,
          }
        : {}),
    },
    0,
  );
  const blob = await blobOf(canvas);
  return blob
    ? { href: URL.createObjectURL(blob), width: ask.width, whole: ask.whole }
    : null;
}

const easel = { canvas: null as HTMLCanvasElement | null };

async function paintedPicture(job: PictureJob): Promise<Picture | null> {
  const size = pixelsFor(job.r);
  const made = surface({
    seed: seedOf(job.id),
    size,
    family: job.look.family,
    colour: job.look.colour,
    star: job.look.star,
    halo: job.look.halo ?? job.look.colour,
    dark: job.look.dark,
  });
  easel.canvas ??= document.createElement('canvas');
  const canvas = easel.canvas;
  canvas.width = size;
  canvas.height = size;
  const pen = canvas.getContext('2d');
  if (!pen) return null;
  pen.putImageData(
    new ImageData(made.data as Uint8ClampedArray<ArrayBuffer>, size, size),
    0,
    0,
  );
  const blob = await blobOf(canvas);
  return blob
    ? { href: URL.createObjectURL(blob), width: 2, whole: false }
    : null;
}

/**
 * Makes each body's picture in turn and gives each as it is ready, under the
 * name `pictureName` gives it. Stops when `signal` is aborted; a picture made
 * after that is let go at once. Bodies Cosmos fails on are painted instead.
 */
export async function makePictures(
  maker: PictureMaker,
  jobs: readonly PictureJob[],
  give: (name: string, picture: Picture) => void,
  signal: AbortSignal,
): Promise<void> {
  if (maker === 'none' || jobs.length === 0) return;
  const drawer = maker === 'cosmos' ? await cosmos() : null;
  if (drawer) {
    const kinds = [
      ...new Set(jobs.map((job) => askOf(job).kind)),
    ] as CelestialType[];
    try {
      await drawer.precompile(kinds);
    } catch {
      // Each picture is still tried below, and painted if Cosmos fails on it.
    }
  }
  for (const job of jobs) {
    if (signal.aborted) return;
    let picture: Picture | null = null;
    try {
      if (drawer) picture = await cosmosPicture(drawer, job);
    } catch {
      picture = null;
    }
    if (!picture && canPaint()) {
      try {
        picture = await paintedPicture(job);
      } catch {
        picture = null;
      }
    }
    if (picture) {
      if (signal.aborted) {
        URL.revokeObjectURL(picture.href);
        return;
      }
      give(pictureName(maker, job), picture);
    }
    await breathe();
  }
}
