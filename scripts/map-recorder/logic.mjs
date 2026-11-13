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
  // "drawnWith" names an attribute, on the same thing, that says which
  // drawing method the map chose (ours says WEBGPU or WEBGL2).
  if (ready !== null && ready.drawnWith !== undefined && (typeof ready.drawnWith !== 'string' || ready.drawnWith === '')) {
    throw new Error('The tour\'s "ready" has a "drawnWith" that is not the name of an attribute.');
  }
  // "probe": attributes to read off the page after each step, and to watch
  // for changes during it. One thing on the page, or a list of them.
  const probes = tour.probe === undefined || tour.probe === null ? [] : [tour.probe].flat();
  const probed = new Set();
  for (const probe of probes) {
    if (probe === null || typeof probe !== 'object' || typeof probe.selector !== 'string' || probe.selector === ''
      || !Array.isArray(probe.attributes) || !probe.attributes.every((name) => typeof name === 'string' && name !== '')) {
      throw new Error('The tour\'s "probe" needs a "selector" and a list of "attributes".');
    }
    for (const name of probe.attributes) {
      if (probed.has(name)) throw new Error(`The tour's "probe" names the attribute "${name}" twice.`);
      probed.add(name);
    }
  }
  // "requests": which of the page's own requests to time, by the start of
  // their path. Ours all sit under /api/.
  const requests = tour.requests ?? null;
  if (requests !== null && (typeof requests.under !== 'string' || !requests.under.startsWith('/'))) {
    throw new Error('The tour\'s "requests" needs an "under" that starts with /, such as /api/.');
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
    ready: ready === null ? null : {
      selector: ready.selector, timeoutSeconds: ready.timeoutSeconds ?? 90, drawnWith: ready.drawnWith ?? null,
    },
    probe: probes.length === 0 ? null : probes.map((probe) => ({ selector: probe.selector, attributes: [...probe.attributes] })),
    requests: requests === null ? null : { under: requests.under },
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

/**
 * Whether a request ended because the page itself dropped it before an answer
 * came. The map does this all the time, on purpose: once the view has moved
 * on it stops asking for the stars of the old view, and the search box drops
 * its question for "So" when "Sol" is typed. That is not a failure, and
 * counting it as one buries the real ones. Chrome and Edge call it
 * net::ERR_ABORTED; Firefox calls it NS_BINDING_ABORTED.
 */
export function droppedByThePage(errorText) {
  return /ERR_ABORTED|NS_BINDING_ABORTED/i.test(errorText ?? '');
}

/**
 * A request's address as the report shows it: without the site's own name,
 * short enough to read, and with anything that looks like a private key
 * taken out. A recording gets passed on, and some addresses carry a key that
 * belongs to one browser or one person (a long run of letters and digits).
 * Plain numbers stay: a system's id is not a secret.
 */
export function tidyAddress(address, origin) {
  let url;
  try {
    url = new URL(address);
  } catch {
    return String(address).slice(0, 240);
  }
  // A key: 20 or more letters, digits and dashes, with a digit or a capital
  // among them. That leaves alone a long route name in small letters.
  const key = (text) => /^[A-Za-z0-9_~.-]{20,}$/.test(text) && /[0-9A-Z]/.test(text) && /[A-Za-z]/.test(text);
  const path = url.pathname.split('/').map((part) => (key(part) ? '<key>' : part)).join('/');
  const asked = [...url.searchParams].map(([name, value]) => `${name}=${key(value) ? '<key>' : value}`).join('&');
  const shown = `${url.origin === origin ? '' : url.origin}${path}${asked ? `?${asked}` : ''}`;
  return shown.length > 240 ? `${shown.slice(0, 239)}…` : shown;
}

const seconds = (value) => `${Math.round(value * 10) / 10} s`;

/** Count a list of timed requests, and find the answer that took longest. */
export function requestTotals(requests) {
  const count = (outcome) => requests.filter((request) => request.outcome === outcome).length;
  const answered = requests.filter((request) => request.outcome === 'answered' && request.tookSeconds !== null);
  return {
    asked: requests.length,
    answered: count('answered'),
    dropped: count('dropped'),
    failed: count('failed'),
    waiting: count('waiting'),
    slowest: answered.length === 0 ? null : answered.reduce((worst, request) => (request.tookSeconds > worst.tookSeconds ? request : worst)),
  };
}

/** A few words on the requests one step asked: "6 asked: 5 answered (slowest 1.9 s), 1 dropped". */
export function requestNote(requests) {
  if (requests.length === 0) return '';
  const totals = requestTotals(requests);
  const parts = [];
  if (totals.answered > 0) parts.push(`${totals.answered} answered${totals.slowest ? ` (slowest ${seconds(totals.slowest.tookSeconds)})` : ''}`);
  if (totals.dropped > 0) parts.push(`${totals.dropped} dropped`);
  if (totals.failed > 0) parts.push(`${totals.failed} failed`);
  if (totals.waiting > 0) parts.push(`${totals.waiting} never answered`);
  return `${totals.asked} asked: ${parts.join(', ')}`;
}

/**
 * Everything the recorder timed, in the order it happened, as lines of text:
 * each request asked and answered, each change the map reported about
 * itself, and each time the picture stood still. Grouped under the step
 * that was running. This is what answers "why did that take so long".
 */
export function timelineLines(report) {
  const events = [];
  for (const request of report.requests ?? []) {
    events.push({ at: request.atSeconds, text: `asked     ${request.method} ${request.address}` });
    if (request.outcome === 'waiting') continue;
    const took = request.tookSeconds ?? 0;
    const ending = {
      answered: `answered  ${request.method} ${request.address}: ${request.status ?? 'no status'}, after ${seconds(took)}${request.kilobytes === null ? '' : `, ${request.kilobytes} KB`}`,
      dropped: `dropped   ${request.method} ${request.address}: by the page, after ${seconds(took)}`,
      failed: `FAILED    ${request.method} ${request.address}: ${request.why ?? 'no reason given'}, after ${seconds(took)}`,
    }[request.outcome];
    events.push({ at: request.atSeconds + took, text: ending });
  }
  for (const change of report.changes ?? []) events.push({ at: change.atSeconds, change });
  for (const freeze of report.freezes ?? []) {
    events.push({ at: freeze.atSeconds, text: `froze     the picture stood still for ${Math.round(freeze.ms)} ms` });
  }
  if (events.length === 0) return [];
  events.sort((a, b) => a.at - b.at);
  const starts = report.steps.filter((step) => typeof step.startedSeconds === 'number').sort((a, b) => a.startedSeconds - b.startedSeconds);
  const lines = [];
  let heading = null;
  // An attribute that changes many times with no request or freeze in
  // between (the camera's position and tilt while it moves, say) is one
  // line: where it began and where it ended. Each attribute is followed by
  // itself, so two that change turn about (position, tilt, position, tilt)
  // make two lines, not hundreds.
  const runs = new Map();
  const endRuns = () => {
    for (const run of [...runs.values()].sort((a, b) => a.at - b.at)) {
      const often = run.count === 1 ? '' : `, in ${run.count} changes over ${seconds(run.last - run.at)}`;
      lines.push(`  ${run.at.toFixed(2).padStart(7)} s  changed   ${run.name}: ${run.was ?? 'nothing'} -> ${run.value ?? 'nothing'}${often}`);
    }
    runs.clear();
  };
  for (const event of events) {
    const step = starts.findLast((candidate) => candidate.startedSeconds <= event.at);
    const title = step ? `${step.name} (began at ${seconds(step.startedSeconds)}: ${step.did})` : 'opening the page';
    if (title !== heading) {
      endRuns();
      if (heading !== null) lines.push('');
      lines.push(title);
      heading = title;
    }
    if (event.change) {
      const run = runs.get(event.change.name);
      if (run) {
        run.value = event.change.value;
        run.last = event.at;
        run.count += 1;
      } else {
        runs.set(event.change.name, {
          at: event.at, last: event.at, count: 1, name: event.change.name, was: event.change.was, value: event.change.value,
        });
      }
    } else {
      endRuns();
      lines.push(`  ${event.at.toFixed(2).padStart(7)} s  ${event.text}`);
    }
  }
  endRuns();
  return lines;
}

/** The lines printed when a recording ends: what to know before opening it. */
export function summaryLines(report) {
  const lines = [];
  if (report.graphics.mapDrawnWith) lines.push(`The map drew with: ${report.graphics.mapDrawnWith}.`);
  const moving = report.steps.filter((step) => step.stats.frames > 0);
  if (moving.length > 0) {
    const slowest = moving.reduce((worst, step) => (step.stats.framesPerSecond < worst.stats.framesPerSecond ? step : worst));
    lines.push(`Slowest step: ${slowest.name}, ${slowest.stats.framesPerSecond} frames a second.`);
    const longest = moving.reduce((worst, step) => (step.stats.slowestMs > worst.stats.slowestMs ? step : worst));
    lines.push(`Longest single frame: ${longest.stats.slowestMs} ms, in ${longest.name}.`);
    const stalled = moving.filter((step) => step.stats.stalls > 0).map((step) => step.name);
    lines.push(stalled.length === 0
      ? 'No step had a stall (a frame over 100 ms).'
      : `Steps with a stall (a frame over 100 ms): ${stalled.join(', ')}.`);
  }
  const didNotWork = report.steps.filter((step) => step.failed).map((step) => step.name);
  if (didNotWork.length > 0) lines.push(`Steps that did not work: ${didNotWork.join(', ')}.`);
  lines.push(`Errors on the page: ${report.pageErrors.length}. In the console: ${report.consoleErrors.length}. Requests that failed: ${report.failedRequests.length}.`);
  const dropped = (report.droppedRequests ?? []).length;
  if (dropped > 0) lines.push(`Requests the page dropped itself before an answer came: ${dropped}. That is normal while the map moves.`);
  if ((report.requests ?? []).length > 0) {
    const totals = requestTotals(report.requests);
    lines.push(`The page's data requests: ${totals.asked} asked, ${totals.answered} answered, ${totals.dropped} dropped by the page, ${totals.failed} failed${totals.waiting > 0 ? `, ${totals.waiting} never answered` : ''}.`);
    if (totals.slowest) lines.push(`Slowest answer: ${seconds(totals.slowest.tookSeconds)}, for ${totals.slowest.method} ${totals.slowest.address.split('?')[0]}, asked in ${totals.slowest.step}.`);
  }
  return lines;
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
  const timed = report.requests ?? [];
  const rows = report.steps.map((step) => `<tr><td>${e(step.name)}</td><td>${e(step.did)}${step.failed ? `<br><b>Did not work:</b> ${e(step.failed)}` : ''}</td>`
    + `<td>${step.stats.framesPerSecond}</td><td>${step.stats.typicalMs}</td><td>${step.stats.slowestMs}</td>`
    + `<td>${step.stats.missed}</td><td>${step.stats.stalls}</td>`
    + `<td>${e(requestNote(timed.filter((request) => request.step === step.name)))}</td></tr>`).join('\n');
  const timeline = timelineLines(report);
  const timelineNote = timeline.length === 0 ? '' : '<h2>What happened when</h2>'
    + '<p>Every data request the page made and when its answer came, every change the map reported about itself, and every time the picture stood still. Times are seconds since the page was opened. The same list is in <code>timeline.txt</code>.</p>'
    + `<details><summary>Show the timeline (${timeline.length} lines)</summary><pre>${e(timeline.join('\n'))}</pre></details>`;
  const pictures = report.steps.filter((step) => step.picture).map((step) => `<figure><img src="${e(step.picture)}" alt="${e(step.name)}">`
    + `<figcaption><b>${e(step.name)}</b>: ${e(step.did)}${step.probe ? `<br><code>${e(JSON.stringify(step.probe))}</code>` : ''}</figcaption></figure>`).join('\n');
  const list = (title, items) => (items.length === 0 ? '' : `<h3>${e(title)} (${items.length})</h3><ul>${items.slice(0, 50).map((item) => `<li><code>${e(item)}</code></li>`).join('')}</ul>`);
  const troubles = list('Errors on the page', report.pageErrors) + list('Errors in the browser\'s console', report.consoleErrors)
    + list('Requests that failed', report.failedRequests);
  const dropped = report.droppedRequests ?? [];
  const droppedNote = dropped.length === 0 ? '' : `<h3>Requests the page dropped itself (${dropped.length})</h3>`
    + '<p>Not failures. The page stopped these before an answer came, as the map does when the view moves on and the search box does when another letter is typed.</p>'
    + `<details><summary>Show them</summary><ul>${dropped.slice(0, 200).map((item) => `<li><code>${e(item)}</code></li>`).join('')}</ul></details>`;
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Map recording: ${e(report.name)}</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;background:#111;color:#ddd;margin:24px;max-width:1800px}
h1,h2,h3{color:#fff}table{border-collapse:collapse}td,th{border:1px solid #444;padding:4px 10px;text-align:right}
td:nth-child(-n+2),th:nth-child(-n+2){text-align:left}figure{margin:24px 0}img,video{max-width:100%;border:1px solid #444}
.warn{background:#5a1d1d;border:1px solid #c44;padding:10px 14px;margin:10px 0}code{color:#f0ad56;word-break:break-all}dt{color:#999}
td:last-child,th:last-child{text-align:left}pre{font:13px/1.45 ui-monospace,Consolas,monospace;white-space:pre-wrap;word-break:break-all}
</style></head><body>
<h1>Map recording: ${e(report.name)}</h1>
${warnings.map((text) => `<p class="warn">${e(text)}</p>`).join('\n')}
<dl>
<dt>When</dt><dd>${e(report.when)}</dd>
<dt>Page</dt><dd>${e(report.page)}</dd>
<dt>Tour</dt><dd>${e(report.tour)}${report.about ? `: ${e(report.about)}` : ''}</dd>
<dt>Browser</dt><dd>${e(report.browser)} ${e(report.browserVersion)}</dd>
<dt>Graphics</dt><dd>${e(report.graphics.renderer || 'not reported')}${report.graphics.vendor ? ` (${e(report.graphics.vendor)})` : ''}</dd>
<dt>The map drew with</dt><dd>${e(report.graphics.mapDrawnWith || 'not reported')}</dd>
<dt>Window</dt><dd>${e(report.width)} x ${e(report.height)}, display scaling ${e(report.scale)}</dd>
</dl>
<h2>How smoothly it ran</h2>
<p>Frames a second while each step moved the map. "Missed" counts frames that took over 33 ms; "stalls" over 100 ms. "Data requests" are the ones the page made during the step.</p>
<table><tr><th>Step</th><th>What it did</th><th>Frames a second</th><th>Typical frame (ms)</th><th>Slowest frame (ms)</th><th>Missed</th><th>Stalls</th><th>Data requests</th></tr>
${rows}
</table>
${timelineNote}
${troubles ? `<h2>What went wrong</h2>${troubles}` : '<h2>What went wrong</h2><p>Nothing: no errors on the page, none in the console, no failed requests.</p>'}
${droppedNote}
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
