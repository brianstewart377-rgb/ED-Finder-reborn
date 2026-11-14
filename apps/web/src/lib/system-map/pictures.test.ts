import { describe, expect, it } from 'vitest';
import { lookOf } from './draw';
import { layoutOf, type BodyItem } from './layout';
import {
  PAINTED_PIXELS_MAX,
  PICTURE_PIXELS_MAX,
  PICTURE_PIXELS_MIN,
  jobAt,
  pictureName,
  picturePixels,
  pictureWidth,
  type PictureBody,
} from './pictures';
import { treeOf } from './recorded-systems';
import { openingView, scaleOf, wholeOf } from './view';

const SCREEN = { width: 1000, height: 560 };

function bodyOf(item: BodyItem): PictureBody {
  return {
    id: item.id,
    look: lookOf(item.node),
    r: item.r,
    surfaceK: item.node.surfaceK,
  };
}

describe('how big each picture is made', () => {
  it('makes a picture at least as big as it is shown, and never more than twice as big', () => {
    for (const shown of [33, 40, 64, 65, 100, 200, 300, 500]) {
      const made = picturePixels(shown);
      expect(made, String(shown)).toBeGreaterThanOrEqual(shown);
      expect(made, String(shown)).toBeLessThan(shown * 2);
    }
    expect(picturePixels(3)).toBe(PICTURE_PIXELS_MIN);
    expect(picturePixels(5000)).toBe(PICTURE_PIXELS_MAX);
  });

  it('makes Sol’s small planets small when the map opens, and sharper as it zooms in', () => {
    const tree = treeOf('sol');
    const layout = layoutOf(tree);
    const items = layout.items.filter(
      (item): item is BodyItem => item.kind === 'body',
    );
    const earth = bodyOf(items.find((item) => item.node.name === 'Earth')!);
    const perUnit = scaleOf(openingView(wholeOf(layout.box), SCREEN), SCREEN);
    const opening = jobAt('cosmos', earth, perUnit);
    // Earth is drawn about 25 pixels wide when Sol's map opens: a 32-pixel
    // picture, not one of 128 shrunk to a quarter.
    expect(earth.r * pictureWidth('cosmos', earth) * perUnit).toBeLessThan(32);
    expect(opening.pixels).toBe(32);
    const zoomed = jobAt('cosmos', earth, perUnit * 6);
    expect(zoomed.pixels).toBeGreaterThan(opening.pixels);
    expect(pictureName('cosmos', zoomed)).not.toBe(
      pictureName('cosmos', opening),
    );
    // The same view on a screen with twice the pixels wants twice the picture.
    expect(jobAt('cosmos', earth, perUnit * 2).pixels).toBe(64);
  });

  it('keeps painted pictures smaller, since each pixel is worked out by hand', () => {
    const sol = layoutOf(treeOf('sol')).items.find(
      (item): item is BodyItem =>
        item.kind === 'body' && item.node.name === 'Sol',
    )!;
    expect(jobAt('cosmos', bodyOf(sol), 10).pixels).toBe(PICTURE_PIXELS_MAX);
    expect(jobAt('painted', bodyOf(sol), 10).pixels).toBe(PAINTED_PIXELS_MAX);
  });
});
