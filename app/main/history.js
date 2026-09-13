'use strict';

const fs = require('fs');
const path = require('path');
const { app } = require('electron');

function log(msg) {
  console.log(`[history] ${msg}`);
}

function filePath() {
  const dir = path.join(app.getPath('appData'), 'SpokenlyV2');
  return path.join(dir, 'history.jsonl');
}

function readAll() {
  const p = filePath();
  try {
    if (!fs.existsSync(p)) return [];
    const lines = fs.readFileSync(p, 'utf8').split(/\r?\n/).filter(Boolean);
    const out = [];
    for (const line of lines) {
      try {
        out.push(JSON.parse(line));
      } catch (_) { /* skip corrupt line */ }
    }
    return out;
  } catch (e) {
    log(`read failed: ${e.message}`);
    return [];
  }
}

function writeAll(entries) {
  const p = filePath();
  try {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, entries.map((e) => JSON.stringify(e)).join('\n') + (entries.length ? '\n' : ''));
  } catch (e) {
    log(`write failed: ${e.message}`);
  }
}

function append({ duration_ms, text }) {
  const max = Math.max(1, (config && config.history_max) || 200);
  const entries = readAll();
  entries.push({
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    ts: Date.now(),
    duration_ms: Math.round(duration_ms || 0),
    text: String(text || ''),
  });
  while (entries.length > max) entries.shift();
  writeAll(entries);
}

function list(query) {
  let entries = readAll();
  if (query && String(query).trim()) {
    const q = String(query).toLowerCase();
    entries = entries.filter((e) => (e.text || '').toLowerCase().includes(q));
  }
  return entries.reverse(); // newest first
}

function remove(id) {
  const entries = readAll();
  const filtered = entries.filter((e) => e.id !== id);
  if (filtered.length !== entries.length) {
    writeAll(filtered);
    return true;
  }
  return false;
}

let config = null;
function setConfigRef(cfg) {
  config = cfg;
}

module.exports = { append, list, remove, setConfigRef, filePath };
