// The map recorder's own logic, kept apart from the browser so that it can be
// tested anywhere (logic.test.mjs): reading the command line, checking a tour
// file, turning frame times into numbers, and writing the report page.
// record.mjs does the part that needs a real browser.

export const DEFAULTS = Object.freeze({
  url: 'https://ed-finder.app',
  browser: 'chrome',
  tour: 'tours/explore.json',
  name: 'map',
  out: 'recordings',
  // The size the plan asks for: it fits a 1920x1080 screen with the browser's
  // own bars around it.
  width: 1760,
  height: 988,
  scale: 1,
});

export const BROWSERS = Object.freeze(['chrome', 'msedge', 'firefox']);

const USAGE = `Record a tour of the map in a real browser.

  node record.mjs [options]

  --url <address>     the site to open (default ${DEFAULTS.url})
  --tour <file>       the tour to fly (default ${DEFAULTS.tour})
  --name <word>       a name for this recording, used in its folder's name
                      (default "${DEFAULTS.name}")
  --browser <name>    chrome, msedge or firefox (default ${DEFAULTS.browser})
  --scale <number>    the display scaling to imitate: 1, 1.25, 1.5 or 2
                      (default 1; Windows at 150% is 1.5)
  --pause             open the page, then wait for you to press Enter before
                      the tour starts (to sign in first, say)
  --out <folder>      where recordings go (default "${DEFAULTS.out}")
  --headless          no window. For trying the recorder itself only: the
                      graphics card is not used, so the result says nothing
                      about how the map looks or runs
  --exe <file>        the browser program to use, if it is not found by itself
  --help              show this
`;

export class UsageError extends Error {}

export function usage() {
  return USAGE;
}

/** Read the command line. Throws UsageError with a plain message. */
export function parseArgs(argv) {
  const options = { ...DEFAULTS, pause: false, headless: false, exe: null, help: false };
  const takesValue = new Set(['url', 'tour', 'name', 'browser', 'scale', 'out', 'exe']);
  for (let i = 0; i < argv.length; i += 1) {
    const word = argv[i];
    if (!word.startsWith('--')) throw new UsageError(`"${word}" is not an option. Options start with --.`);
    const key = word.slice(2);
    if (key === 'pause' || key === 'headless' || key === 'help') {
      options[key] = true;
    } else if (takesValue.has(key)) {
      const value = argv[i + 1];
      if (value === undefined || value.startsWith('--')) throw new UsageError(`--${key} needs a value after it.`);
      options[key] = value;
      i += 1;
    } else {
      throw new UsageError(`--${key} is not an option this understands.`);
    }
  }
  if (!BROWSERS.includes(options.browser)) {
    throw new UsageError(`--browser must be one of: ${BROWSERS.join(', ')}.`);
  }
  options.scale = Number(options.scale);
  if (!(options.scale >= 1 && options.scale <= 3)) throw new UsageError('--scale must be a number from 1 to 3.');
  let site;
  try {
    site = new URL(options.url);
  } catch {
    throw new UsageError(`--url "${options.url}" is not a web address.`);
  }
  if (site.protocol !== 'https:' && site.protocol !== 'http:') {
    throw new UsageError('--url must start with https:// or http://.');
  }
  options.url = site.origin;
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$/.test(options.name)) {
    throw new UsageError('--name must be letters, digits, dots, dashes or underscores, 41 at most.');
  }
  return options;
}

const STEP_KINDS = Object.freeze({
  wait: ['seconds'],
  // Wait until something is on the page ("timeoutSeconds", 30 unless said).
  waitFor: ['selector'],
  shot: [],
  focus: ['selector'],
  wheel: ['deltaY', 'ticks', 'seconds'],
  drag: ['from', 'to', 'seconds'],
  keys: ['key', 'times', 'seconds'],
  // A click on a place in the window ("at") or on something on the page
  // ("selector"); one of the two.
  click: [],
  move: ['at'],
  // Type into a box on the page; "enter": true presses Enter afterwards.
  type: ['selector', 'text'],
});

