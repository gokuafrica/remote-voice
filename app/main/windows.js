'use strict';

const fs = require('fs');
const path = require('path');
const { BrowserWindow, screen, ipcMain } = require('electron');

// Matches the overlay CSS design (renderer README: ~240x60). Keep in sync
// with app/renderer/overlay.css.
const OVERLAY_SIZE = { width: 240, height: 60 };
const SETTINGS_SIZE = { width: 940, height: 680 };

function log(msg) {
  console.log(`[windows] ${msg}`);
}

const windows = {
  overlay: null,
  settings: null,
  config: null,
  deps: null,
};

// Overlay state is intentionally cached in the main process. A BrowserWindow
// can be created, hidden, or reloaded independently of the recording state;
// renderer IPC is not a durable state channel.
let overlayState = { state: 'hidden', level: 0 };
let overlayReady = false;

function rendererPath(name) {
  return path.join(__dirname, '..', 'renderer', name);
}

function init(deps) {
  windows.config = deps.config;
  windows.deps = deps;
  ipcMain.on('overlay:cancel', () => {
    if (deps.onCancel) deps.onCancel('overlay click');
  });
  ipcMain.on('overlay:ready', (event) => {
    const win = windows.overlay;
    if (!win || win.isDestroyed() || event.sender !== win.webContents) return;
    markOverlayReady(win, 'renderer handshake');
  });
}

function overlayFileExists() {
  return fs.existsSync(rendererPath('overlay.html'));
}

function settingsFileExists() {
  return fs.existsSync(rendererPath('settings.html'));
}

function sendOverlayStateToRenderer(win) {
  if (!win || win.isDestroyed() || !overlayReady) return false;
  try {
    win.webContents.send('overlay:state', { ...overlayState });
    return true;
  } catch (e) {
    log(`overlay state send failed: ${e.message}`);
    return false;
  }
}

function markOverlayReady(win, source) {
  if (windows.overlay !== win || win.isDestroyed()) return;
  overlayReady = true;
  sendOverlayStateToRenderer(win);
  if (overlayState.state !== 'hidden') win.showInactive();
  log(`overlay renderer ready (${source})`);
}

function discardOverlay(win, reason) {
  if (windows.overlay !== win) return;
  overlayReady = false;
  windows.overlay = null;
  log(`overlay discarded: ${reason}`);
  try {
    if (!win.isDestroyed()) win.destroy();
  } catch (_) { /* best effort */ }
}

function createOverlay() {
  if (windows.overlay && !windows.overlay.isDestroyed()) return windows.overlay;
  if (!overlayFileExists()) {
    log('overlay.html missing — overlay disabled (UI agent pending)');
    return null;
  }
  const win = new BrowserWindow({
    show: false,
    frame: false,
    transparent: true,
    // Windows defaults draw a square DWM shadow/frame around the window rect,
    // which shows as a square halo around the rounded pill. Both must be off.
    hasShadow: false,
    thickFrame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    focusable: false,
    width: OVERLAY_SIZE.width,
    height: OVERLAY_SIZE.height,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });
  windows.overlay = win;
  overlayReady = false;

  win.setAlwaysOnTop(true, 'screen-saver');
  win.webContents.on('did-start-loading', () => {
    if (windows.overlay !== win) return;
    overlayReady = false;
    if (win.isVisible()) win.hide();
  });
  win.webContents.on('did-finish-load', () => {
    if (windows.overlay === win) log('overlay document loaded; awaiting renderer handshake');
  });
  win.webContents.on('did-fail-load', (_event, errorCode, errorDescription, _validatedURL, isMainFrame) => {
    if (isMainFrame === false) return;
    log(`overlay load failed (${errorCode}): ${errorDescription}`);
    discardOverlay(win, 'main-frame load failure');
  });
  win.webContents.on('preload-error', (_event, preloadPath, error) => {
    log(`overlay preload failed (${preloadPath}): ${error && error.message ? error.message : error}`);
    discardOverlay(win, 'preload failure');
  });
  win.webContents.on('render-process-gone', (_event, details) => {
    const reason = details && details.reason ? details.reason : 'unknown reason';
    log(`overlay renderer gone: ${reason}`);
    discardOverlay(win, 'renderer process gone');
  });
  win.on('closed', () => {
    if (windows.overlay === win) {
      windows.overlay = null;
      overlayReady = false;
    }
  });
  win.loadFile(rendererPath('overlay.html')).catch((e) => {
    log(`overlay load promise rejected: ${e.message}`);
    discardOverlay(win, 'load promise rejection');
  });
  return win;
}

