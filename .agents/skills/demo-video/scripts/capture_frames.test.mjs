import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import { startCapture, captureWindow, stopCapture, captureSummary } from './capture_frames.mjs';

// Explicit tiny SOF fixture, not a captured browser image or decoding proof.
const jpeg = Buffer.from('ffd8ffc0000b080001000101011100ffd9', 'hex');
const root = path.resolve(process.env.CAPTURE_TEST_ROOT ?? 'temp/checks/capture-frames-test-work');
await fs.mkdir(root, { recursive: true });
let nextId = 0;

async function fixture(options = {}) {
  const parent = await fs.mkdtemp(path.join(root, 'case-'));
  const folder = path.join(parent, 'frames');
  const tabId = 'fixture-' + ++nextId;
  let now = 0;
  const clock = { now: () => now, sleep: async ms => { now += ms; } };
  const calls = [];
  const batches = [...(options.batches ?? [])];
  const frame = (sequence, timestamp = 1793636000 + sequence / 1000) => ({
    method: 'Page.screencastFrame', sequence, source: { tabId },
    params: { data: jpeg.toString('base64'), sessionId: sequence * 10,
      metadata: { timestamp, deviceWidth: 1028, deviceHeight: 861.33, monotonicTimestamp: sequence / 1000 } } });
  const cap = {
    async send(method, params, limits) {
      calls.push({ method, params, limits });
      if (options.failStart && method === 'Page.startScreencast') throw new Error('private start payload must not leak');
      if (options.failAck && method === 'Page.screencastFrameAck') throw new Error('private ack payload must not leak');
      if (options.failStop && method === 'Page.stopScreencast') throw new Error('private stop payload must not leak');
      if (method === 'Page.screencastFrameAck') now += options.ackMs ?? 0;
    },
    async readEvents(args) {
      calls.push({ method: 'readEvents', args });
      if (args.afterSequence === undefined) return { cursor: 10, events: [], hasMore: false, truncated: false };
      now += 1;
      if (options.read) return options.read(args, { frame, advance: ms => { now += ms; } });
      const batch = batches.shift();
      if (batch instanceof Error) throw batch;
      return batch ? { hasMore: false, truncated: false, ...batch } :
        { cursor: args.afterSequence, events: [], hasMore: false, truncated: false };
    },
  };
  const tab = { id: tabId, capabilities: { async get(name) { assert.equal(name, 'cdp'); return cap; } } };
  const start = extra => startCapture({ tab, fs, folder, knownIdle: true, clock, ...extra });
  return { folder, tab, frame, cap, clock, calls, batches, start, advance: ms => { now += ms; }, options };
}

