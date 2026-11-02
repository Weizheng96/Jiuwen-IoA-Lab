/** Awaited CUA CDP capture windows. No browser startup, UI actions or background work. */
const handles = new WeakSet();
const ownedStarts = new WeakSet();
const activeTabs = new Map();
const MAX_WINDOW_MS = 25_000;
const defaultClock = { now: () => Date.now(), sleep: ms => new Promise(resolve => setTimeout(resolve, ms)) };

export class CaptureError extends Error {
  constructor(code) { super(`Capture rejected: ${code}`); this.name = 'CaptureError'; this.code = code; }
}

function requireHandle(handle) {
  if (!handles.has(handle)) throw new CaptureError('unknown_handle');
}

function errorRecord(handle, stage, error) {
  // Do not retain arbitrary exception bodies, CDP params, base64 or page content.
  const value = { at: new Date(handle.clock.now()).toISOString(), stage,
    code: error instanceof CaptureError ? error.code : 'operation_failed',
    type: ['Error', 'TypeError', 'RangeError'].includes(error?.name) ? error.name : 'CaptureError' };
  handle.errors.push(value);
  return value;
}

export function captureSummary(handle) {
  requireHandle(handle);
  return { folder: handle.folder, tabId: handle.tabId, status: handle.status,
    cursor: handle.cursor, frames: handle.frames, firstFrame: handle.firstFrame,
    stopped: handle.stopped, limitReached: handle.limitReached,
    truncatedReads: handle.truncatedReads.length, errors: [...handle.errors] };
}

async function save(handle) {
  const value = { ...captureSummary(handle), startedAt: handle.startedAt,
    finishedAt: handle.finishedAt ?? null, startSucceeded: handle.startSucceeded,
    parameters: handle.parameters, limits: handle.limits, windows: handle.windows,
    truncatedReads: handle.truncatedReads, baseline: handle.baseline ?? null,
    pendingFrame: handle.pendingFrame ?? null, lastNativeTimestamp: handle.lastNativeTimestamp ?? null,
    timingNotes: 'Native timestamps are unchanged. Bounded drains and frame gaps do not prove continuous complete screen recording.' };
  const body = JSON.stringify(value, null, 2) + '\n';
  if (!handle.metadataCreated) {
    await handle.fs.writeFile(handle.folder + '/capture.json', body, { flag: 'wx' });
    handle.metadataCreated = true;
  } else {
    const temporary = handle.folder + '/capture.json.next';
    await handle.fs.writeFile(temporary, body, { flag: 'wx' });
    await handle.fs.rename(temporary, handle.folder + '/capture.json');
  }
}

async function saveFailure(handle) {
  try { await save(handle); }
  catch (error) { errorRecord(handle, 'metadata_write', error); }
}

function jpegDimensions(bytes) {
  if (bytes[0] !== 0xff || bytes[1] !== 0xd8) throw new CaptureError('invalid_jpeg');
  let offset = 2;
  while (offset + 4 <= bytes.length) {
    if (bytes[offset++] !== 0xff) throw new CaptureError('invalid_jpeg');
    while (bytes[offset] === 0xff) offset++;
    const marker = bytes[offset++];
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
    const length = bytes.readUInt16BE(offset);
    if (length < 2 || offset + length > bytes.length) throw new CaptureError('invalid_jpeg');
    if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
      if (length < 8) throw new CaptureError('invalid_jpeg');
      const height = bytes.readUInt16BE(offset + 3), width = bytes.readUInt16BE(offset + 5);
      if (!width || !height) throw new CaptureError('invalid_jpeg');
      return { width, height };
    }
    offset += length;
  }
  throw new CaptureError('jpeg_dimensions_missing');
}

function validateRead(value, before) {
  if (!value || !Number.isSafeInteger(value.cursor) || value.cursor < before || !Array.isArray(value.events)
      || typeof value.truncated !== 'boolean' || typeof value.hasMore !== 'boolean') {
    throw new CaptureError('invalid_event_response');
  }
}

