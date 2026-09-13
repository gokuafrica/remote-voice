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
  overlay_position: 'cursor',
  history_max: 200,
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
    if (fs.existsSync(p)) {
      const raw = JSON.parse(fs.readFileSync(p, 'utf8'));
      config = { ...DEFAULTS, ...raw };
      if (typeof config.pronunciation_fixes !== 'object' || config.pronunciation_fixes === null) {
        config.pronunciation_fixes = {};
      }
      // migrate legacy int device index
      if (typeof config.mic_device === 'number') config.mic_device = null;
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
    fs.writeFileSync(p, JSON.stringify(config, null, 4));
  } catch (e) {
    log(`save failed: ${e.message}`);
  }
}

module.exports = { load, get, set, saveNow, scheduleSave, configPath, DEFAULTS };
