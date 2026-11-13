// Tests for the map recorder's own logic. They need no browser:
//   node --test scripts/map-recorder
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  DEFAULTS, UsageError, describeStep, drawnBySoftware, droppedByThePage, folderName, frameStats, parseArgs, reportHtml,
  requestNote, requestTotals, summaryLines, tidyAddress, timelineLines, usage, validateTour,
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
  refused({ ready: { selector: '.map', drawnWith: 7 }, steps: [{ do: 'shot', name: 'a' }] }, '"drawnWith" that is not the name of an attribute');
  refused({ probe: [{ selector: '.map', attributes: ['a'] }, { selector: '.count', attributes: ['a'] }], steps: [{ do: 'shot', name: 'a' }] }, 'names the attribute "a" twice');
  refused({ probe: [{ selector: '.map', attributes: ['a'] }, { attributes: ['b'] }], steps: [{ do: 'shot', name: 'a' }] }, '"probe" needs');
  refused({ requests: { under: 'api/' }, steps: [{ do: 'shot', name: 'a' }] }, '"requests" needs an "under" that starts with /');
  refused({ requests: {}, steps: [{ do: 'shot', name: 'a' }] }, '"requests" needs an "under"');
});

test('a tour can probe one thing on the page or several, and can say which requests to time', async () => {
  const steps = [{ do: 'shot', name: 'a' }];
  assert.equal(validateTour({ steps }).probe, null);
  assert.equal(validateTour({ steps }).requests, null);
  // One thing, as the first tours had it, becomes a list of one.
  assert.deepEqual(validateTour({ probe: { selector: '.map', attributes: ['a', 'b'] }, steps }).probe, [{ selector: '.map', attributes: ['a', 'b'] }]);
  assert.deepEqual(
    validateTour({ probe: [{ selector: '.map', attributes: ['a'] }, { selector: '.count', attributes: ['n'] }], steps }).probe,
    [{ selector: '.map', attributes: ['a'] }, { selector: '.count', attributes: ['n'] }],
  );
  assert.deepEqual(validateTour({ requests: { under: '/api/' }, steps }).requests, { under: '/api/' });
  // Our own tour watches the camera, the number of stars drawn, and /api/.
  const explore = validateTour(JSON.parse(await readFile(path.join(HERE, 'tours', 'explore.json'), 'utf8')));
  assert.deepEqual(explore.requests, { under: '/api/' });
  const watched = explore.probe.flatMap((probe) => probe.attributes);
  assert.ok(watched.includes('data-camera-transition-active'));
  assert.ok(watched.includes('data-catalogue-star-count'));
});

