'use strict';

const fs = require('fs');
const path = require('path');
const { app } = require('electron');

function log(msg) {
  console.log(`[history] ${msg}`);
}

let dataDirOverride = null;

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

function retentionMs() {
  const hours = Number(config && config.history_retention_hours);
  if (Number.isFinite(hours) && hours > 0) return hours * 3600 * 1000;
  return 24 * 3600 * 1000; // default: keep 24 hours
}

// Pure: drops entries older than the retention window. `ts` is epoch ms
// everywhere (Date.now()), so this is timezone-safe by construction.
// Entries with a missing/non-numeric ts are kept (the history_max cap
// bounds them) so a malformed line never silently nukes user data.
function pruneEntries(entries, now = Date.now()) {
  const cutoff = now - retentionMs();
  return entries.filter((e) => {
    const ts = Number(e && e.ts);
    if (!Number.isFinite(ts)) return true;
    return ts >= cutoff;
  });
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

// Prunes the on-disk file. Returns the number of lines removed (stale
// entries plus corrupt/unparseable lines, which are rewritten away).
// Called on startup, on every append, and on every history list query so
// the file never grows unbounded between app launches.
function pruneFile(now = Date.now()) {
  const p = filePath();
  let rawLines = [];
  try {
    if (fs.existsSync(p)) {
      rawLines = fs.readFileSync(p, 'utf8').split(/\r?\n/).filter(Boolean);
    }
  } catch (e) {
    log(`read failed: ${e.message}`);
    return 0;
  }
  const entries = [];
  for (const line of rawLines) {
    try {
      entries.push(JSON.parse(line));
    } catch (_) { /* skip corrupt line */ }
  }
  const kept = pruneEntries(entries, now);
  if (kept.length !== rawLines.length) {
    writeAll(kept);
    log(`pruned ${rawLines.length - kept.length} lines older than ${Math.round(retentionMs() / 3600000)}h`);
    return rawLines.length - kept.length;
  }
  return 0;
}

function append({ duration_ms, text }) {
  const max = Math.max(1, (config && config.history_max) || 200);
  const entries = pruneEntries(readAll());
  entries.push({
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    ts: Date.now(),
    duration_ms: Math.round(duration_ms || 0),
    text: String(text || ''),
  });
  while (entries.length > max) entries.shift(); // oldest first (belt and braces)
  writeAll(entries);
}

function list(query) {
  pruneFile();
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

module.exports = {
  append,
  list,
  remove,
  setConfigRef,
  setDataDir,
  pruneFile,
  pruneEntries,
  filePath,
};
