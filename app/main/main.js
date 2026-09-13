'use strict';

const { app, ipcMain, session } = require('electron');

app.setName('Remote Voice');

const config = require('./config');
const state = require('./state');
const engine = require('./engine');
const hotkey = require('./hotkey');
const recorder = require('./recorder');
const paste = require('./paste');
const tray = require('./tray');
const windows = require('./windows');
const history = require('./history');

const SMOKE = process.argv.includes('--smoke');
const SMOKE_TIMEOUT_MS = 30000;

function log(msg) {
  console.log(`[main] ${msg}`);
}

// One-time migration from the old %APPDATA%/SpokenlyV2 data dir (best effort).
function migrateLegacyData() {
  const fs = require('fs');
  const path = require('path');
  try {
    const oldDir = path.join(app.getPath('appData'), 'SpokenlyV2');
    const newDir = path.join(app.getPath('appData'), 'Remote Voice');
    if (!fs.existsSync(oldDir)) return;
    fs.mkdirSync(newDir, { recursive: true });
    for (const name of ['history.jsonl', 'config.json']) {
      const src = path.join(oldDir, name);
      const dst = path.join(newDir, name);
      if (fs.existsSync(src) && !fs.existsSync(dst)) {
        fs.copyFileSync(src, dst);
        log(`migrated legacy ${name} from SpokenlyV2`);
      }
    }
  } catch (e) {
    log(`legacy data migration failed: ${e.message}`);
  }
}

if (!app.requestSingleInstanceLock()) {
  if (SMOKE) {
    console.log(JSON.stringify({ tray: false, engine_ready: false, engine: 'error', latency_ms: null, error: 'another instance is running' }));
    app.exit(1);
  } else {
    log('another instance is running — quitting');
    app.quit();
  }
} else {
  app.on('second-instance', () => {
    windows.focusOrCreateSettings();
  });

  app.whenReady().then(onReady).catch((e) => {
    log(`startup failed: ${e.message}`);
    if (SMOKE) {
      console.log(JSON.stringify({ tray: false, engine_ready: false, engine: 'error', latency_ms: null, error: e.message }));
      app.exit(1);
    } else {
      app.quit();
    }
  });
}

async function onReady() {
  migrateLegacyData();
  config.load();
  history.setConfigRef(config.get());
  history.pruneFile(); // rolling 24h retention: drop old entries at startup

  // allow mic capture from renderer pages
  try {
    session.defaultSession.setPermissionRequestHandler((wc, permission, callback) => {
      callback(permission === 'media');
    });
    session.defaultSession.setPermissionCheckHandler((wc, permission) => permission === 'media');
  } catch (e) {
    log(`permission handler setup failed: ${e.message}`);
  }

  recorder.init(state);
  windows.init({
    config,
    tray,
    onCancel: (reason) => cancelRecording(reason),
  });

  // engine status -> settings window + smoke. Also cache the latest status
  // so a renderer that loads after a transition can pull it (engine:status:get)
  // instead of staying stuck on its static "Loading" placeholder.
  let lastEngineStatus = null;
  engine.on('status', ({ ready, model, error }) => {
    lastEngineStatus = { ready, model, error };
    windows.broadcast('engine:status', { ready, model, error });
  });
  engine.start(config);

  tray.init({
    config,
    state,
    engine,
    recorder,
    history,
    paste,
    windows,
    onQuit: () => app.quit(),
  });

  hotkey.register(config, {
    onPress: () => onHotkeyPress(),
    onRelease: () => onHotkeyRelease(),
    onCancel: (reason) => cancelRecording(reason),
  });

  // state transitions -> tray icon + overlay
  state.on('change', (next) => {
    if (next === 'RECORDING') windows.showOverlay();
    else windows.hideOverlay();
  });
  state.on('level', (level) => {
    windows.sendOverlayState('recording', level);
  });

  // settings IPC
  ipcMain.handle('settings:get', () => config.get());
  ipcMain.handle('engine:status:get', () => lastEngineStatus);
  ipcMain.handle('settings:save', async (e, cfg) => {
    config.set(cfg || {});
    try {
      await engine.setFixes(config.get().pronunciation_fixes);
    } catch (err) {
      log(`set_fixes after save failed: ${err.message}`);
    }
    hotkey.applyConfig(config.get());
    tray.rebuildMenu();
    return true;
  });
  ipcMain.handle('mics:list', async () => recorder.listMics());
  ipcMain.handle('history:list', (e, query) => history.list(query));
  ipcMain.handle('history:delete', (e, id) => history.remove(id));

  if (SMOKE) {
    runSmoke();
  } else {
    log('ready');
  }
}

