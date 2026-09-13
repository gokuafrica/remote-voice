'use strict';

const fs = require('fs');
const path = require('path');
const { app } = require('electron');

const DEFAULTS = {
  hotkey: 'right ctrl',
  mode: 'toggle',
  mic_device: null,
  sample_rate: 16000,
  voice_model: 'nemo-parakeet-tdt-0.6b-v2',
  pronunciation_fixes: {},
  overlay_position: 'bottom',
  history_max: 50,
  ollama_url: 'http://localhost:11434',
  ollama_model: 'qwen2.5:3b',
  cleanup_prompt: '',
  use_llm: false,
  python_cmd: null,
};

let _path = null;
let config = { ...DEFAULTS };
let saveTimer = null;

function log(msg) {
  console.log(`[config] ${msg}`);
}

// Canonical mode value used by the whole main process ('push_to_talk',
// underscore). The settings window used to save 'push-to-talk' (hyphen),
// which hotkey/tray/main silently treated as 'toggle' — normalize any
// spelling variant here so every write path lands on the same value.
function normalizeMode(mode) {
  if (typeof mode !== 'string') return undefined;
  // separators: whitespace, underscores AND hyphens ('push-to-talk' was the
  // legacy settings-renderer spelling and must map to 'push_to_talk', not
  // 'toggle')
  const m = mode.trim().toLowerCase().replace(/[\s_-]+/g, '_');
  return m === 'push_to_talk' ? 'push_to_talk' : 'toggle';
}

// One-time migrations applied to freshly loaded config. Follows the
// overlay_position: cursor -> bottom pattern.
function migrate(config) {
  const legacyMode = config.mode;
  const mode = normalizeMode(config.mode);
  if (mode && mode !== legacyMode) {
    log(`migrating mode '${legacyMode}' -> '${mode}'`);
    config.mode = mode;
    saveNow();
  }
  // '200' was the pre-rework seed default; the new default is 50. A persisted
  // 200 came from an old seed, not from the user (the value is not editable
  // in the UI yet), so migrate it once.
  if (Number(config.history_max) === 200) {
    log('migrating history_max 200 (legacy seed) -> 50');
    config.history_max = 50;
    saveNow();
  }
}

function configPath() {
  if (!_path) {
    if (app.isPackaged) {
      _path = path.join(app.getPath('appData'), 'Remote Voice', 'config.json');
    } else {
      // dev: worktree root (app/main/config.js -> app/..)
      _path = path.join(__dirname, '..', '..', 'config.json');
    }
  }
  return _path;
}

function load() {
  const p = configPath();
  try {
    if (!fs.existsSync(p) && app.isPackaged) {
      // first packaged run: seed from the bundled default config
      const seed = path.join(process.resourcesPath, 'config.json');
      if (fs.existsSync(seed)) {
        fs.mkdirSync(path.dirname(p), { recursive: true });
        fs.copyFileSync(seed, p);
        log(`seeded config from ${seed}`);
      }
    }
    if (fs.existsSync(p)) {
      const raw = JSON.parse(fs.readFileSync(p, 'utf8'));
      config = { ...DEFAULTS, ...raw };
      if (typeof config.pronunciation_fixes !== 'object' || config.pronunciation_fixes === null) {
        config.pronunciation_fixes = {};
      }
      // migrate legacy int device index
      if (typeof config.mic_device === 'number') config.mic_device = null;
      // migrate legacy Chromium deviceId (old settings renderer saved
      // m.id; the whole main process identifies mics by LABEL — tray radio
      // and recorder matchDevice both compare labels). A 32+ char hex string
      // can never be a device label, so reset to System Default.
      if (typeof config.mic_device === 'string' && /^[0-9a-f]{32,}$/i.test(config.mic_device)) {
        log('migrating mic_device legacy deviceId -> null (System Default)');
        config.mic_device = null;
        saveNow();
      }
      // One-time migration of legacy overlay_position: 'cursor' was the old
      // hardcoded default and was never exposed as a Settings choice, so any
      // persisted 'cursor' value came from an old seed, not from the user.
      // Migrate it (and a missing value) to the new default 'bottom'. Tension:
      // a user who later sets "cursor" by hand-editing config.json will be
      // re-migrated on the next app start; that is accepted because 'cursor'
      // was never a supported user-facing option in the current UI.
      if (!config.overlay_position || config.overlay_position === 'cursor') {
        log(`migrating overlay_position '${config.overlay_position || 'missing'}' -> 'bottom'`);
        config.overlay_position = 'bottom';
        saveNow();
      }
      migrate(config);
      log(`loaded ${p}`);
    } else {
      config = { ...DEFAULTS };
      saveNow();
      log(`created default config at ${p}`);
    }
  } catch (e) {
    log(`failed to load config (${e.message}), using defaults`);
    config = { ...DEFAULTS };
  }
  return config;
}


function get() {
  return config;
}

function set(patch) {
  if (patch && Object.prototype.hasOwnProperty.call(patch, 'mode')) {
    const m = normalizeMode(patch.mode);
    if (m === undefined) {
      // unknown/missing mode: leave the existing value untouched rather than
      // overwriting it with undefined
      const { mode, ...rest } = patch;
      void mode;
      patch = rest;
    } else {
      patch = { ...patch, mode: m };
    }
  }
  Object.assign(config, patch);
  scheduleSave();
}

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 300);
}

function saveNow() {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
  const p = configPath();
  try {
    fs.mkdirSync(path.dirname(p), { recursive: true });
      fs.writeFileSync(p, JSON.stringify(config, null, 4) + '\n');
  } catch (e) {
    log(`save failed: ${e.message}`);
  }
}

module.exports = { load, get, set, saveNow, scheduleSave, configPath, normalizeMode, DEFAULTS };