function isFraction(pair) {
  return Array.isArray(pair) && pair.length === 2 && pair.every((n) => typeof n === 'number' && n >= 0 && n <= 1);
}

function positive(value) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
}

/**
 * Check a tour and fill in what it leaves out. A tour says which page to
 * open, how to tell the map is ready, and the steps to fly. Places on the
 * screen are given as fractions of the window, [across, down], so the same
 * tour works at any size. Throws an Error naming the step at fault.
 */
export function validateTour(tour) {
  if (tour === null || typeof tour !== 'object' || Array.isArray(tour)) throw new Error('The tour must be an object.');
  const path = tour.path ?? '/';
  if (typeof path !== 'string' || !path.startsWith('/')) throw new Error('The tour\'s "path" must start with /.');
  const ready = tour.ready ?? null;
  if (ready !== null && (typeof ready.selector !== 'string' || ready.selector === '')) {
    throw new Error('The tour\'s "ready" needs a "selector".');
  }
  const probe = tour.probe ?? null;
  if (probe !== null) {
    if (typeof probe.selector !== 'string' || !Array.isArray(probe.attributes)
      || !probe.attributes.every((name) => typeof name === 'string')) {
      throw new Error('The tour\'s "probe" needs a "selector" and a list of "attributes".');
    }
  }
  if (!Array.isArray(tour.steps) || tour.steps.length === 0) throw new Error('The tour needs a list of "steps".');
  const names = new Set();
  const steps = tour.steps.map((step, index) => {
    const where = `Step ${index + 1}`;
    if (step === null || typeof step !== 'object') throw new Error(`${where} must be an object.`);
    if (!Object.hasOwn(STEP_KINDS, step.do)) {
      throw new Error(`${where}: "do" must be one of ${Object.keys(STEP_KINDS).join(', ')}.`);
    }
    for (const needed of STEP_KINDS[step.do]) {
      if (step[needed] === undefined) throw new Error(`${where} (${step.do}) needs "${needed}".`);
    }
    if (typeof step.name !== 'string' || !/^[a-z0-9][a-z0-9-]{0,50}$/.test(step.name)) {
      throw new Error(`${where} needs a "name" of small letters, digits and dashes (it names the picture).`);
    }
    if (names.has(step.name)) throw new Error(`${where}: the name "${step.name}" is used twice.`);
    names.add(step.name);
    for (const key of ['seconds', 'ticks', 'times', 'timeoutSeconds']) {
      if (step[key] !== undefined && !positive(step[key])) throw new Error(`${where}: "${key}" must be more than 0.`);
    }
    for (const key of ['from', 'to', 'at']) {
      if (step[key] !== undefined && !isFraction(step[key])) {
        throw new Error(`${where}: "${key}" must be two numbers from 0 to 1, [across, down].`);
      }
    }
    if (step.do === 'wheel' && (typeof step.deltaY !== 'number' || step.deltaY === 0)) {
      throw new Error(`${where}: "deltaY" must be a number other than 0 (below 0 zooms in on most maps).`);
    }
    if (step.do === 'drag' && !['left', 'middle', 'right'].includes(step.button ?? 'left')) {
      throw new Error(`${where}: "button" must be left, middle or right.`);
    }
    if (step.do === 'click' && (step.at === undefined) === (step.selector === undefined)) {
      throw new Error(`${where} (click) needs either "at" or "selector", not both.`);
    }
    for (const key of ['selector', 'text', 'key']) {
      if (step[key] !== undefined && (typeof step[key] !== 'string' || step[key] === '')) {
        throw new Error(`${where}: "${key}" must be some text.`);
      }
    }
    return {
      ...step,
      // Where the mouse is for a wheel step, unless the step says.
      at: step.at ?? (step.do === 'wheel' ? [0.5, 0.5] : undefined),
      button: step.button ?? 'left',
      // A picture after every step unless the step says not to.
      shot: step.shot ?? true,
      // Time for the map to come to rest before the picture.
      settleSeconds: step.settleSeconds ?? 1.5,
    };
  });
  return {
    about: typeof tour.about === 'string' ? tour.about : '',
    path,
    ready: ready === null ? null : { selector: ready.selector, timeoutSeconds: ready.timeoutSeconds ?? 90 },
    probe,
    steps,
  };
}