export async function startCapture({ tab, fs, folder, knownIdle = false,
  quality = 70, maxWidth = 1280, maxHeight = 960, everyNthFrame = 1,
  maxFrames = 5000, maxDurationMs = 900_000, clock = defaultClock } = {}) {
  if (!knownIdle) throw new CaptureError('owner_must_confirm_idle_stream');
  if (!tab?.id || !tab.capabilities?.get || !fs || !['mkdir', 'writeFile', 'appendFile', 'rename'].every(key => typeof fs[key] === 'function')
      || typeof folder !== 'string' || !folder.trim()) throw new CaptureError('invalid_dependencies');
  if (![quality, maxWidth, maxHeight, everyNthFrame, maxFrames, maxDurationMs].every(Number.isSafeInteger)
      || quality < 1 || quality > 100 || maxWidth < 1 || maxHeight < 1 || everyNthFrame < 1
      || maxFrames < 1 || maxFrames > 5000 || maxDurationMs < 1 || maxDurationMs > 900_000) {
    throw new CaptureError('invalid_capture_limits');
  }
  const tabId = String(tab.id);
  if (activeTabs.has(tabId)) throw new CaptureError('tab_capture_already_owned');
  const handle = { tabId, fs, folder: folder.replace(/[\\/]+$/, ''), clock, cursor: 0, frames: 0,
    parameters: { format: 'jpeg', quality, maxWidth, maxHeight, everyNthFrame },
    limits: { maxFrames, maxDurationMs }, status: 'preparing', startSucceeded: false,
    stopped: false, busy: false, windows: [], errors: [], truncatedReads: [], firstFrame: null,
    startedAt: new Date(clock.now()).toISOString(), startedMs: clock.now(), limitReached: null };
  handles.add(handle);
  activeTabs.set(tabId, handle);
  let stage = 'folder';
  try {
    // Nonrecursive creation is intentional: caller prepares the parent, and an
    // existing source directory is never inspected, emptied or overwritten.
    await fs.mkdir(handle.folder, { recursive: false });
  } catch (error) {
    activeTabs.delete(tabId);
    throw new CaptureError(error?.code === 'EEXIST' ? 'source_directory_exists' : 'source_directory_unavailable');
  }
  try {
    await fs.writeFile(handle.folder + '/frames.jsonl', '', { flag: 'wx' });
    await save(handle);
    stage = 'capability'; handle.cap = await tab.capabilities.get('cdp');
    stage = 'baseline';
    const baseline = await handle.cap.readEvents({ methods: ['Page.screencastFrame'], limit: 1, timeoutMs: 0 });
    validateRead(baseline, 0);
    handle.cursor = baseline.cursor;
    handle.baseline = { cursor: baseline.cursor, truncated: baseline.truncated,
      priorFrameEvents: baseline.events.filter(event => event.method === 'Page.screencastFrame').length };
    stage = 'start';
    await handle.cap.send('Page.startScreencast', handle.parameters, { timeoutMs: 1000 });
    ownedStarts.add(handle);
    handle.startSucceeded = true; handle.status = 'running';
    handle.startedMs = clock.now(); handle.startedAt = new Date(handle.startedMs).toISOString();
    stage = 'metadata'; await save(handle);
  } catch (error) {
    errorRecord(handle, stage, error);
    handle.status = stage === 'start' ? 'start_unknown' : 'failed';
    await saveFailure(handle);
    if (handle.startSucceeded) await stopCapture(handle);
    else if (stage !== 'start') activeTabs.delete(tabId);
  }
  return handle;
}