async function onHotkeyPress() {
  if (state.is('IDLE')) {
    startRecording();
  } else if (state.is('RECORDING') && config.get().mode === 'toggle') {
    stopRecording();
  }
}

function onHotkeyRelease() {
  if (config.get().mode === 'push_to_talk' && state.is('RECORDING')) {
    // If the mic is still opening, recorder.start will notice via isHeld check
    // after it resolves; if it is open, stop now.
    if (recorder.isOpen()) stopRecording();
  }
}

async function startRecording() {
  if (!state.is('IDLE')) return;
  state.set('RECORDING', { reason: 'hotkey' });
  try {
    await recorder.start(config.get().mic_device);
    // mic-open window: hotkey may already have been released (push_to_talk)
    if (config.get().mode === 'push_to_talk' && !hotkey.isHeld()) {
      log('hotkey released during mic open — auto-stopping');
      await stopRecording();
    }
  } catch (e) {
    log(`recording failed to start: ${e.message}`);
    if (state.is('RECORDING')) state.set('IDLE', { reason: 'start failed' });
  }
}

async function stopRecording() {
  if (!state.is('RECORDING')) return;
  state.set('PROCESSING', { reason: 'stop' });
  try {
    const result = await recorder.stop();
    if (!result) {
      state.set('IDLE', { reason: 'no audio' });
      return;
    }
    if (result.durationMs < 200) {
      log(`recording too short (${result.durationMs}ms) — discarding`);
      state.set('IDLE', { reason: 'too short' });
      return;
    }
    const resp = await engine.transcribe(result.path);
    const text = (resp.text || '').trim();
    log(`transcribe result: ${text.length} chars: ${text.slice(0, 120)}`);
    if (text) {
      const pasted = await paste.pasteText(text);
      log(`paste attempt result: ${pasted ? 'ok' : 'failed'}`);
    }
    history.append({ duration_ms: result.durationMs, text });
    state.set('IDLE', { reason: 'done', flash: true });
  } catch (e) {
    log(`transcription failed: ${e.message}`);
    state.set('IDLE', { reason: 'error' });
  } finally {
    cleanupWavFiles();
  }
}

async function cancelRecording(reason) {
  if (!state.is('RECORDING')) return;
  log(`cancel (${reason})`);
  await recorder.stop({ discard: true }).catch(() => {});
  state.set('IDLE', { reason: 'cancelled' });
}

function cleanupWavFiles() {
  // leave temp wav files for debugging; OS cleans tmpdir. No-op by design.
}

app.on('before-quit', () => {
  try { hotkey.stop(); } catch (_) { /* ignore */ }
  try { engine.shutdown(); } catch (_) { /* ignore */ }
  try { recorder.destroy(); } catch (_) { /* ignore */ }
  try { tray.destroy(); } catch (_) { /* ignore */ }
  config.saveNow();
});

app.on('window-all-closed', () => {
  // tray app: keep running unless Quit chosen
});

// ---------------------------------------------------------------------------
// Smoke test mode: `npm run smoke` / `electron . --smoke`
// ---------------------------------------------------------------------------
function runSmoke() {
  const t0 = Date.now();
  let settled = false;

  const finish = (result) => {
    if (settled) return;
    settled = true;
    console.log(JSON.stringify(result));
    app.exit(result.tray && (result.engine_ready || result.engine === 'missing') ? 0 : 1);
  };

  const timeout = setTimeout(() => {
    finish({
      tray: !!tray.tray,
      engine_ready: engine.ready,
      engine: 'error',
      latency_ms: null,
      error: 'smoke timeout after 30s',
    });
  }, SMOKE_TIMEOUT_MS);

  const check = () => {
    if (settled) return;
    if (engine.error === 'engine missing (shell-only)') {
      console.log('engine: missing (shell-only)');
      clearTimeout(timeout);
      finish({
        tray: !!tray.tray,
        engine_ready: false,
        engine: 'missing',
        latency_ms: null,
      });
      return;
    }
    if (engine.ready) {
      clearTimeout(timeout);
      finish({
        tray: !!tray.tray,
        engine_ready: true,
        engine: 'ready',
        latency_ms: Date.now() - t0,
      });
      return;
    }
    if (engine.error && engine.error !== 'engine missing (shell-only)') {
      clearTimeout(timeout);
      finish({
        tray: !!tray.tray,
        engine_ready: false,
        engine: 'error',
        latency_ms: null,
        error: engine.error,
      });
      return;
    }
    setTimeout(check, 250);
  };

  // give tray/windows a moment to be created, then poll engine status
  setTimeout(check, 500);
}
