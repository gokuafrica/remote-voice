'use strict';

// Node-only test for history retention pruning. Run: `node app/tools/history_test.js`
// (no Electron needed — history.js only touches the electron `app` path when
// no data dir override is set, and the tests always set one).

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const history = require('../main/history');

const HOUR = 3600 * 1000;
const DAY = 24 * HOUR;

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`ok - ${name}`);
  } catch (e) {
    console.error(`FAIL - ${name}`);
    console.error(e && e.stack ? e.stack : e);
    process.exit(1);
  }
}

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'rv-history-'));
}

function makeEntry(ts, text) {
  return {
    id: `t-${ts}-${Math.random().toString(36).slice(2, 8)}`,
    ts,
    duration_ms: 1000,
    text,
  };
}

function readLines(dir) {
  const p = path.join(dir, 'history.jsonl');
  return fs.readFileSync(p, 'utf8').split(/\r?\n/).filter(Boolean);
}

// every line must be a complete JSON object — no partial/truncated lines
function assertWellFormed(dir) {
  const lines = readLines(dir);
  for (const line of lines) {
    const obj = JSON.parse(line); // throws on partial/corrupt line
    assert.strictEqual(typeof obj, 'object');
    assert(obj !== null);
    if (obj.ts !== undefined) {
      assert.strictEqual(typeof obj.ts, 'number');
    }
    assert.strictEqual(typeof obj.text, 'string');
  }
  return lines.length;
}

// 1. default 24h retention: old entries dropped, recent + exactly-at-cutoff kept
test('pruneFile drops entries older than 24h (default), keeps boundary', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 200, history_retention_hours: 24 });
  const now = Date.now();
  const old = makeEntry(now - 25 * HOUR, 'old entry');
  const boundary = makeEntry(now - DAY, 'boundary entry'); // exactly 24h old -> kept
  const recent = makeEntry(now - 1 * HOUR, 'recent entry');
  fs.writeFileSync(
    path.join(dir, 'history.jsonl'),
    [old, boundary, recent].map((e) => JSON.stringify(e)).join('\n') + '\n'
  );
  const removed = history.pruneFile(now);
  assert.strictEqual(removed, 1, 'exactly one old entry removed');
  const count = assertWellFormed(dir);
  assert.strictEqual(count, 2, 'recent + boundary survive');
  const texts = readLines(dir).map((l) => JSON.parse(l).text);
  assert(texts.includes('boundary entry'));
  assert(texts.includes('recent entry'));
  assert(!texts.includes('old entry'));
  assert(!fs.existsSync(path.join(dir, 'history.jsonl.tmp')), 'no leftover temp file');
});

// 2. retention window comes from config.history_retention_hours
test('pruneFile honors configured retention hours (6h)', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 200, history_retention_hours: 6 });
  const now = Date.now();
  const old = makeEntry(now - 10 * HOUR, 'ten hours old');
  const recent = makeEntry(now - 2 * HOUR, 'two hours old');
  fs.writeFileSync(
    path.join(dir, 'history.jsonl'),
    [old, recent].map((e) => JSON.stringify(e)).join('\n') + '\n'
  );
  const removed = history.pruneFile(now);
  assert.strictEqual(removed, 1);
  assertWellFormed(dir);
  assert.strictEqual(readLines(dir).length, 1);
  assert(JSON.parse(readLines(dir)[0]).text === 'two hours old');
});

// 3. missing config key falls back to 24h
test('prune falls back to 24h when history_retention_hours missing', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 200 });
  const now = Date.now();
  const kept = history.pruneEntries([makeEntry(now - 23 * HOUR, 'a'), makeEntry(now - 25 * HOUR, 'b')], now);
  assert.strictEqual(kept.length, 1);
  assert.strictEqual(kept[0].text, 'a');
});

// 4. corrupt/partial lines are skipped, malformed ts kept, file stays clean
test('pruneFile survives corrupt lines and keeps malformed-ts entries', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 200, history_retention_hours: 24 });
  const now = Date.now();
  const good = makeEntry(now - 1 * HOUR, 'good entry');
  const malformed = { id: 'm1', duration_ms: 5, text: 'no ts here' };
  fs.writeFileSync(
    path.join(dir, 'history.jsonl'),
    [
      '{"id":"broken","ts":', // truncated line (simulated partial write)
      JSON.stringify(good),
      JSON.stringify(malformed),
    ].join('\n') + '\n'
  );
  const removed = history.pruneFile(now);
  assert.strictEqual(removed, 1, 'the corrupt line is rewritten away');
  const count = assertWellFormed(dir);
  assert.strictEqual(count, 2, 'good + malformed-ts entries kept');
});

// 5. append enforces the time window AND the history_max cap (oldest first)
test('append prunes old entries and trims to history_max', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 2, history_retention_hours: 24 });
  const now = Date.now();
  const old = makeEntry(now - 25 * HOUR, 'stale');
  fs.writeFileSync(path.join(dir, 'history.jsonl'), JSON.stringify(old) + '\n');
  history.append({ duration_ms: 1000, text: 'first' });
  history.append({ duration_ms: 1000, text: 'second' });
  assertWellFormed(dir);
  const entries = readLines(dir).map((l) => JSON.parse(l));
  assert.strictEqual(entries.length, 2, 'capped at history_max=2');
  const texts = entries.map((e) => e.text);
  assert(!texts.includes('stale'), 'stale entry pruned by time window');
  assert.deepStrictEqual(texts, ['first', 'second'], 'oldest trimmed first');
});

// 6. list() prunes persistently, then returns newest first
test('list prunes old entries and returns newest first', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 200, history_retention_hours: 24 });
  const now = Date.now();
  const old = makeEntry(now - 30 * HOUR, 'ancient');
  const mid = makeEntry(now - 2 * HOUR, 'mid');
  const recent = makeEntry(now - 30 * 1000, 'fresh');
  fs.writeFileSync(
    path.join(dir, 'history.jsonl'),
    [old, mid, recent].map((e) => JSON.stringify(e)).join('\n') + '\n'
  );
  const listed = history.list();
  assert.strictEqual(listed.length, 2);
  assert.strictEqual(listed[0].text, 'fresh');
  assert.strictEqual(listed[1].text, 'mid');
  assert.strictEqual(readLines(dir).length, 2, 'prune persisted to file');
  // empty file after everything ages out
  history.setConfigRef({ history_max: 200, history_retention_hours: 24 });
  const gone = history.pruneFile(Date.now() + 3 * DAY);
  assert.strictEqual(gone, 2);
  assert.strictEqual(readLines(dir).length, 0);
  assertWellFormed(dir);
});

// 7. prune on an empty/missing file is a no-op
test('pruneFile on missing file is a no-op', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 200, history_retention_hours: 24 });
  assert.strictEqual(history.pruneFile(), 0);
  assert(!fs.existsSync(path.join(dir, 'history.jsonl.tmp')));
});

console.log(`\n${passed} tests passed`);