async function metadata(value) { return JSON.parse(await fs.readFile(path.join(value.folder, 'capture.json'), 'utf8')); }
async function records(value) {
  return (await fs.readFile(path.join(value.folder, 'frames.jsonl'), 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);
}
function sent(value, method) { return value.calls.filter(call => call.method === method); }

test('first Visibility is not a frame; later native frame is saved unchanged and acked once', async () => {
  const f = await fixture();
  f.batches.push({ cursor: 11, events: [{ method: 'Page.screencastVisibilityChanged', sequence: 11 }], hasMore: true });
  const original = f.frame(12); f.batches.push({ cursor: 12, events: [original] });
  const h = await f.start();
  assert.equal(captureSummary(h).firstFrame, null);
  const result = await captureWindow(h, 40);
  assert.equal(result.frames, 1);
  assert.deepEqual(result.firstFrame, { file: '000000.jpg', sequence: 12, timestamp: original.params.metadata.timestamp, width: 1, height: 1 });
  assert.deepEqual(await fs.readFile(path.join(f.folder, '000000.jpg')), jpeg);
  assert.deepEqual(await records(f), [{ file: '000000.jpg', sequence: 12, metadata: original.params.metadata }]);
  assert.deepEqual(sent(f, 'Page.screencastFrameAck').map(call => call.params), [{ sessionId: 120 }]);
  assert.ok(f.calls.filter(call => call.method === 'readEvents').every(call => call.args.methods[0] === 'Page.screencastFrame'));
  await stopCapture(h); await stopCapture(h);
  h.stopped = false; // Public diagnostics cannot resurrect a stopped ownership proof.
  await stopCapture(h);
  assert.equal(sent(f, 'Page.stopScreencast').length, 1);
});

test('empty bounded window reports no first frame rather than inventing one', async () => {
  const f = await fixture(); const h = await f.start();
  const result = await captureWindow(h, 20);
  assert.equal(result.firstFrame, null); assert.equal(result.frames, 0);
  assert.equal((await metadata(f)).windows.length, 1);
  await stopCapture(h);
});

test('two explicit handles retain their own cursor and stopped metadata cannot receive another window', async () => {
  const wang = await fixture(), zhao = await fixture();
  wang.batches.push({ cursor: 11, events: [wang.frame(11)] });
  zhao.batches.push({ cursor: 13, events: [zhao.frame(13)] });
  const w = await wang.start(), z = await zhao.start();
  await captureWindow(w, 20); await stopCapture(w);
  const frozen = await fs.readFile(path.join(wang.folder, 'capture.json'));
  await captureWindow(z, 20);
  await assert.rejects(captureWindow(w, 20), { code: 'capture_not_running' });
  assert.deepEqual(await fs.readFile(path.join(wang.folder, 'capture.json')), frozen);
  assert.deepEqual((await records(wang)).map(row => row.sequence), [11]);
  assert.deepEqual((await records(zhao)).map(row => row.sequence), [13]);
  await stopCapture(z);
});

test('existing source and duplicate/unknown tab ownership are refused without stopping any stream', async () => {
  const f = await fixture(); await fs.mkdir(f.folder); await fs.writeFile(path.join(f.folder, 'prior.jpg'), jpeg);
  await assert.rejects(f.start(), { code: 'source_directory_exists' });
  assert.equal(sent(f, 'Page.startScreencast').length, 0);
  assert.deepEqual(await fs.readFile(path.join(f.folder, 'prior.jpg')), jpeg);
  const g = await fixture();
  await assert.rejects(g.start({ knownIdle: false }), { code: 'owner_must_confirm_idle_stream' });
  const h = await g.start();
  await assert.rejects(g.start({ folder: g.folder + '-other' }), { code: 'tab_capture_already_owned' });
  await assert.rejects(stopCapture({ cap: g.cap }), { code: 'unknown_handle' });
  assert.equal(sent(g, 'Page.stopScreencast').length, 0);
  await stopCapture(h);
});

test('unknown start is retained and never blindly stopped or overwritten', async () => {
  const f = await fixture({ failStart: true }); const h = await f.start();
  assert.equal(h.status, 'start_unknown'); assert.equal(h.startSucceeded, false);
  h.startSucceeded = true; // A mutable diagnostic field is not proof of owned start.
  await stopCapture(h);
  assert.equal(sent(f, 'Page.stopScreencast').length, 0);
  await assert.rejects(captureWindow(h, 10), { code: 'capture_not_running' });
  await assert.rejects(f.start({ folder: f.folder + '-again' }), { code: 'tab_capture_already_owned' });
  assert.ok(!(await fs.readFile(path.join(f.folder, 'capture.json'), 'utf8')).includes('private start payload'));
});

test('truncation is recorded explicitly; native timestamps are neither replaced nor sorted', async () => {
  const f = await fixture();
  f.batches.push({ cursor: 12, events: [f.frame(11, 200), f.frame(12, 100)], truncated: true });
  const h = await f.start(); await captureWindow(h, 20); await stopCapture(h);
  assert.deepEqual((await records(f)).map(row => row.metadata.timestamp), [200, 100]);
  assert.equal((await metadata(f)).truncatedReads.length, 1);
  assert.equal((await metadata(f)).windows[0].truncatedReads, 1);
});

test('pending read events survive a short window budget and are not skipped by its cursor', async () => {
  const f = await fixture({ ackMs: 10, read: (args, { frame }) => ({ cursor: 13,
    events: [11, 12, 13].filter(sequence => sequence > args.afterSequence).map(sequence => frame(sequence)),
    hasMore: false, truncated: false }) });
  const h = await f.start(); await captureWindow(h, 5);
  assert.equal(h.frames, 1); assert.equal(h.cursor, 11);
  await captureWindow(h, 40); await stopCapture(h);
  assert.deepEqual((await records(f)).map(row => row.sequence), [11, 12, 13]);
  assert.equal(sent(f, 'Page.screencastFrameAck').length, 3);
});

test('read failure preserves error and stops only the stream this handle started', async () => {
  const f = await fixture({ batches: [new Error('private page body must not leak')] });
  const h = await f.start(); const result = await captureWindow(h, 10);
  assert.equal(result.status, 'failed'); assert.equal(result.stopped, true);
  assert.equal(sent(f, 'Page.stopScreencast').length, 1);
  const text = await fs.readFile(path.join(f.folder, 'capture.json'), 'utf8');
  assert.ok(!text.includes('private page body')); assert.ok(!text.includes(jpeg.toString('base64')));
});

test('a failed frame append preserves the raw orphan, pending record and actual frame ack', async () => {
  const f = await fixture(); f.batches.push({ cursor: 11, events: [f.frame(11)] });
  const brokenFs = { ...fs, async appendFile() { throw new Error('private persistence text'); } };
  const h = await f.start({ fs: brokenFs }); const result = await captureWindow(h, 20);
  assert.equal(result.frames, 0); assert.equal(result.status, 'failed'); assert.equal(result.stopped, true);
  assert.deepEqual(await fs.readFile(path.join(f.folder, '000000.jpg')), jpeg);
  assert.equal((await metadata(f)).pendingFrame.file, '000000.jpg');
  assert.equal((await metadata(f)).errors[0].stage, 'frame');
  assert.equal(sent(f, 'Page.screencastFrameAck').length, 1);
});

test('invalid native timestamp/JPEG fail without fabricating a frame and still ack own session', async () => {
  for (const bad of ['timestamp', 'jpeg']) {
    const f = await fixture(), event = f.frame(11);
    if (bad === 'timestamp') delete event.params.metadata.timestamp;
    else event.params.data = Buffer.from('not a jpeg').toString('base64');
    f.batches.push({ cursor: 11, events: [event] });
    const h = await f.start(); await captureWindow(h, 10);
    assert.equal(h.frames, 0); assert.equal(h.status, 'failed'); assert.equal(h.stopped, true);
    assert.equal(sent(f, 'Page.screencastFrameAck').length, 1);
  }
});

test('foreign tab frame is neither saved nor acknowledged', async () => {
  const f = await fixture(), event = f.frame(11); event.source.tabId = 'another-owner';
  f.batches.push({ cursor: 11, events: [event] });
  const h = await f.start(); await captureWindow(h, 10);
  assert.equal(h.frames, 0); assert.equal(h.errors[0].code, 'foreign_tab_frame');
  assert.equal(sent(f, 'Page.screencastFrameAck').length, 0);
  assert.equal(sent(f, 'Page.stopScreencast').length, 1);
});

test('ack failure is explicit and owned stop failure can be retried without a new start', async () => {
  const f = await fixture({ failAck: true, failStop: true }); f.batches.push({ cursor: 11, events: [f.frame(11)] });
  const h = await f.start(); await captureWindow(h, 20);
  assert.equal(h.frames, 1); assert.equal(h.status, 'stop_unknown'); assert.equal(h.stopped, false);
  assert.ok(h.errors.some(error => error.stage === 'ack'));
  assert.ok(h.errors.some(error => error.stage === 'stop'));
  f.options.failStop = false; await stopCapture(h);
  assert.equal(h.stopped, true); assert.equal(sent(f, 'Page.startScreencast').length, 1);
});

test('frame/duration ceilings stop owned streams; excessive requested limits/windows are rejected', async () => {
  const f = await fixture(); f.batches.push({ cursor: 11, events: [f.frame(11)] });
  const h = await f.start({ maxFrames: 1 }); await captureWindow(h, 20);
  assert.equal(h.limitReached, 'frames'); assert.equal(h.stopped, true);
  const g = await fixture(), time = await g.start({ maxDurationMs: 10 });
  await assert.rejects(captureWindow(time, 25001), { code: 'window_must_be_at_most_25000ms' });
  await captureWindow(time, 20); assert.equal(time.limitReached, 'duration'); assert.equal(time.stopped, true);
  const other = await fixture();
  await assert.rejects(other.start({ maxFrames: 5001 }), { code: 'invalid_capture_limits' });
  await assert.rejects(other.start({ maxDurationMs: 900001 }), { code: 'invalid_capture_limits' });
  assert.equal(other.calls.length, 0);
});

test('concurrent window and stop are rejected while the original awaited read owns the handle', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const f = await fixture({ read: async (args, { frame }) => {
    await gate; return { cursor: 11, events: args.afterSequence < 11 ? [frame(11)] : [], hasMore: false, truncated: false };
  } });
  const h = await f.start(), pending = captureWindow(h, 20);
  await assert.rejects(captureWindow(h, 20), { code: 'capture_window_in_progress' });
  await assert.rejects(stopCapture(h), { code: 'capture_window_in_progress' });
  release(); await pending; await stopCapture(h);
  assert.equal(h.frames, 1); assert.equal(sent(f, 'Page.stopScreencast').length, 1);
});
