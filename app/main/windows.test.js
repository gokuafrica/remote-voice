'use strict';

const assert = require('assert');
const { EventEmitter } = require('events');
const Module = require('module');

// Virtual time makes missed readiness/acknowledgement races deterministic.
const timers = new Map();
let clock = 0;
const realSetTimeout = global.setTimeout;
const realClearTimeout = global.clearTimeout;
global.setTimeout = (fn, delay) => {
  const timer = { at: clock + delay, fn, unref() {} };
  timers.set(timer, timer);
  return timer;
};
global.clearTimeout = (timer) => timers.delete(timer);
function advance(ms) {
  const end = clock + ms;
  for (;;) {
    const next = [...timers.values()].sort((a, b) => a.at - b.at)[0];
    if (!next || next.at > end) break;
    clock = next.at;
    timers.delete(next);
    next.fn();
  }
  clock = end;
}
function ready(win) { ipcMain.emit('overlay:ready', { sender: win.webContents }); }
function applied(win) {
  ipcMain.emit('overlay:applied', { sender: win.webContents }, win.webContents.sent.at(-1).payload.sequence);
}
function payload(win) {
  const { sequence, ...state } = win.webContents.sent.at(-1).payload;
  assert(Number.isInteger(sequence));
  return state;
}

class FakeWebContents extends EventEmitter {
  constructor() {
    super();
    this.sent = [];
  }

  send(channel, payload) {
    this.sent.push({ channel, payload });
  }
}

class FakeBrowserWindow extends EventEmitter {
  static instances = [];

  constructor(options) {
    super();
    this.options = options;
    this.webContents = new FakeWebContents();
    this.visible = Boolean(options.show);
    this.destroyed = false;
    this.position = null;
    FakeBrowserWindow.instances.push(this);
  }

  setAlwaysOnTop() {}

  loadFile(file) {
    this.loadedFile = file;
    return Promise.resolve();
  }

  showInactive() {
    this.visible = true;
  }

  hide() {
    this.visible = false;
  }

  isVisible() {
    return this.visible;
  }

  isDestroyed() {
    return this.destroyed;
  }

  setPosition(x, y) {
    this.position = { x, y };
  }

  destroy() {
    this.destroyed = true;
    this.emit('closed');
  }
}

const ipcMain = new EventEmitter();
const originalLoad = Module._load;
Module._load = function load(request, parent, isMain) {
  if (request === 'electron') {
    return {
      BrowserWindow: FakeBrowserWindow,
      screen: {
        getCursorScreenPoint: () => ({ x: 500, y: 400 }),
        getDisplayNearestPoint: () => ({
          bounds: { x: 0, y: 0, width: 1920, height: 1080 },
          workArea: { x: 0, y: 0, width: 1920, height: 1040 },
        }),
      },
      ipcMain,
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const windows = require('./windows');
Module._load = originalLoad;

const config = { get: () => ({ overlay_position: 'bottom' }) };
windows.init({ config, tray: {}, onCancel() {} });

// A listener handshake alone must never expose a blank native window.
windows.showOverlay();
const first = windows.windows.overlay;
assert(first);
assert.strictEqual(first.visible, false);
ready(first);
assert.strictEqual(first.visible, false);
assert.deepStrictEqual(payload(first), { state: 'recording', level: 0 });
applied(first);
assert.strictEqual(first.visible, true);

// Replay the latest state accumulated during load, then permit visibility.
windows.showOverlay();
const second = windows.windows.overlay;
assert(first.destroyed, 'each recording owns a fresh transparent surface');
windows.sendOverlayState('recording', 0.75);
assert.deepStrictEqual(second.webContents.sent, []);
ready(second);
assert.deepStrictEqual(payload(second), { state: 'recording', level: 0.75 });
applied(second);
assert(second.visible);

// Stopping during load disposes the surface; stale handshakes cannot revive it.
windows.showOverlay();
const stopped = windows.windows.overlay;
windows.hideOverlay();
ready(stopped);
assert(stopped.destroyed);
assert.strictEqual(windows.windows.overlay, null);
advance(10000);
assert.strictEqual(windows.windows.overlay, null);

// Missing readiness used to leave every subsequent recording blank forever.
windows.showOverlay();
const noReady = windows.windows.overlay;
advance(3100);
assert(noReady.destroyed);
const recovered = windows.windows.overlay;
assert(recovered && recovered !== noReady);
ready(recovered);
applied(recovered);
assert(recovered.visible);

// A renderer can be ready but fail to consume updates. Frequent audio levels
// must not postpone the watchdog indefinitely, nor must old IPC acknowledge it.
windows.sendOverlayState('recording', 0.42);
const pendingSequence = recovered.webContents.sent.at(-1).payload.sequence;
for (let i = 0; i < 30; i++) {
  windows.sendOverlayState('recording', 0.5);
  assert.strictEqual(recovered.webContents.sent.at(-1).payload.sequence, pendingSequence);
  ipcMain.emit('overlay:applied', { sender: first.webContents }, pendingSequence);
  advance(100);
}
assert(recovered.destroyed);
advance(100);
const responsive = windows.windows.overlay;
ready(responsive);
applied(responsive);
assert(responsive.visible);

// Renderer loss is repaired during this recording, with no new hotkey needed.
windows.showOverlay();
const crashed = windows.windows.overlay;
ready(crashed);
applied(crashed);
crashed.webContents.emit('render-process-gone', {}, { reason: 'crashed' });
advance(100);
const replacement = windows.windows.overlay;
assert(replacement && replacement !== crashed);
ready(replacement);
applied(replacement);
assert(replacement.visible);

// Reload resets readiness and old acknowledgements cannot show an empty page.
windows.sendOverlayState('recording', 0.8);
const oldSequence = replacement.webContents.sent.at(-1).payload.sequence;
replacement.webContents.emit('did-start-loading');
assert(!replacement.visible);
windows.sendOverlayState('recording', 0.23);
ipcMain.emit('overlay:applied', { sender: replacement.webContents }, oldSequence);
assert(!replacement.visible);
ready(replacement);
assert.deepStrictEqual(payload(replacement), { state: 'recording', level: 0.23 });
applied(replacement);
assert(replacement.visible);

// Cancel in the retry gap; no delayed resurrection.
replacement.webContents.emit('render-process-gone', {}, { reason: 'crashed' });
windows.hideOverlay();
advance(10000);
assert.strictEqual(windows.windows.overlay, null);

// Persistent failure gets only two retries. A later recording gets a new budget.
windows.showOverlay();
const before = FakeBrowserWindow.instances.length;
advance(20000);
assert.strictEqual(FakeBrowserWindow.instances.length, before + 2);
assert.strictEqual(windows.windows.overlay, null);
windows.showOverlay();
const last = windows.windows.overlay;
ready(last);
applied(last);
assert(last.visible);
windows.hideOverlay();
assert.strictEqual(timers.size, 0);
global.setTimeout = realSetTimeout;
global.clearTimeout = realClearTimeout;
console.log('windows overlay lifecycle tests passed');