function percentile(sorted, share) {
  if (sorted.length === 0) return 0;
  return sorted[Math.min(sorted.length - 1, Math.floor(share * sorted.length))];
}

/**
 * Turn the times between frames (milliseconds) into what matters: how many
 * frames a second, and how often the picture hung. A screen that redraws 60
 * times a second gives 16.7 ms a frame; over 33.4 ms a frame was missed, and
 * over 100 ms the eye sees a stall.
 */
export function frameStats(deltas) {
  const frames = deltas.filter((ms) => Number.isFinite(ms) && ms >= 0);
  if (frames.length === 0) {
    return { frames: 0, seconds: 0, framesPerSecond: 0, typicalMs: 0, slowestMs: 0, worstOneInTwentyMs: 0, missed: 0, stalls: 0 };
  }
  const total = frames.reduce((sum, ms) => sum + ms, 0);
  const sorted = [...frames].sort((a, b) => a - b);
  const round = (n) => Math.round(n * 10) / 10;
  return {
    frames: frames.length,
    seconds: round(total / 1000),
    framesPerSecond: total > 0 ? round((frames.length * 1000) / total) : 0,
    typicalMs: round(percentile(sorted, 0.5)),
    worstOneInTwentyMs: round(percentile(sorted, 0.95)),
    slowestMs: round(sorted[sorted.length - 1]),
    missed: frames.filter((ms) => ms > 33.4).length,
    stalls: frames.filter((ms) => ms > 100).length,
  };
}

/** Whether the graphics were drawn by software, not by a graphics card. */
export function drawnBySoftware(renderer) {
  return /swiftshader|llvmpipe|software|softpipe|basic render/i.test(renderer ?? '');
}

