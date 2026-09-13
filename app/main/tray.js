'use strict';

const { Tray, Menu, nativeImage } = require('electron');

const SIZE = 64;
const COLORS = {
  idle: [0x88, 0x88, 0x88, 0xff],
  recording: [0xff, 0x44, 0x44, 0xff],
  processing: [0x44, 0x88, 0xff, 0xff],
  success: [0x44, 0xcc, 0x44, 0xff],
};

function log(msg) {
  console.log(`[tray] ${msg}`);
}

// Procedurally drawn mic icon (port of old tray.py icon): colored circle +
// white mic body, stem and base, rendered into a raw BGRA bitmap.
function drawIcon(colorName) {
  const color = COLORS[colorName] || COLORS.idle;
  const buf = Buffer.alloc(SIZE * SIZE * 4);
  const put = (x, y, r, g, b, a) => {
    const i = (y * SIZE + x) * 4;
    buf[i] = b; buf[i + 1] = g; buf[i + 2] = r; buf[i + 3] = a;
  };
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const dx = x - 32;
      const dy = y - 32;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist <= 24) put(x, y, color[0], color[1], color[2], color[3]);
      else if (dist <= 25) put(x, y, color[0], color[1], color[2], 128); // AA edge
      // mic body (white rounded rect)
      else if (x >= 26 && x < 38 && y >= 14 && y < 38) put(x, y, 255, 255, 255, 255);
      // stem
      else if (x >= 31 && x < 33 && y >= 38 && y < 50) put(x, y, 255, 255, 255, 255);
      // base arc approximation: horizontal bar
      else if (y >= 50 && y < 52 && x >= 24 && x < 40) put(x, y, 255, 255, 255, 255);
    }
  }
  return nativeImage.createFromBitmap(buf, { width: SIZE, height: SIZE });
}

const STATE_TO_ICON = {
  IDLE: 'idle',
  RECORDING: 'recording',
  PROCESSING: 'processing',
};

class TrayIcon {
  constructor() {
    this.tray = null;
    this.icons = {};
    this.flashTimer = null;
    this.mics = [];
    this.deps = null; // {config, state, engine, history, paste, windows, onQuit}
  }

  init(deps) {
    this.deps = deps;
    for (const [key, name] of Object.entries(STATE_TO_ICON)) {
      this.icons[key] = drawIcon(name);
    }
    this.icons.success = drawIcon('success');
    this.tray = new Tray(this.icons.IDLE);
    this.tray.setToolTip('Remote Voice — Idle');
    this.rebuildMenu();
    deps.state.on('change', (next, prev, meta) => this._onState(next, meta));
    this.refreshMics();
  }

  async refreshMics() {
    try {
      const mics = await this.deps.recorder.listMics();
      if (Array.isArray(mics) && mics.length) {
        this.mics = mics;
        this.rebuildMenu();
      }
    } catch (_) { /* non-fatal */ }
  }

  _onState(next, meta) {
    if (!this.tray) return;
    if (next === 'IDLE' && meta && meta.flash) {
      this._flashSuccess();
      return;
    }
    this._cancelFlash();
    const icon = this.icons[next] || this.icons.IDLE;
    this.tray.setImage(icon);
    this.tray.setToolTip(`Remote Voice — ${next.charAt(0)}${next.slice(1).toLowerCase()}`);
  }

  _flashSuccess() {
    this._cancelFlash();
    this.tray.setImage(this.icons.success);
    this.tray.setToolTip('Remote Voice — Done');
    this.flashTimer = setTimeout(() => {
      this.flashTimer = null;
      if (this.tray && this.deps.state.current === 'IDLE') {
        this.tray.setImage(this.icons.IDLE);
        this.tray.setToolTip('Remote Voice — Idle');
      }
    }, 500);
  }

  _cancelFlash() {
    if (this.flashTimer) {
      clearTimeout(this.flashTimer);
      this.flashTimer = null;
    }
  }

  rebuildMenu() {
    if (!this.tray || !this.deps) return;
    const { config, state, engine, history, paste, windows } = this.deps;
    const cfg = config.get();
    const modeLabel = cfg.mode === 'push_to_talk' ? 'hold' : 'toggle';

    const setMode = (mode) => () => {
      config.set({ mode });
      log(`mode -> ${mode}`);
      this.rebuildMenu();
    };
    const setMic = (name) => () => {
      config.set({ mic_device: name });
      log(`mic -> ${name === null ? 'System Default' : name}`);
      this.rebuildMenu();
    };

    const micItems = [
      {
        label: 'System Default',
        type: 'radio',
        checked: !cfg.mic_device,
        click: setMic(null),
      },
      ...this.mics.map((d) => ({
        label: d.label,
        type: 'radio',
        checked: cfg.mic_device === d.label,
        click: setMic(d.label),
      })),
    ];

    const menu = Menu.buildFromTemplate([
      { label: `Hotkey: ${cfg.hotkey} (${modeLabel})`, enabled: false },
      { type: 'separator' },
      {
        label: 'Mode',
        submenu: [
          { label: 'Push to Talk (hold hotkey)', type: 'radio', checked: cfg.mode === 'push_to_talk', click: setMode('push_to_talk') },
          { label: 'Toggle (press twice)', type: 'radio', checked: cfg.mode !== 'push_to_talk', click: setMode('toggle') },
        ],
      },
      { label: 'Microphone', submenu: micItems },
      { type: 'separator' },
      {
        label: 'Paste last transcription',
        click: async () => {
          try {
            const items = history.list();
            if (items.length && items[0].text) {
              await paste.pasteText(items[0].text);
            } else {
              log('no history to paste');
            }
          } catch (e) {
            log(`paste last failed: ${e.message}`);
          }
        },
      },
      { label: 'Settings…', click: () => windows.openSettings() },
      { type: 'separator' },
      {
        label: 'Quit',
        click: () => {
          try { engine.shutdown(); } catch (_) { /* ignore */ }
          this.deps.onQuit();
        },
      },
    ]);
    this.tray.setContextMenu(menu);
  }

  destroy() {
    this._cancelFlash();
    if (this.tray) {
      try { this.tray.destroy(); } catch (_) { /* ignore */ }
      this.tray = null;
    }
  }
}

module.exports = new TrayIcon();