function positionOverlay() {
  const win = windows.overlay;
  if (!win || win.isDestroyed()) return;
  const cfg = windows.config.get();
  const cursor = screen.getCursorScreenPoint();
  // multi-monitor: use the display the cursor is currently on
  const display = screen.getDisplayNearestPoint(cursor);
  const wa = display.workArea;
  let pos;
  if (cfg.overlay_position === 'tray') {
    try {
      const bounds = windows.deps.tray.tray.getBounds();
      pos = { x: bounds.x - OVERLAY_SIZE.width, y: bounds.y - OVERLAY_SIZE.height - 8 };
    } catch (_) {
      log('tray bounds unavailable — positioning overlay at bottom center');
      pos = {
        x: wa.x + Math.round((wa.width - OVERLAY_SIZE.width) / 2),
        y: wa.y + wa.height - OVERLAY_SIZE.height - 18,
      };
    }
  } else if (cfg.overlay_position === 'cursor') {
    pos = { x: cursor.x - OVERLAY_SIZE.width / 2, y: cursor.y + 24 };
  } else {
    // default 'bottom': bottom-center of the cursor's display, just above the taskbar
    pos = {
      x: wa.x + Math.round((wa.width - OVERLAY_SIZE.width) / 2),
      y: wa.y + wa.height - OVERLAY_SIZE.height - 18,
    };
  }
  pos.x = Math.max(wa.x + 8, Math.min(pos.x, wa.x + wa.width - OVERLAY_SIZE.width - 8));
  pos.y = Math.max(wa.y + 8, Math.min(pos.y, wa.y + wa.height - OVERLAY_SIZE.height - 8));
  log(`overlay bounds: ${pos.x},${pos.y} (display ${display.bounds.x},${display.bounds.y} ${display.bounds.width}x${display.bounds.height}, mode ${cfg.overlay_position || 'bottom'})`);
  win.setPosition(pos.x, pos.y);
}

function showOverlay() {
  overlayState = { state: 'recording', level: 0 };
  const win = createOverlay();
  if (!win) return;
  positionOverlay();
  if (overlayReady) {
    sendOverlayStateToRenderer(win);
    win.showInactive();
    log('overlay shown');
  } else {
    log('overlay show deferred until renderer ready');
  }
}

function hideOverlay() {
  overlayState = { state: 'hidden', level: 0 };
  const win = windows.overlay;
  if (win && !win.isDestroyed()) {
    sendOverlayStateToRenderer(win);
    win.hide();
    log('overlay hidden');
  }
}

function sendOverlayState(stateName, level) {
  overlayState = { state: stateName, level: Number(level) || 0 };
  const win = windows.overlay;
  sendOverlayStateToRenderer(win);
}

function createSettings() {
  if (windows.settings && !windows.settings.isDestroyed()) {
    return windows.settings;
  }
  if (!settingsFileExists()) {
    log('settings.html missing — settings window unavailable (UI agent pending)');
    return null;
  }
  windows.settings = new BrowserWindow({
    width: SETTINGS_SIZE.width,
    height: SETTINGS_SIZE.height,
    backgroundColor: '#1e1e1e',
    title: 'Remote Voice — Settings',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  windows.settings.loadFile(rendererPath('settings.html'));
  windows.settings.on('closed', () => { windows.settings = null; });
  log('settings window created (menu: none)');
  return windows.settings;
}

function openSettings() {
  const win = createSettings();
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

function focusOrCreateSettings() {
  if (windows.settings && !windows.settings.isDestroyed()) {
    if (windows.settings.isMinimized()) windows.settings.restore();
    windows.settings.focus();
    return;
  }
  if (settingsFileExists()) {
    openSettings();
  } else {
    log('second instance launch: settings.html missing, nothing to focus');
  }
}

function broadcast(channel, payload) {
  for (const win of [windows.overlay, windows.settings]) {
    if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
  }
}

module.exports = {
  init,
  showOverlay,
  hideOverlay,
  sendOverlayState,
  openSettings,
  focusOrCreateSettings,
  broadcast,
  overlayFileExists,
  settingsFileExists,
  get windows() { return windows; },
};