export function folderName(when, name) {
  const two = (n) => String(n).padStart(2, '0');
  return `${when.getFullYear()}-${two(when.getMonth() + 1)}-${two(when.getDate())}_`
    + `${two(when.getHours())}${two(when.getMinutes())}${two(when.getSeconds())}-${name}`;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** The one-page report: what ran, how smoothly, every picture, the video. */
export function reportHtml(report) {
  const e = escapeHtml;
  const warnings = [];
  if (report.headless) warnings.push('Recorded with no window (--headless): the graphics card was not used. This says nothing about how the map looks or runs.');
  else if (drawnBySoftware(report.graphics.renderer)) warnings.push('The browser drew the map in software, not on the graphics card. Check that hardware acceleration is switched on in the browser.');
  if (!report.ready.ok) warnings.push(`The map never said it was ready (waited ${report.ready.waitedSeconds} s for ${report.ready.selector}). The tour did not run.`);
  const failed = report.steps.filter((step) => step.failed);
  if (failed.length > 0) warnings.push(`${failed.length} of the tour's steps did not work: ${failed.map((step) => step.name).join(', ')}. The steps after them still ran.`);
  const rows = report.steps.map((step) => `<tr><td>${e(step.name)}</td><td>${e(step.did)}${step.failed ? `<br><b>Did not work:</b> ${e(step.failed)}` : ''}</td>`
    + `<td>${step.stats.framesPerSecond}</td><td>${step.stats.typicalMs}</td><td>${step.stats.slowestMs}</td>`
    + `<td>${step.stats.missed}</td><td>${step.stats.stalls}</td></tr>`).join('\n');
  const pictures = report.steps.filter((step) => step.picture).map((step) => `<figure><img src="${e(step.picture)}" alt="${e(step.name)}">`
    + `<figcaption><b>${e(step.name)}</b>: ${e(step.did)}${step.probe ? `<br><code>${e(JSON.stringify(step.probe))}</code>` : ''}</figcaption></figure>`).join('\n');
  const list = (title, items) => (items.length === 0 ? '' : `<h3>${e(title)} (${items.length})</h3><ul>${items.slice(0, 50).map((item) => `<li><code>${e(item)}</code></li>`).join('')}</ul>`);
  const troubles = list('Errors on the page', report.pageErrors) + list('Errors in the browser\'s console', report.consoleErrors)
    + list('Requests that failed', report.failedRequests);
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Map recording: ${e(report.name)}</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;background:#111;color:#ddd;margin:24px;max-width:1800px}
h1,h2,h3{color:#fff}table{border-collapse:collapse}td,th{border:1px solid #444;padding:4px 10px;text-align:right}
td:nth-child(-n+2),th:nth-child(-n+2){text-align:left}figure{margin:24px 0}img,video{max-width:100%;border:1px solid #444}
.warn{background:#5a1d1d;border:1px solid #c44;padding:10px 14px;margin:10px 0}code{color:#f0ad56;word-break:break-all}dt{color:#999}
</style></head><body>
<h1>Map recording: ${e(report.name)}</h1>
${warnings.map((text) => `<p class="warn">${e(text)}</p>`).join('\n')}
<dl>
<dt>When</dt><dd>${e(report.when)}</dd>
<dt>Page</dt><dd>${e(report.page)}</dd>
<dt>Tour</dt><dd>${e(report.tour)}${report.about ? `: ${e(report.about)}` : ''}</dd>
<dt>Browser</dt><dd>${e(report.browser)} ${e(report.browserVersion)}</dd>
<dt>Graphics</dt><dd>${e(report.graphics.renderer || 'not reported')}${report.graphics.vendor ? ` (${e(report.graphics.vendor)})` : ''}</dd>
<dt>Window</dt><dd>${e(report.width)} x ${e(report.height)}, display scaling ${e(report.scale)}</dd>
</dl>
<h2>How smoothly it ran</h2>
<p>Frames a second while each step moved the map. "Missed" counts frames that took over 33 ms; "stalls" over 100 ms.</p>
<table><tr><th>Step</th><th>What it did</th><th>Frames a second</th><th>Typical frame (ms)</th><th>Slowest frame (ms)</th><th>Missed</th><th>Stalls</th></tr>
${rows}
</table>
${troubles ? `<h2>What went wrong</h2>${troubles}` : '<h2>What went wrong</h2><p>Nothing: no errors on the page, none in the console, no failed requests.</p>'}
<h2>The video</h2>
${report.video ? `<video src="${e(report.video)}" controls></video>` : '<p>No video was saved.</p>'}
<h2>The pictures</h2>
${pictures}
</body></html>
`;
}

/** A few words on what a step does, for the report. */
export function describeStep(step) {
  switch (step.do) {
    case 'wait': return `waited ${step.seconds} s`;
    case 'waitFor': return `waited for ${step.selector} to be on the page`;
    case 'shot': return 'took a picture';
    case 'focus': return `put the keyboard on ${step.selector}`;
    case 'wheel': return `turned the mouse wheel ${step.ticks} notches ${step.deltaY < 0 ? 'forward' : 'back'} over ${step.seconds} s`;
    case 'drag': return `dragged with the ${step.button} button from ${step.from.join(', ')} to ${step.to.join(', ')} over ${step.seconds} s`;
    case 'keys': return step.times === 1 ? `pressed ${step.key}` : `pressed ${step.key} ${step.times} times over ${step.seconds} s`;
    case 'click': return step.selector ? `clicked ${step.selector}` : `clicked at ${step.at.join(', ')}`;
    case 'move': return `moved the mouse to ${step.at.join(', ')}`;
    case 'type': return `typed "${step.text}" into ${step.selector}${step.enter ? ' and pressed Enter' : ''}`;
    default: return step.do;
  }
}
