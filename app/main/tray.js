'use strict';

const { Tray, Menu, nativeImage } = require('electron');
const iconDraw = require('./trayIconDraw');

const TRAY_SIZE = 32; // downscaled in-app from the 256px master for crisp AA

function log(msg) {
  console.log(`[tray] ${msg}`);
}

// Studio-mic glyph on a state-colored circle: drawn at 256x256 with SDF
// antialiasing (app/main/trayIconDraw.js), area-downsampled to 32 and handed
// to Windows as a premultiplied BGRA bitmap.
function drawIcon(colorName) {
  const master = iconDraw.renderIcon(colorName);
  const small = iconDraw.downsample(master.data, master.size, TRAY_SIZE);
  return nativeImage.createFromBitmap(Buffer.from(iconDraw.rgbaToBgraPremultiplied(small)), {
    width: TRAY_SIZE,
    height: TRAY_SIZE,
  });
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

    // apply via the SAME main-process path the settings window uses
    // (deps.onApply -> applyChange: persists, re-registers hotkey, rebuilds
    // this menu) so the two paths can never diverge.
    const setMode = (mode) => () => {
      log(`mode -> ${mode}`);
      this.deps.onApply({ mode });
    };
    const setMic = (name) => () => {
      log(`mic -> ${name === null ? 'System Default' : name}`);
      this.deps.onApply({ mic_device: name });
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
    log(`menu rebuilt: mode=${modeLabel}, mic=${cfg.mic_device === null ? 'System Default' : cfg.mic_device}`);
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
