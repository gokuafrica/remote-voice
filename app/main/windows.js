'use strict';

const fs = require('fs');
const path = require('path');
const { BrowserWindow, screen, ipcMain } = require('electron');

const OVERLAY_SIZE = { width: 180, height: 56 };
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

function rendererPath(name) {
  return path.join(__dirname, '..', 'renderer', name);
}

function init(deps) {
  windows.config = deps.config;
  windows.deps = deps;
  ipcMain.on('overlay:cancel', () => {
    if (deps.onCancel) deps.onCancel('overlay click');
  });
}

function overlayFileExists() {
  return fs.existsSync(rendererPath('overlay.html'));
}

function settingsFileExists() {
  return fs.existsSync(rendererPath('settings.html'));
}

function createOverlay() {
  if (windows.overlay && !windows.overlay.isDestroyed()) return windows.overlay;
  if (!overlayFileExists()) {
    log('overlay.html missing — overlay disabled (UI agent pending)');
    return null;
  }
  windows.overlay = new BrowserWindow({
    show: false,
    frame: false,
    transparent: true,
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
  windows.overlay.setAlwaysOnTop(true, 'screen-saver');
  windows.overlay.loadFile(rendererPath('overlay.html'));
  windows.overlay.on('closed', () => { windows.overlay = null; });
  return windows.overlay;
}

function positionOverlay() {
  const win = windows.overlay;
  if (!win || win.isDestroyed()) return;
  const cfg = windows.config.get();
  let pos;
  if (cfg.overlay_position === 'tray') {
    try {
      const bounds = windows.deps.tray.tray.getBounds();
      pos = { x: bounds.x - OVERLAY_SIZE.width, y: bounds.y - OVERLAY_SIZE.height - 8 };
    } catch (_) {
      pos = null;
    }
  }
  if (!pos) {
    const p = screen.getCursorScreenPoint();
    pos = { x: p.x - OVERLAY_SIZE.width / 2, y: p.y + 24 };
  }
  const display = screen.getDisplayNearestPoint({ x: pos.x, y: pos.y });
  const wa = display.workArea;
  pos.x = Math.max(wa.x + 8, Math.min(pos.x, wa.x + wa.width - OVERLAY_SIZE.width - 8));
  pos.y = Math.max(wa.y + 8, Math.min(pos.y, wa.y + wa.height - OVERLAY_SIZE.height - 8));
  win.setPosition(pos.x, pos.y);
}

function showOverlay() {
  const win = createOverlay();
  if (!win) return;
  positionOverlay();
  win.showInactive();
  win.webContents.send('overlay:state', { state: 'recording', level: 0 });
}

function hideOverlay() {
  const win = windows.overlay;
  if (win && !win.isDestroyed()) {
    win.webContents.send('overlay:state', { state: 'hidden', level: 0 });
    win.hide();
  }
}

function sendOverlayState(stateName, level) {
  const win = windows.overlay;
  if (win && !win.isDestroyed() && win.isVisible()) {
    win.webContents.send('overlay:state', { state: stateName, level });
  }
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
    title: 'Spokenly V2 — Settings',
    webPreferences: {
      preload: path.join(__dirname, '..', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  windows.settings.loadFile(rendererPath('settings.html'));
  windows.settings.on('closed', () => { windows.settings = null; });
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
