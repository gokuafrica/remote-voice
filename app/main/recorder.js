'use strict';

const path = require('path');
const fs = require('fs');
const { BrowserWindow, ipcMain } = require('electron');

function log(msg) {
  console.log(`[recorder] ${msg}`);
}

const START_TIMEOUT_MS = 5000;
const STOP_TIMEOUT_MS = 3000;
const ENUM_TIMEOUT_MS = 3000;

class Recorder {
  constructor() {
    this.win = null;
    this.frames = [];
    this.recording = false;
    this.startedAt = 0;
    this.pending = new Map(); // tag -> {resolve, reject, timer}
    this.seq = 0;
  }

  init(state) {
    this.state = state;
    ipcMain.on('rec:audio', (e, buf) => {
      if (this.recording && e.sender === this.win.webContents) {
        this.frames.push(Buffer.from(buf));
      }
    });
    ipcMain.on('rec:level', (e, level) => {
      if (e.sender === this.win.webContents && this.state) this.state.setLevel(level);
    });
    for (const tag of ['rec:started', 'rec:error', 'rec:stopped', 'rec:devices']) {
      ipcMain.on(tag, (e, payload) => {
        const entry = this.pending.get(tag);
        if (entry && e.sender === this.win.webContents) {
          this.pending.delete(tag);
          clearTimeout(entry.timer);
          entry.resolve(payload);
        }
      });
    }
  }

  ensureWindow() {
    if (this.win && !this.win.isDestroyed()) return this.win;
    const preload = path.join(__dirname, '..', 'renderer', 'recorder-preload.js');
    const html = path.join(__dirname, '..', 'renderer', 'recorder.html');
    if (!fs.existsSync(html)) {
      throw new Error('recorder.html missing');
    }
    this.win = new BrowserWindow({
      show: false,
      skipTaskbar: true,
      width: 1,
      height: 1,
      webPreferences: {
        preload,
        contextIsolation: true,
        nodeIntegration: false,
        backgroundThrottling: false,
      },
    });
    this._loadPromise = this.win.loadFile(html).then(() => undefined);
    this._loadPromise.catch(() => {});
    this.win.on('closed', () => { this.win = null; });
    return this.win;
  }

  async _ready() {
    this.ensureWindow();
    try {
      await this._loadPromise;
    } catch (e) {
      throw new Error(`recorder page failed to load: ${e.message}`);
    }
  }

  _waitFor(tag, timeoutMs, errMessage) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(tag);
        reject(new Error(errMessage));
      }, timeoutMs);
      this.pending.set(tag, { resolve, reject, timer });
    });
  }

  async start(deviceName) {
    await this._ready();
    const win = this.win;
    if (this.recording) {
      log('start ignored: already recording');
      return { device: 'already recording' };
    }
    this.frames = [];
    this.recording = true;
    this.startedAt = Date.now();

    // race started vs error
    const attemptRace = async (name, isFallback) => {
      const startedP = this._waitFor('rec:started', START_TIMEOUT_MS, 'mic open timed out');
      const errorP = this._waitFor('rec:error', START_TIMEOUT_MS + 500, 'mic open failed');
      win.webContents.send('rec:start', { deviceName: name, sampleRate: 16000 });
      try {
        const res = await Promise.race([
          startedP.then((v) => ({ ok: true, v })),
          errorP.then((v) => ({ ok: false, v })),
        ]);
        // let errorP settle silently
        errorP.catch(() => {});
        if (res.ok) return { ...res.v, fallback: isFallback };
        throw new Error(res.v && res.v.message ? res.v.message : 'mic open failed');
      } catch (e) {
        startedP.catch(() => {});
        throw e;
      }
    };

    try {
      let res;
      if (deviceName) {
        try {
          res = await attemptRace(deviceName, false);
        } catch (e) {
          log(`named device "${deviceName}" failed (${e.message}) — retrying with system default`);
          this.recording = true; // attemptRace may have been rejected before renderer set state
          res = await attemptRace(null, true);
        }
      } else {
        res = await attemptRace(null, false);
      }
      log(`mic open: device=${res.device || 'default'}, sr=${res.sampleRate}${res.fallback ? ' (fallback)' : ''}`);
      return res;
    } catch (e) {
      this.recording = false;
      this.frames = [];
      throw e;
    }
  }

  /** Stop capture and return PCM in memory. Returns {pcm, sampleRate, durationMs, bytes} or null. */
  isOpen() {
    return this.recording;
  }

  async stop({ discard = false } = {}) {
    if (!this.recording) return null;
    this.recording = false;
    const startFramesLen = this.frames.length;
    try {
      if (this.win && !this.win.isDestroyed()) {
        this.win.webContents.send('rec:stop', {});
        try {
          await this._waitFor('rec:stopped', STOP_TIMEOUT_MS, 'recorder stop timed out');
        } catch (e) {
          log(e.message);
        }
      }
    } catch (_) { /* ignore */ }

    const frames = this.frames;
    this.frames = [];
    if (discard) {
      log('recording discarded');
      return null;
    }
    if (frames.length === 0) {
      log('no audio captured');
      return null;
    }
    const pcm = Buffer.concat(frames);
    const durationMs = Math.round((pcm.length / 2 / 16000) * 1000);
    log(`audio captured in memory (${pcm.length} pcm bytes, ${durationMs}ms, started with ${startFramesLen} frames)`);
    return { pcm, sampleRate: 16000, durationMs, bytes: pcm.length };
  }

  async listMics() {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await this._ready();
        const p = this._waitFor('rec:devices', ENUM_TIMEOUT_MS, 'device enumeration timed out');
        this.win.webContents.send('rec:enum', {});
        const devices = await p;
        if (Array.isArray(devices)) return devices;
      } catch (e) {
        log(`listMics attempt ${attempt + 1} failed: ${e.message}`);
      }
    }
    return [];
  }

  destroy() {
    if (this.win && !this.win.isDestroyed()) this.win.destroy();
    this.win = null;
  }
}

module.exports = new Recorder();
