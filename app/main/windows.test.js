'use strict';

const assert = require('assert');
const { EventEmitter } = require('events');
const Module = require('module');

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

// The first show request must be deferred until the renderer has installed
// its IPC listener and sent the ready handshake.
windows.showOverlay();
const first = windows.windows.overlay;
assert(first);
assert.strictEqual(first.visible, false);
assert.deepStrictEqual(first.webContents.sent, []);

ipcMain.emit('overlay:ready', { sender: first.webContents });
assert.strictEqual(first.visible, true);
assert.deepStrictEqual(first.webContents.sent, [
  { channel: 'overlay:state', payload: { state: 'recording', level: 0 } },
]);

// State updates are retained and delivered while the renderer is ready.
windows.sendOverlayState('recording', 0.8);
assert.deepStrictEqual(first.webContents.sent.at(-1), {
  channel: 'overlay:state',
  payload: { state: 'recording', level: 0.8 },
});

// A renderer process gone event discards the bad window so the next request
// creates a fresh one instead of reusing a dead BrowserWindow.
first.webContents.emit('render-process-gone', {}, { reason: 'crashed' });
assert.strictEqual(windows.windows.overlay, null);

// Stopping before the new renderer is ready must not resurrect a stale pill.
windows.showOverlay();
const second = windows.windows.overlay;
windows.sendOverlayState('recording', 0.75);
windows.hideOverlay();
assert.strictEqual(second.visible, false);
assert.deepStrictEqual(second.webContents.sent, []);
ipcMain.emit('overlay:ready', { sender: second.webContents });
assert.strictEqual(second.visible, false);
assert.deepStrictEqual(second.webContents.sent, [
  { channel: 'overlay:state', payload: { state: 'hidden', level: 0 } },
]);

// Reloads reset readiness; the latest desired state is replayed when the
// renderer handshakes again.
windows.showOverlay();
const sentBeforeReload = second.webContents.sent.length;
second.webContents.emit('did-start-loading');
assert.strictEqual(second.visible, false);
windows.sendOverlayState('recording', 0.42);
assert.strictEqual(second.webContents.sent.length, sentBeforeReload);
ipcMain.emit('overlay:ready', { sender: second.webContents });
assert.strictEqual(second.visible, true);
assert.deepStrictEqual(second.webContents.sent.at(-1), {
  channel: 'overlay:state',
  payload: { state: 'recording', level: 0.42 },
});

console.log('windows overlay lifecycle tests passed');
