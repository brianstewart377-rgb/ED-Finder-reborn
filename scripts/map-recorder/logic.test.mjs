// Tests for the map recorder's own logic. They need no browser:
//   node --test scripts/map-recorder
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  DEFAULTS, UsageError, describeStep, drawnBySoftware, folderName, frameStats, parseArgs, reportHtml, usage, validateTour,
} from './logic.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));

// -- the command line ------------------------------------------------------------

test('with no options it records the live site in Chrome at the plan\'s size', () => {
  const options = parseArgs([]);
  assert.equal(options.url, 'https://ed-finder.app');
  assert.equal(options.browser, 'chrome');
  assert.deepEqual([options.width, options.height, options.scale], [1760, 988, 1]);
  assert.deepEqual([options.pause, options.headless, options.help], [false, false, false]);
  assert.equal(options.tour, 'tours/explore.json');
});

test('options are read, and only the site\'s own address is kept from --url', () => {
  const options = parseArgs(['--url', 'http://127.0.0.1:4173/explore?x=1', '--browser', 'msedge', '--scale', '1.5',
    '--name', 'almanac-trial', '--pause', '--tour', 'tours/other.json']);
  assert.equal(options.url, 'http://127.0.0.1:4173');
  assert.deepEqual([options.browser, options.scale, options.name, options.pause, options.tour],
    ['msedge', 1.5, 'almanac-trial', true, 'tours/other.json']);
});

test('a wrong option is refused in plain words', () => {
  const refused = (argv, words) => assert.throws(() => parseArgs(argv), (error) => error instanceof UsageError && error.message.includes(words));
  refused(['explore'], 'is not an option');
  refused(['--speed', '2'], '--speed is not an option');
  refused(['--url'], '--url needs a value');
  refused(['--browser', 'safari'], 'chrome, msedge, firefox');
  refused(['--scale', '0'], '--scale must be a number from 1 to 3');
  refused(['--url', 'ftp://example.org'], 'https:// or http://');
  refused(['--url', 'not an address'], 'is not a web address');
  refused(['--name', '../elsewhere'], '--name must be');
  assert.match(usage(), /--pause/);
  assert.equal(parseArgs(['--help']).help, true);
});

// -- tours -----------------------------------------------------------------------

test('every tour in the tours folder can be used', async () => {
  const files = (await readdir(path.join(HERE, 'tours'))).filter((name) => name.endsWith('.json'));
  assert.ok(files.includes('explore.json'));
  assert.ok(files.includes(path.basename(DEFAULTS.tour)));
  for (const file of files) {
    const tour = validateTour(JSON.parse(await readFile(path.join(HERE, 'tours', file), 'utf8')));
    assert.ok(tour.path.startsWith('/'), file);
    assert.ok(tour.steps.length > 0, file);
    for (const step of tour.steps) assert.ok(describeStep(step).length > 0, `${file}: ${step.name}`);
  }
});

test('a tour fills in what its steps leave out', () => {
  const tour = validateTour({ steps: [
    { do: 'wheel', name: 'in', deltaY: -200, ticks: 5, seconds: 2 },
    { do: 'focus', name: 'keys', selector: '.map', shot: false },
    { do: 'click', name: 'pick', selector: 'button.go' },
  ] });
  assert.equal(tour.path, '/');
  assert.equal(tour.ready, null);
  assert.deepEqual(tour.steps[0].at, [0.5, 0.5]);
  assert.deepEqual([tour.steps[0].shot, tour.steps[0].settleSeconds, tour.steps[0].button], [true, 1.5, 'left']);
  assert.equal(tour.steps[1].shot, false);
  assert.equal(describeStep(tour.steps[0]), 'turned the mouse wheel 5 notches forward over 2 s');
  assert.equal(describeStep(tour.steps[2]), 'clicked button.go');
});

test('a tour with a mistake is refused, naming the step', () => {
  const step = (extra) => ({ steps: [{ do: 'wait', name: 'first', seconds: 1 }, extra] });
  const refused = (tour, words) => assert.throws(() => validateTour(tour), (error) => error.message.includes(words), words);
  refused([], 'must be an object');
  refused({ steps: [] }, 'needs a list of "steps"');
  refused({ path: 'explore', steps: [{ do: 'shot', name: 'a' }] }, 'must start with /');
  refused(step({ do: 'fly', name: 'x' }), 'Step 2: "do" must be one of');
  refused(step({ do: 'wheel', name: 'x', ticks: 3, seconds: 1 }), 'Step 2 (wheel) needs "deltaY"');
  refused(step({ do: 'wheel', name: 'x', deltaY: 0, ticks: 3, seconds: 1 }), 'other than 0');
  refused(step({ do: 'wait', name: 'x', seconds: -1 }), '"seconds" must be more than 0');
  refused(step({ do: 'move', name: 'x', at: [1.2, 0.5] }), 'two numbers from 0 to 1');
  refused(step({ do: 'drag', name: 'x', from: [0, 0], to: [1, 1], seconds: 1, button: 'thumb' }), 'left, middle or right');
  refused(step({ do: 'click', name: 'x' }), 'either "at" or "selector"');
  refused(step({ do: 'click', name: 'x', at: [0.5, 0.5], selector: 'a' }), 'either "at" or "selector"');
  refused(step({ do: 'type', name: 'x', selector: 'input', text: '' }), '"text" must be some text');
  refused(step({ do: 'waitFor', name: 'x' }), 'Step 2 (waitFor) needs "selector"');
  refused(step({ do: 'waitFor', name: 'x', selector: '.a', timeoutSeconds: 0 }), '"timeoutSeconds" must be more than 0');
  refused(step({ do: 'shot', name: 'Zoom In' }), 'needs a "name" of small letters');
  refused(step({ do: 'shot', name: 'first' }), 'the name "first" is used twice');
  refused({ ready: {}, steps: [{ do: 'shot', name: 'a' }] }, '"ready" needs a "selector"');
  refused({ probe: { selector: '.map' }, steps: [{ do: 'shot', name: 'a' }] }, '"probe" needs');
});