test('a tour can say where the map tells which drawing method it chose', async () => {
  const steps = [{ do: 'shot', name: 'a' }];
  assert.equal(validateTour({ ready: { selector: '.map' }, steps }).ready.drawnWith, null);
  assert.equal(validateTour({ ready: { selector: '.map', drawnWith: 'data-backend' }, steps }).ready.drawnWith, 'data-backend');
  // Our own map's tour asks: WebGPU and WebGL2 are two different drawing
  // paths, and a recording has to say which one it is a recording of.
  const explore = validateTour(JSON.parse(await readFile(path.join(HERE, 'tours', 'explore.json'), 'utf8')));
  assert.equal(explore.ready.drawnWith, 'data-renderer-backend');
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

// -- requests ----------------------------------------------------------------------

test('a request the page dropped itself is told apart from one that failed', () => {
  // What Chrome and Edge say, and what Firefox says, when the page stops a
  // request before an answer came.
  assert.equal(droppedByThePage('net::ERR_ABORTED'), true);
  assert.equal(droppedByThePage('NS_BINDING_ABORTED'), true);
  // Real failures.
  assert.equal(droppedByThePage('net::ERR_CONNECTION_REFUSED'), false);
  assert.equal(droppedByThePage('net::ERR_NAME_NOT_RESOLVED'), false);
  assert.equal(droppedByThePage('net::ERR_TIMED_OUT'), false);
  assert.equal(droppedByThePage('failed'), false);
  assert.equal(droppedByThePage(undefined), false);
});

test('an address in a report loses the site\'s name and anything that looks like a private key', () => {
  const site = 'https://ed-finder.app';
  assert.equal(tidyAddress('https://ed-finder.app/api/map/systems?min_x=-2000&max_x=0&limit=5000', site), '/api/map/systems?min_x=-2000&max_x=0&limit=5000');
  // A system's id is a plain number, not a secret: it stays.
  assert.equal(tidyAddress('https://ed-finder.app/api/v1/systems/10477373803/bodies', site), '/api/v1/systems/10477373803/bodies');
  // A long run of letters and digits is a key that belongs to one browser
  // or one person. It must not travel with a recording. (An invented one.)
  assert.equal(tidyAddress('https://ed-finder.app/api/v2/watchlist/Qw3rTy7uI0pAsDfGh1JkLzXcVbNm2468', site), '/api/v2/watchlist/<key>');
  assert.equal(tidyAddress('https://ed-finder.app/api/thing?token=Qw3rTy7uI0pAsDfGh1JkLzXcVbNm2468&page=2', site), '/api/thing?token=<key>&page=2');
  assert.equal(tidyAddress('https://ed-finder.app/api/sessions/3f2b8c1e-7a4d-4e9b-b1c2-9d8e7f6a5b4c', site), '/api/sessions/<key>');
  // A long route name in small letters is not a key.
  assert.equal(tidyAddress('https://ed-finder.app/api/colonisation-candidate-summaries', site), '/api/colonisation-candidate-summaries');
  // Another site keeps its name. Something that is not an address is cut short, not thrown.
  assert.equal(tidyAddress('https://example.org/font.woff2', site), 'https://example.org/font.woff2');
  assert.equal(tidyAddress('not an address', site), 'not an address');
  assert.equal(tidyAddress(`https://ed-finder.app/api/x?q=${'a'.repeat(400)}`, site).length, 240);
});

// -- timed requests and the timeline ---------------------------------------------------

const asked = (step, atSeconds, address, outcome, tookSeconds, more = {}) => ({
  step, atSeconds, method: 'GET', address, outcome, status: outcome === 'answered' ? 200 : null, why: null, tookSeconds, kilobytes: null, ...more,
});

test('timed requests are counted by how they ended, and the slowest answer is found', () => {
  const requests = [
    asked('a', 1, '/api/one', 'answered', 0.4),
    asked('a', 1.1, '/api/two', 'answered', 2.94),
    asked('a', 1.2, '/api/three', 'dropped', 9),
    asked('b', 5, '/api/four', 'failed', 0.2, { why: 'net::ERR_CONNECTION_RESET' }),
    asked('b', 6, '/api/five', 'waiting', null),
  ];
  const totals = requestTotals(requests);
  assert.deepEqual({ ...totals, slowest: totals.slowest.address }, { asked: 5, answered: 2, dropped: 1, failed: 1, waiting: 1, slowest: '/api/two' });
  // A dropped request's long wait is not an answer's: it is never "slowest".
  assert.equal(requestTotals([asked('a', 1, '/api/x', 'dropped', 30)]).slowest, null);
  assert.equal(requestNote(requests.filter((request) => request.step === 'a')), '3 asked: 2 answered (slowest 2.9 s), 1 dropped');
  assert.equal(requestNote(requests.filter((request) => request.step === 'b')), '2 asked: 1 failed, 1 never answered');
  assert.equal(requestNote([]), '');
});

test('the timeline puts requests, changes and freezes in order, under the step that was running', () => {
  const lines = timelineLines({
    steps: [
      { name: 'whole-galaxy', did: 'waited 5 s', startedSeconds: 2 },
      { name: 'fly-to-sol', did: 'pressed Enter', startedSeconds: 10 },
    ],
    requests: [
      asked('opening the page', 0.5, '/api/map/heatmap?voxel_size=590', 'answered', 1.25, { kilobytes: 412.5 }),
      asked('fly-to-sol', 10.3, '/api/map/systems?min_x=-2000', 'dropped', 0.3),
      asked('fly-to-sol', 10.7, '/api/map/systems?min_x=-1000', 'answered', 1.8),
      asked('fly-to-sol', 11, '/api/local/search', 'failed', 0.5, { method: 'POST', why: 'net::ERR_CONNECTION_RESET' }),
      asked('fly-to-sol', 12, '/api/never', 'waiting', null),
    ],
    changes: [
      { atSeconds: 10.05, name: 'data-camera-transition-active', value: 'true', was: 'false' },
      { atSeconds: 10.1, name: 'data-camera-revision', value: '3', was: '2' },
      { atSeconds: 10.2, name: 'data-camera-revision', value: '4', was: '3' },
      { atSeconds: 10.4, name: 'data-camera-revision', value: '5', was: '4' },
      { atSeconds: 12.6, name: 'data-catalogue-star-count', value: '4554', was: '8000' },
      { atSeconds: 12.7, name: 'data-last-settled-focus-id64', value: '10477373803', was: null },
    ],
    freezes: [{ atSeconds: 10.01, ms: 616.7 }],
  });
  assert.deepEqual(lines, [
    'opening the page',
    '     0.50 s  asked     GET /api/map/heatmap?voxel_size=590',
    '     1.75 s  answered  GET /api/map/heatmap?voxel_size=590: 200, after 1.3 s, 412.5 KB',
    '',
    'fly-to-sol (began at 10 s: pressed Enter)',
    '    10.01 s  froze     the picture stood still for 617 ms',
    '    10.05 s  changed   data-camera-transition-active: false -> true',
    // Changes running to the same attribute are one line. The request asked
    // at 10.3 s comes between the second and third, so it ends the run.
    '    10.10 s  changed   data-camera-revision: 2 -> 4, in 2 changes over 0.1 s',
    '    10.30 s  asked     GET /api/map/systems?min_x=-2000',
    '    10.40 s  changed   data-camera-revision: 4 -> 5',
    '    10.60 s  dropped   GET /api/map/systems?min_x=-2000: by the page, after 0.3 s',
    '    10.70 s  asked     GET /api/map/systems?min_x=-1000',
    '    11.00 s  asked     POST /api/local/search',
    '    11.50 s  FAILED    POST /api/local/search: net::ERR_CONNECTION_RESET, after 0.5 s',
    '    12.00 s  asked     GET /api/never',
    '    12.50 s  answered  GET /api/map/systems?min_x=-1000: 200, after 1.8 s',
    '    12.60 s  changed   data-catalogue-star-count: 8000 -> 4554',
    '    12.70 s  changed   data-last-settled-focus-id64: nothing -> 10477373803',
  ]);
  // Nothing timed (a tour that asks for none of it, or an older report): no timeline.
  assert.deepEqual(timelineLines({ steps: [] }), []);
});

// -- the report ------------------------------------------------------------------------

const REPORT = {
  name: 'try', when: 'Thu Nov 12 2026 22:00:00', page: 'https://ed-finder.app/explore', tour: 'explore.json',
  about: 'A tour <of> the map', browser: 'chrome', browserVersion: 'Chrome/153', headless: false,
  width: 1760, height: 988, scale: 1,
  graphics: {
    renderer: 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)', vendor: 'Google Inc. (NVIDIA)', webgl: 'WebGL 2.0', mapDrawnWith: 'WEBGPU',
  },
  ready: { ok: true, selector: '[data-renderer-state="ready"]', waitedSeconds: 2 },
  steps: [
    { name: 'whole-galaxy', did: 'waited 4 s', picture: 'shots/01-whole-galaxy.png', stats: frameStats([16.7, 16.7]), probe: { 'data-camera-revision': '2' } },
    { name: 'keyboard', did: 'put the keyboard on .map', picture: null, stats: frameStats([16.7]), probe: null },
  ],
  pageErrors: [], consoleErrors: [], failedRequests: [], droppedRequests: [], video: 'video.webm',
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
  assert.match(html, /<dt>The map drew with<\/dt><dd>WEBGPU<\/dd>/);
  assert.match(reportHtml({ ...REPORT, graphics: { ...REPORT.graphics, mapDrawnWith: '' } }), /<dt>The map drew with<\/dt><dd>not reported<\/dd>/);
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

test('the report page shows each step\'s data requests and the timeline', () => {
  const steps = [
    { name: 'whole-galaxy', did: 'waited 4 s', startedSeconds: 2, picture: null, stats: frameStats([16.7, 16.7]), probe: null },
    { name: 'fly-to-sol', did: 'pressed Enter', startedSeconds: 10, picture: null, stats: frameStats([16.7]), probe: null },
  ];
  const requests = [
    asked('fly-to-sol', 10.2, '/api/map/systems?min_x=-2000', 'dropped', 0.3),
    asked('fly-to-sol', 10.6, '/api/map/systems?min_x=-1000', 'answered', 1.9),
  ];
  const html = reportHtml({ ...REPORT, steps, requests, changes: [], freezes: [] });
  assert.match(html, /<th>Data requests<\/th>/);
  assert.match(html, /<td>fly-to-sol<\/td>.*<td>2 asked: 1 answered \(slowest 1\.9 s\), 1 dropped<\/td><\/tr>/);
  assert.match(html, /<td>whole-galaxy<\/td>.*<td><\/td><\/tr>/);
  assert.match(html, /<h2>What happened when<\/h2>/);
  assert.match(html, /Show the timeline \(5 lines\)/);
  assert.match(html, /10\.50 s {2}dropped {3}GET \/api\/map\/systems\?min_x=-2000: by the page, after 0\.3 s/);
  // A report with nothing timed has no timeline section, and still reads.
  assert.doesNotMatch(reportHtml(REPORT), /What happened when/);
});

test('requests the page dropped itself are shown apart, and are not called failures', () => {
  const dropped = ['fly-to-sol-start: GET /api/map/systems?min_x=0 (net::ERR_ABORTED)', 'type-sol: GET /api/local/autocomplete?q=So (net::ERR_ABORTED)'];
  const html = reportHtml({ ...REPORT, droppedRequests: dropped });
  assert.match(html, /Nothing: no errors on the page, none in the console, no failed requests\./);
  assert.match(html, /Requests the page dropped itself \(2\)/);
  assert.match(html, /Not failures\./);
  assert.match(html, /fly-to-sol-start: GET \/api\/map\/systems\?min_x=0 \(net::ERR_ABORTED\)/);
  assert.doesNotMatch(html, /Requests that failed/);
  // With none dropped, the report does not mention them.
  assert.doesNotMatch(reportHtml(REPORT), /dropped itself/);
  // A report saved before this was added has no list of them: still readable.
  const { droppedRequests: _gone, ...older } = REPORT;
  assert.doesNotMatch(reportHtml(older), /dropped itself/);
});

// -- the lines printed at the end ------------------------------------------------------

test('the closing lines name the slowest step, the longest frame, stalls, and real failures only', () => {
  const steps = [
    { name: 'whole-galaxy', did: 'waited 4 s', failed: null, picture: null, stats: frameStats(Array(60).fill(16.7)), probe: null },
    { name: 'fly-to-sol-start', did: 'pressed Enter', failed: null, picture: null, stats: frameStats([16.7, 16.7, 240, 16.7]), probe: null },
    { name: 'zoom-in', did: 'turned the wheel', failed: null, picture: null, stats: frameStats(Array(30).fill(33.3)), probe: null },
    { name: 'keyboard', did: 'put the keyboard on .map', failed: null, picture: null, stats: frameStats([]), probe: null },
  ];
  const lines = summaryLines({
    ...REPORT, steps, failedRequests: ['zoom-in: GET /api/x (answered 503)'], droppedRequests: ['a', 'b', 'c'],
  });
  assert.deepEqual(lines, [
    'The map drew with: WEBGPU.',
    'Slowest step: fly-to-sol-start, 13.8 frames a second.',
    'Longest single frame: 240 ms, in fly-to-sol-start.',
    'Steps with a stall (a frame over 100 ms): fly-to-sol-start.',
    'Errors on the page: 0. In the console: 0. Requests that failed: 1.',
    'Requests the page dropped itself before an answer came: 3. That is normal while the map moves.',
  ]);
});

test('the closing lines say so when nothing stalled, and name steps that did not work', () => {
  const steady = { name: 'whole-galaxy', did: 'waited 4 s', failed: null, picture: null, stats: frameStats(Array(60).fill(16.7)), probe: null };
  const broken = { name: 'pick', did: 'clicked .gone', failed: 'locator.click: Timeout 10000ms exceeded.', picture: null, stats: frameStats([16.7]), probe: null };
  const lines = summaryLines({ ...REPORT, graphics: { ...REPORT.graphics, mapDrawnWith: '' }, steps: [steady, broken] });
  assert.deepEqual(lines, [
    'Slowest step: whole-galaxy, 59.9 frames a second.',
    'Longest single frame: 16.7 ms, in whole-galaxy.',
    'No step had a stall (a frame over 100 ms).',
    'Steps that did not work: pick.',
    'Errors on the page: 0. In the console: 0. Requests that failed: 0.',
  ]);
  // With the page's data requests timed, they are counted and the slowest answer named.
  const requests = [
    asked('whole-galaxy', 1, '/api/map/heatmap?voxel_size=590', 'answered', 0.8),
    asked('pick', 5, '/api/local/search?near=10477373803', 'answered', 4.26, { method: 'POST' }),
    asked('pick', 5.1, '/api/map/systems?min_x=0', 'dropped', 0.2),
  ];
  assert.deepEqual(summaryLines({ ...REPORT, graphics: { ...REPORT.graphics, mapDrawnWith: '' }, steps: [steady], requests }).slice(-2), [
    'The page\'s data requests: 3 asked, 2 answered, 1 dropped by the page, 0 failed.',
    'Slowest answer: 4.3 s, for POST /api/local/search, asked in pick.',
  ]);
  // A tour that never ran (the map was not ready) still gets the error counts.
  assert.deepEqual(summaryLines({ ...REPORT, graphics: { ...REPORT.graphics, mapDrawnWith: '' }, steps: [] }), [
    'Errors on the page: 0. In the console: 0. Requests that failed: 0.',
  ]);
});

test('a recording\'s folder is named by when it was made and what it was called', () => {
  assert.equal(folderName(new Date(2026, 10, 12, 9, 5, 7), 'almanac'), '2026-11-12_090507-almanac');
});
