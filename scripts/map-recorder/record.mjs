#!/usr/bin/env node
// Record a tour of the map in a real browser, on this machine's graphics card.
//
//   node record.mjs                      the live site's Explore map, in Chrome
//   node record.mjs --help               every option
//
// It opens the map in a window, flies the steps of a tour file (zoom, pan,
// turn), and saves into one new folder under recordings/:
//
//   index.html    the report: open this one
//   video.webm    the whole tour
//   shots/        a picture after each step
//   report.json   the same numbers, for a program to read
//   timeline.txt  every data request, change on the map and freeze, in order
//
// The plan's rule: Playwright is for map recordings on a real graphics card
// and nothing else. Pass/fail journeys are Cypress's job, in CI.
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import readline from 'node:readline/promises';
import { fileURLToPath } from 'node:url';

import {
  UsageError, describeStep, drawnBySoftware, droppedByThePage, folderName, frameStats, parseArgs, reportHtml,
  summaryLines, tidyAddress, timelineLines, usage, validateTour,
} from './logic.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));

// Runs in the page before anything else: notes the time between frames, and
// each time the picture stood still (a frame over 100 ms) with the clock time
// it began, so that a freeze can be set beside the request that caused it.
function frameSampler() {
  const deltas = [];
  const freezes = [];
  let last = performance.now();
  function tick(now) {
    const delta = now - last;
    deltas.push(delta);
    if (delta > 100 && freezes.length < 5000) freezes.push({ at: performance.timeOrigin + last, ms: delta });
    last = now;
    if (deltas.length > 100000) deltas.splice(0, 50000);
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
  window.__mapRecorderFrames = () => deltas.splice(0);
  window.__mapRecorderFreezes = () => freezes.splice(0);
}

// Runs in the page before anything else: notes each change to the attributes
// the tour's "probe" names, with the clock time. The map says in those
// attributes when its camera is moving and how many stars it is drawing, so
// this is how the recording knows when the stars arrived, not only that they
// had by the time of the picture.
function changeWatcher(probes) {
  const changes = [];
  const names = [...new Set(probes.flatMap((probe) => probe.attributes))];
  const wanted = (element, name) => probes.some((probe) => probe.attributes.includes(name) && element.matches(probe.selector));
  new MutationObserver((records) => {
    const at = performance.timeOrigin + performance.now();
    records.forEach((record, index) => {
      if (record.type !== 'attributes' || !wanted(record.target, record.attributeName)) return;
      // Several changes can arrive together. Each one's new value is the
      // next one's old value; the last one's is what the page says now.
      const next = records.slice(index + 1).find((later) => later.target === record.target && later.attributeName === record.attributeName);
      const value = next ? next.oldValue : record.target.getAttribute(record.attributeName);
      if (value !== record.oldValue && changes.length < 20000) changes.push({ at, name: record.attributeName, value, was: record.oldValue });
    });
  }).observe(document, { subtree: true, attributes: true, attributeOldValue: true, attributeFilter: names });
  window.__mapRecorderChanges = () => changes.splice(0);
}

// Runs in the page: which graphics card (or software) draws WebGL here.
function graphicsInfo() {
  const canvas = document.createElement('canvas');
  const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
  if (!gl) return { renderer: '', vendor: '', webgl: 'none', userAgent: navigator.userAgent };
  const names = gl.getExtension('WEBGL_debug_renderer_info');
  return {
    renderer: String(names ? gl.getParameter(names.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)),
    vendor: String(names ? gl.getParameter(names.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR)),
    webgl: gl.getParameter(gl.VERSION),
    userAgent: navigator.userAgent,
  };
}

const sleep = (seconds) => new Promise((resolve) => { setTimeout(resolve, seconds * 1000); });

async function act(page, step, size) {
  const point = ([across, down]) => [Math.round(across * size.width), Math.round(down * size.height)];
  switch (step.do) {
    case 'wait':
      await sleep(step.seconds);
      break;
    case 'waitFor':
      await page.locator(step.selector).first().waitFor({ state: 'visible', timeout: (step.timeoutSeconds ?? 30) * 1000 });
      break;
    case 'shot':
      break;
    case 'focus':
      await page.locator(step.selector).first().focus({ timeout: 10000 });
      break;
    case 'wheel': {
      await page.mouse.move(...point(step.at));
      for (let tick = 0; tick < step.ticks; tick += 1) {
        await page.mouse.wheel(0, step.deltaY);
        await sleep(step.seconds / step.ticks);
      }
      break;
    }
    case 'drag': {
      const [fromX, fromY] = point(step.from);
      const [toX, toY] = point(step.to);
      const moves = Math.max(2, Math.round(step.seconds * 30));
      await page.mouse.move(fromX, fromY);
      await page.mouse.down({ button: step.button });
      for (let move = 1; move <= moves; move += 1) {
        await page.mouse.move(fromX + ((toX - fromX) * move) / moves, fromY + ((toY - fromY) * move) / moves);
        await sleep(step.seconds / moves);
      }
      await page.mouse.up({ button: step.button });
      break;
    }
    case 'keys':
      for (let press = 0; press < step.times; press += 1) {
        await page.keyboard.press(step.key);
        await sleep(step.seconds / step.times);
      }
      break;
    case 'click':
      if (step.selector) await page.locator(step.selector).first().click({ timeout: 10000 });
      else await page.mouse.click(...point(step.at));
      break;
    case 'type': {
      // Key by key, as a person types: a search box that suggests as you
      // type then behaves as it does for a person, and the video shows it.
      const box = page.locator(step.selector).first();
      await box.click({ timeout: 10000 });
      await box.fill('');
      await box.pressSequentially(step.text, { delay: 90 });
      if (step.enter) await page.keyboard.press('Enter');
      break;
    }
    case 'move':
      await page.mouse.move(...point(step.at));
      break;
    default:
      throw new Error(`unknown step: ${step.do}`);
  }
}

async function readProbe(page, probes) {
  if (!probes) return null;
  return page.evaluate((list) => {
    const values = {};
    for (const { selector, attributes } of list) {
      const element = document.querySelector(selector);
      for (const name of attributes) values[name] = element ? element.getAttribute(name) : null;
    }
    return values;
  }, probes).catch(() => null);
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    if (!(error instanceof UsageError)) throw error;
    console.error(`${error.message}\n\n${usage()}`);
    return 2;
  }
  if (options.help) {
    console.log(usage());
    return 0;
  }
  const tourFile = path.resolve(HERE, options.tour);
  let tour;
  try {
    tour = validateTour(JSON.parse(await readFile(tourFile, 'utf8')));
  } catch (error) {
    console.error(`The tour ${tourFile} cannot be used: ${error.message}`);
    return 2;
  }

  let playwright;
  try {
    playwright = await import('playwright');
  } catch {
    console.error('Playwright is not installed here yet. In this folder, run:  npm install');
    return 2;
  }

  const when = new Date();
  const folder = path.resolve(HERE, options.out, folderName(when, options.name));
  await mkdir(path.join(folder, 'shots'), { recursive: true });
  const size = { width: options.width, height: options.height };
  const kind = options.browser === 'firefox' ? playwright.firefox : playwright.chromium;
  // Its own browser profile, kept between runs, so that a sign-in is done once.
  const profile = path.resolve(HERE, '.profile', options.browser);

  console.log(`Opening ${options.browser} at ${size.width} x ${size.height} ...`);
  let context;
  try {
    context = await kind.launchPersistentContext(profile, {
      ...(options.browser === 'firefox' ? {} : { channel: options.browser }),
      ...(options.exe ? { executablePath: options.exe, channel: undefined } : {}),
      headless: options.headless,
      viewport: size,
      deviceScaleFactor: options.scale,
      recordVideo: { dir: folder, size },
      // Use the graphics card even if the browser's own list would not.
      args: options.browser === 'firefox' ? [] : ['--ignore-gpu-blocklist', '--enable-gpu-rasterization'],
    });
  } catch (error) {
    const reason = String(error.message).split('\n')[0];
    console.error(`Could not open ${options.browser}: ${reason}`);
    if (/ffmpeg/i.test(reason)) console.error('The small program that saves the video is missing. In this folder, run:  npx playwright install ffmpeg');
    else if (options.browser === 'firefox') console.error('Firefox needs one download first. In this folder, run:  npx playwright install firefox');
    else console.error(`Is ${options.browser === 'msedge' ? 'Microsoft Edge' : 'Google Chrome'} installed? Try --browser msedge or --browser chrome.`);
    return 1;
  }

  const report = {
    name: options.name,
    when: when.toString(),
    page: options.url + tour.path,
    tour: path.basename(tourFile),
    about: tour.about,
    browser: options.browser,
    // Filled in from the page: a browser opened with its own profile does
    // not give its version any other way.
    browserVersion: '',
    headless: options.headless,
    width: size.width,
    height: size.height,
    scale: options.scale,
    // "renderer" is the graphics card, asked of the browser. "mapDrawnWith"
    // is the drawing method the map itself chose, read from the page.
    graphics: { renderer: '', vendor: '', webgl: '', mapDrawnWith: '' },
    ready: { ok: true, selector: tour.ready?.selector ?? '', waitedSeconds: 0 },
    steps: [],
    pageErrors: [],
    consoleErrors: [],
    failedRequests: [],
    // Requests the page stopped itself before an answer came: not failures.
    droppedRequests: [],
    // What happened when, in seconds since the page was opened: the page's
    // own data requests, each change the map reported about itself, and
    // each time the picture stood still.
    requests: [],
    changes: [],
    freezes: [],
    video: null,
  };
  // Which step was running when something went wrong.
  let during = 'opening the page';
  // The clock all the times above are read from.
  const opened = Date.now();
  const since = (clock = Date.now()) => Math.round((clock - opened) / 10) / 100;
  const origin = new URL(report.page).origin;
  const tidy = (address) => tidyAddress(address, origin);

  await context.addInitScript(frameSampler);
  if (tour.probe) await context.addInitScript(changeWatcher, tour.probe);
  const page = context.pages()[0] ?? await context.newPage();
  // Move what the page noted (changes, freezes) into the report.
  const collect = async () => {
    const noted = await page.evaluate(() => ({
      changes: window.__mapRecorderChanges?.() ?? [], freezes: window.__mapRecorderFreezes?.() ?? [],
    })).catch(() => ({ changes: [], freezes: [] }));
    for (const change of noted.changes) report.changes.push({ atSeconds: since(change.at), name: change.name, value: change.value, was: change.was });
    for (const freeze of noted.freezes) report.freezes.push({ atSeconds: since(freeze.at), ms: Math.round(freeze.ms * 10) / 10 });
  };
  // Time the page's own data requests: when each was asked, and how it ended.
  const timed = new Map();
  const finishing = [];
  page.on('request', (request) => {
    if (!tour.requests) return;
    let address;
    try {
      address = new URL(request.url());
    } catch {
      return;
    }
    if (address.origin !== origin || !address.pathname.startsWith(tour.requests.under)) return;
    const entry = {
      step: during, atSeconds: since(), method: request.method(), address: tidy(request.url()),
      outcome: 'waiting', status: null, why: null, tookSeconds: null, kilobytes: null,
    };
    timed.set(request, entry);
    report.requests.push(entry);
  });
  page.on('requestfinished', (request) => {
    const entry = timed.get(request);
    if (!entry) return;
    // The browser's own clock for this request, where it gives one: when it
    // went out and when the last of the answer came in. The moment this
    // recorder hears about it can be late if the machine is busy.
    const timing = request.timing();
    if (timing.startTime > 0) entry.atSeconds = since(timing.startTime);
    entry.tookSeconds = timing.responseEnd > 0
      ? Math.round(timing.responseEnd / 10) / 100
      : Math.round((since() - entry.atSeconds) * 100) / 100;
    entry.outcome = 'answered';
    finishing.push((async () => {
      entry.status = (await request.response())?.status() ?? null;
      entry.kilobytes = Math.round((await request.sizes()).responseBodySize / 102.4) / 10;
    })().catch(() => {}));
  });
  page.on('pageerror', (error) => report.pageErrors.push(String(error.message).slice(0, 500)));
  page.on('console', (message) => {
    if (message.type() === 'error') report.consoleErrors.push(message.text().slice(0, 500));
  });
  page.on('requestfailed', (request) => {
    const why = request.failure()?.errorText ?? 'failed';
    const line = `${during}: ${request.method()} ${tidy(request.url())} (${why})`;
    (droppedByThePage(why) ? report.droppedRequests : report.failedRequests).push(line);
    const entry = timed.get(request);
    if (!entry) return;
    entry.tookSeconds = Math.round((since() - entry.atSeconds) * 100) / 100;
    entry.outcome = droppedByThePage(why) ? 'dropped' : 'failed';
    entry.why = why;
  });
  page.on('response', (response) => {
    if (response.status() >= 400) report.failedRequests.push(`${during}: ${response.request().method()} ${tidy(response.url())} (answered ${response.status()})`);
  });

  let exitCode = 0;
  try {
    console.log(`Opening ${report.page} ...`);
    await page.goto(report.page, { waitUntil: 'domcontentloaded', timeout: 60000 });
    if (options.pause) {
      const ask = readline.createInterface({ input: process.stdin, output: process.stdout });
      await ask.question('The page is open. Sign in or set it up as you want it, then press Enter here to start the tour. ');
      ask.close();
    }
    if (tour.ready) {
      const began = Date.now();
      console.log('Waiting for the map to say it is ready ...');
      try {
        await page.locator(tour.ready.selector).first().waitFor({ state: 'attached', timeout: tour.ready.timeoutSeconds * 1000 });
      } catch {
        report.ready.ok = false;
      }
      report.ready.waitedSeconds = Math.round((Date.now() - began) / 1000);
    }
    const { userAgent, ...graphics } = await page.evaluate(graphicsInfo).catch(() => ({ ...report.graphics, userAgent: '' }));
    report.graphics = { ...report.graphics, ...graphics };
    report.browserVersion = userAgent;
    console.log(`Graphics: ${report.graphics.renderer || 'not reported'}`);
    if (report.ready.ok && tour.ready?.drawnWith) {
      report.graphics.mapDrawnWith = await page.locator(tour.ready.selector).first()
        .getAttribute(tour.ready.drawnWith, { timeout: 5000 }).catch(() => null) ?? '';
      console.log(`The map drew with: ${report.graphics.mapDrawnWith || 'not reported'}`);
    }

    if (!report.ready.ok) {
      console.log('The map never said it was ready. Taking one picture and stopping.');
      await page.screenshot({ path: path.join(folder, 'shots', '00-not-ready.png') });
      report.steps.push({ name: 'not-ready', did: 'the map was not ready', startedSeconds: since(), failed: null, picture: 'shots/00-not-ready.png', stats: frameStats([]), probe: null });
      exitCode = 1;
    } else {
      for (const [index, step] of tour.steps.entries()) {
        const number = String(index + 1).padStart(2, '0');
        const did = describeStep(step);
        console.log(`  ${number} ${step.name}: ${did}`);
        during = step.name;
        const startedSeconds = since();
        await page.evaluate(() => window.__mapRecorderFrames?.());
        // A step that does not work is noted, and the tour carries on: the
        // steps after it may still be worth having.
        let failed = null;
        try {
          await act(page, step, size);
        } catch (error) {
          if (page.isClosed()) throw error;
          failed = String(error.message).split('\n')[0].slice(0, 300);
          console.log(`     did not work: ${failed}`);
          exitCode = 1;
        }
        await sleep(step.settleSeconds);
        const stats = frameStats(await page.evaluate(() => window.__mapRecorderFrames?.() ?? []));
        let picture = null;
        if (step.shot) {
          picture = `shots/${number}-${step.name}.png`;
          await page.screenshot({ path: path.join(folder, picture) });
        }
        report.steps.push({ name: step.name, did, startedSeconds, failed, picture, stats, probe: await readProbe(page, tour.probe) });
        await collect();
      }
    }
  } catch (error) {
    console.error(`Stopped: ${String(error.message).split('\n')[0]}`);
    report.pageErrors.push(`The recorder stopped: ${String(error.message).slice(0, 500)}`);
    exitCode = 1;
  }

  await collect();
  await Promise.allSettled(finishing);
  const video = page.video();
  await context.close();
  if (video) {
    try {
      await rename(await video.path(), path.join(folder, 'video.webm'));
      report.video = 'video.webm';
    } catch {
      report.video = null;
    }
  }
  await writeFile(path.join(folder, 'report.json'), `${JSON.stringify(report, null, 1)}\n`);
  await writeFile(path.join(folder, 'index.html'), reportHtml(report));
  await writeFile(path.join(folder, 'timeline.txt'), `${timelineLines(report).join('\n')}\n`);

  if (!options.headless && drawnBySoftware(report.graphics.renderer)) {
    console.log('WARNING: the browser drew the map in software, not on the graphics card.');
  }
  for (const line of summaryLines(report)) console.log(line);
  console.log(`Saved to: ${folder}`);
  console.log(`Open this to see it: ${path.join(folder, 'index.html')}`);
  return exitCode;
}

process.exitCode = await main();