// -- frame times -------------------------------------------------------------------

test('a steady 60 frames a second reads as 60, with nothing missed', () => {
  const stats = frameStats(Array.from({ length: 120 }, () => 1000 / 60));
  assert.deepEqual([stats.frames, stats.seconds, stats.framesPerSecond, stats.missed, stats.stalls], [120, 2, 60, 0, 0]);
  assert.equal(stats.typicalMs, 16.7);
});

test('hangs are counted: over 33.4 ms is a missed frame, over 100 ms a stall', () => {
  const stats = frameStats([16.7, 16.7, 16.6, 40, 120, 16.7]);
  assert.deepEqual([stats.frames, stats.missed, stats.stalls, stats.slowestMs], [6, 2, 1, 120]);
  assert.equal(stats.framesPerSecond, 26.5);
  // The first frame after the sampler was last read can be anything: a
  // frame time that is not a number is left out, not counted as zero.
  assert.equal(frameStats([16.7, Number.NaN, 16.7]).frames, 2);
});

test('no frames at all gives zeros, not a division by nothing', () => {
  assert.deepEqual(frameStats([]), {
    frames: 0, seconds: 0, framesPerSecond: 0, typicalMs: 0, slowestMs: 0, worstOneInTwentyMs: 0, missed: 0, stalls: 0,
  });
});

// -- software or graphics card ---------------------------------------------------------

test('software drawing is told apart from a graphics card', () => {
  assert.equal(drawnBySoftware('ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)'), true);
  assert.equal(drawnBySoftware('llvmpipe (LLVM 15.0.7, 256 bits)'), true);
  assert.equal(drawnBySoftware('Microsoft Basic Render Driver'), true);
  assert.equal(drawnBySoftware('ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)'), false);
  assert.equal(drawnBySoftware('ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)'), false);
  assert.equal(drawnBySoftware(''), false);
});

// -- the report ------------------------------------------------------------------------

const REPORT = {
  name: 'try', when: 'Thu Nov 12 2026 22:00:00', page: 'https://ed-finder.app/explore', tour: 'explore.json',
  about: 'A tour <of> the map', browser: 'chrome', browserVersion: 'Chrome/153', headless: false,
  width: 1760, height: 988, scale: 1,
  graphics: { renderer: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)', vendor: 'Google Inc. (NVIDIA)', webgl: 'WebGL 2.0' },
  ready: { ok: true, selector: '[data-renderer-state="ready"]', waitedSeconds: 2 },
  steps: [
    { name: 'whole-galaxy', did: 'waited 4 s', picture: 'shots/01-whole-galaxy.png', stats: frameStats([16.7, 16.7]), probe: { 'data-camera-revision': '2' } },
    { name: 'keyboard', did: 'put the keyboard on .map', picture: null, stats: frameStats([16.7]), probe: null },
  ],
  pageErrors: [], consoleErrors: [], failedRequests: [], video: 'video.webm',
};

test('the report shows each step, each picture and the video, and says when nothing went wrong', () => {
  const html = reportHtml(REPORT);
  assert.match(html, /<h1>Map recording: try<\/h1>/);
  assert.match(html, /NVIDIA GeForce RTX 4070/);
  assert.match(html, /<img src="shots\/01-whole-galaxy\.png"/);
  assert.equal(html.match(/<img /g).length, 1, 'a step with no picture has none shown');
  assert.match(html, /<video src="video\.webm" controls>/);
  assert.match(html, /Nothing: no errors on the page/);
  assert.doesNotMatch(html, /class="warn"/);
  // Text from outside is shown as text, never as part of the page.
  assert.match(html, /A tour &lt;of&gt; the map/);
});

test('the report warns loudly about software drawing, a window-less run and a map that was not ready', () => {
  const software = reportHtml({ ...REPORT, graphics: { ...REPORT.graphics, renderer: 'SwiftShader' } });
  assert.match(software, /class="warn">The browser drew the map in software/);
  const noWindow = reportHtml({ ...REPORT, headless: true });
  assert.match(noWindow, /class="warn">Recorded with no window/);
  const notReady = reportHtml({ ...REPORT, ready: { ok: false, selector: '.ready', waitedSeconds: 90 } });
  assert.match(notReady, /The map never said it was ready \(waited 90 s/);
});

test('the report says which steps did not work', () => {
  const steps = [REPORT.steps[0], { ...REPORT.steps[1], failed: 'locator.click: Timeout 10000ms exceeded.' }];
  const html = reportHtml({ ...REPORT, steps });
  assert.match(html, /class="warn">1 of the tour&#39;s steps did not work: keyboard\. The steps after them still ran\./);
  assert.match(html, /<b>Did not work:<\/b> locator\.click: Timeout 10000ms exceeded\./);
});

test('the report lists what went wrong', () => {
  const html = reportHtml({ ...REPORT, pageErrors: ['boom <script>'], consoleErrors: ['a', 'b'], failedRequests: ['GET /api/x (answered 503)'] });
  assert.match(html, /Errors on the page \(1\)/);
  assert.match(html, /boom &lt;script&gt;/);
  assert.match(html, /Errors in the browser&#39;s console \(2\)/);
  assert.match(html, /Requests that failed \(1\)/);
  assert.doesNotMatch(html, /Nothing: no errors/);
});

test('a recording\'s folder is named by when it was made and what it was called', () => {
  assert.equal(folderName(new Date(2026, 10, 12, 9, 5, 7), 'almanac'), '2026-11-12_090507-almanac');
});
