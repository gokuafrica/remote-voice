'use strict';

const fs = require('fs');
const path = require('path');
const { app } = require('electron');

function log(msg) {
  console.log(`[history] ${msg}`);
}

let dataDirOverride = null;
let config = null;

function dataDir() {
  if (dataDirOverride) return dataDirOverride;
  return path.join(app.getPath('appData'), 'Remote Voice');
}

function filePath() {
  return path.join(dataDir(), 'history.jsonl');
}

function setDataDir(dir) {
  dataDirOverride = dir;
}

function setConfigRef(cfg) {
  config = cfg;
}

function maxEntries() {
  const max = Number(config && config.history_max);
  if (Number.isFinite(max) && max > 0) return Math.floor(max);
  return 50; // default: keep the last 50 dictations
}

// Pure: keeps only the newest `max` entries, rotating the oldest out first.
// No time-based retention — history is only bounded by count.
function capEntries(entries, max = maxEntries()) {
  const limit = Math.max(1, max);
  return entries.slice(-limit);
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
  // write temp file + rename so a crash mid-write can never leave
  // partial/truncated lines in history.jsonl
  const p = filePath();
  try {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    const tmp = `${p}.tmp`;
    fs.writeFileSync(tmp, entries.map((e) => JSON.stringify(e)).join('\n') + (entries.length ? '\n' : ''));
    fs.renameSync(tmp, p);
  } catch (e) {
    log(`write failed: ${e.message}`);
  }
}

function append({ duration_ms, text }) {
  const entries = readAll();
  entries.push({
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    ts: Date.now(),
    duration_ms: Math.round(duration_ms || 0),
    text: String(text || ''),
  });
  writeAll(capEntries(entries)); // rotate oldest out past the cap
}

function list(query) {
  let entries = readAll();
  const max = maxEntries();
  if (entries.length > max) {
    // config cap shrank since the last write — persist the trim
    entries = capEntries(entries, max);
    writeAll(entries);
  }
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

module.exports = {
  append,
  list,
  remove,
  setConfigRef,
  setDataDir,
  capEntries,
  filePath,
};
