<!-- What the System Map's marks mean, drawn with the map's own shapes and colours. -->
<script lang="ts">
  import { asset } from '$app/paths';
  import { RING_COLOURS, STATION_GLYPHS, rgb255 } from './draw';
  import { RING_KINDS } from './tree';

  const stationLines = [
    { mark: 'starport', words: 'A starport in orbit' },
    { mark: 'outpost', words: 'An outpost in orbit' },
    { mark: 'building', words: 'Being built in orbit' },
    { mark: 'megaship', words: 'A megaship' },
  ] as const;

  const ringLines = Object.entries(RING_KINDS).map(([code, words]) => ({
    colour: rgb255(RING_COLOURS[code] ?? [120, 120, 126]),
    words: `${words[0]?.toUpperCase() ?? ''}${words.slice(1)} ring or belt`,
  }));
</script>

<ul class="system-map-key" aria-label="What the marks mean">
  {#each stationLines as line (line.mark)}
    {@const glyph = STATION_GLYPHS[line.mark]}
    <li>
      <svg viewBox="-9 -7 18 14" aria-hidden="true">
        <rect x="-5.5" y="-5.5" width="11" height="11" rx="2" />
        <path
          d={glyph.d}
          class:filled={glyph.filled}
          class:heavy={glyph.heavy}
        />
      </svg>
      <span>{line.words}</span>
    </li>
  {/each}
  <li>
    <svg viewBox="-9 -7 18 14" aria-hidden="true">
      <rect class="guess" x="-5.5" y="-5.5" width="11" height="11" rx="2" />
    </svg>
    <span
      >Dashed: placed by its distance. Our data does not say which body it is
      at.</span
    >
  </li>
  <li>
    <svg viewBox="-9 -7 18 14" aria-hidden="true">
      <text class="built" x="-8" y="3.5">&#9650;3</text>
    </svg>
    <span>Three sites on the ground, in our data.</span>
  </li>
  <li>
    <svg viewBox="-9 -7 18 14" aria-hidden="true">
      <text class="slots" x="-8" y="3.5">&#9633;4</text>
    </svg>
    <span>
      Four ground slots, predicted by this site. The game is the only sure
      source. No square: the site makes no prediction for that body.
    </span>
  </li>
  <li>
    <svg viewBox="-9 -7 18 14" aria-hidden="true">
      <path class="pair" d="M-7 4 V-3 H7 V4" />
    </svg>
    <span>Two bodies that circle each other.</span>
  </li>
  {#each ringLines as line (line.words)}
    <li>
      <span class="swatch" style:background={line.colour} aria-hidden="true"
      ></span>
      <span>{line.words}</span>
    </li>
  {/each}
</ul>
<p>
  Sizes are for reading, not to scale. Colours are by class, as on the app's
  other maps; the pictures are illustrations, not the real surface. A rocky or
  metal body at 1,300 K or more is drawn molten.
</p>
<p>
  Where the browser can, the pictures are drawn by
  <a href="https://github.com/Eluvade/cosmos" rel="noreferrer">Cosmos</a>
  by Eluvade (<a href={asset('/licences/cosmos-LICENSE.txt')}>MIT licence</a>),
  whose planets come from Deep-Fold's
  <a href="https://github.com/Deep-Fold/PixelPlanets" rel="noreferrer"
    >Pixel Planets</a
  >
  (<a href={asset('/licences/pixel-planets-LICENSE.txt')}>MIT licence</a>).
</p>

<style>
  .system-map-key {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
    gap: 0.35rem 1rem;
    margin: 0 0 0.6rem;
    padding: 0;
    list-style: none;
  }

  .system-map-key li {
    display: grid;
    grid-template-columns: 1.4rem 1fr;
    align-items: center;
    gap: 0.5rem;
  }

  svg {
    width: 1.15rem;
    height: 0.9rem;
    overflow: visible;
  }

  rect {
    fill: var(--panel-sunken, #0d1524);
    stroke: var(--accent-hi, #ff9a3c);
    stroke-width: 1;
  }

  rect.guess {
    stroke-dasharray: 2 1.6;
  }

  path {
    fill: none;
    stroke: var(--accent-hi, #ff9a3c);
    stroke-width: 1.2;
  }

  path.filled {
    fill: var(--accent-hi, #ff9a3c);
    stroke: none;
  }

  path.heavy {
    stroke-width: 2.2;
  }

  path.pair {
    stroke: var(--line-strong, #3a5a84);
    stroke-width: 1;
  }

  text {
    font-size: 10px;
  }

  text.built {
    fill: var(--accent-hi, #ff9a3c);
  }

  text.slots {
    fill: #7fe0c0;
  }

  .swatch {
    justify-self: center;
    width: 1rem;
    height: 0.4rem;
    border-radius: 0.2rem;
  }

  a {
    color: var(--neon-hi, #5ec8ff);
  }

  p {
    margin: 0;
  }

  p + p {
    margin-top: 0.4rem;
  }
</style>