export async function captureWindow(handle, milliseconds = 20_000) {
  requireHandle(handle);
  if (!Number.isFinite(milliseconds) || milliseconds <= 0 || milliseconds > MAX_WINDOW_MS) throw new CaptureError('window_must_be_at_most_25000ms');
  if (!ownedStarts.has(handle) || handle.stopped || handle.status !== 'running') throw new CaptureError('capture_not_running');
  if (handle.busy) throw new CaptureError('capture_window_in_progress');
  handle.busy = true;
  const window = { startedAt: new Date(handle.clock.now()).toISOString(), framesBefore: handle.frames,
    cursorBefore: handle.cursor, reads: 0, truncatedReads: 0 };
  handle.windows.push(window);
  const deadline = Math.min(handle.clock.now() + milliseconds, handle.startedMs + handle.limits.maxDurationMs);
  let stage = 'read', endCapture = false;
  try {
    while (handle.clock.now() < deadline && handle.frames < handle.limits.maxFrames) {
      stage = 'read';
      const before = handle.cursor;
      const result = await handle.cap.readEvents({ afterSequence: before, methods: ['Page.screencastFrame'],
        limit: Math.min(100, handle.limits.maxFrames - handle.frames),
        timeoutMs: Math.max(0, Math.min(1000, Math.floor(deadline - handle.clock.now()))) });
      validateRead(result, before); window.reads++;
      if (result.truncated) {
        window.truncatedReads++;
        handle.truncatedReads.push({ at: new Date(handle.clock.now()).toISOString(), afterSequence: before, cursor: result.cursor });
      }
      let partlyProcessed = false;
      for (const event of result.events) {
        if (handle.clock.now() >= deadline) { partlyProcessed = true; break; }
        if (event.method !== 'Page.screencastFrame') continue;
        if (!Number.isSafeInteger(event.sequence) || event.sequence > result.cursor) throw new CaptureError('invalid_frame_sequence');
        if (event.sequence <= before) continue;
        if (event.sequence <= handle.cursor) throw new CaptureError('unordered_frame_sequence');
        const params = event.params;
        if (String(event.source?.tabId) !== handle.tabId) throw new CaptureError('foreign_tab_frame');
        if (!Number.isSafeInteger(params?.sessionId) || params.sessionId < 0) throw new CaptureError('invalid_frame_session');
        let frameError, failedStage;
        try {
          stage = 'frame';
          if (!Number.isFinite(params.metadata?.timestamp)) throw new CaptureError('native_timestamp_missing');
          if (typeof params.data !== 'string' || !params.data.length || params.data.length % 4 !== 0
              || !/^[A-Za-z0-9+/]*={0,2}$/.test(params.data)) throw new CaptureError('invalid_frame_base64');
          const bytes = Buffer.from(params.data, 'base64'), dimensions = jpegDimensions(bytes);
          const file = String(handle.frames).padStart(6, '0') + '.jpg';
          handle.pendingFrame = { file, sequence: event.sequence, sessionId: params.sessionId };
          await handle.fs.writeFile(handle.folder + '/' + file, bytes, { flag: 'wx' });
          await handle.fs.appendFile(handle.folder + '/frames.jsonl', JSON.stringify({ file, sequence: event.sequence, metadata: params.metadata }) + '\n');
          handle.frames++; handle.pendingFrame = null;
          handle.firstFrame ??= { file, sequence: event.sequence, timestamp: params.metadata.timestamp, ...dimensions };
          handle.lastNativeTimestamp = params.metadata.timestamp;
        } catch (error) { frameError = error; failedStage = stage; }
        // Only a verified frame source/session is acknowledged, including when
        // local persistence failed; stop our own stream after preserving error.
        try { stage = 'ack'; await handle.cap.send('Page.screencastFrameAck', { sessionId: params.sessionId }, { timeoutMs: 1000 }); }
        catch (error) { if (!frameError) { frameError = error; failedStage = 'ack'; } }
        if (frameError) { stage = failedStage; throw frameError; }
        handle.cursor = event.sequence;
        if (handle.frames >= handle.limits.maxFrames) break;
      }
      // A read is not an acknowledgement. Leave unprocessed events after the
      // last saved/acked sequence available to the next awaited window.
      if (!partlyProcessed) handle.cursor = result.cursor;
      if (!result.hasMore && handle.clock.now() < deadline) await handle.clock.sleep(Math.min(25, deadline - handle.clock.now()));
    }
    if (handle.frames >= handle.limits.maxFrames) handle.limitReached = 'frames';
    else if (handle.clock.now() >= handle.startedMs + handle.limits.maxDurationMs) handle.limitReached = 'duration';
    endCapture = Boolean(handle.limitReached);
  } catch (error) {
    window.error = errorRecord(handle, stage, error); handle.status = 'failed'; endCapture = true;
  } finally {
    window.finishedAt = new Date(handle.clock.now()).toISOString();
    window.framesAfter = handle.frames; window.cursorAfter = handle.cursor;
    handle.busy = false;
    try { await save(handle); }
    catch (error) { errorRecord(handle, 'metadata_write', error); handle.status = 'failed'; endCapture = true; }
  }
  if (endCapture) await stopCapture(handle);
  return captureSummary(handle);
}

export async function stopCapture(handle) {
  requireHandle(handle);
  if (handle.busy) throw new CaptureError('capture_window_in_progress');
  if (!ownedStarts.has(handle)) return captureSummary(handle);
  try {
    await handle.cap.send('Page.stopScreencast', {}, { timeoutMs: 1000 });
    ownedStarts.delete(handle);
    handle.stopped = true;
    if (handle.status === 'running') handle.status = 'stopped';
    handle.finishedAt = new Date(handle.clock.now()).toISOString();
    if (activeTabs.get(handle.tabId) === handle) activeTabs.delete(handle.tabId);
  } catch (error) {
    errorRecord(handle, 'stop', error); handle.status = 'stop_unknown';
  }
  await saveFailure(handle);
  return captureSummary(handle);
}
