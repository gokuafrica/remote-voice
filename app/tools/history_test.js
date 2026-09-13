'use strict';

// Node-only test for history capping (rotating last-N). Run:
// `node app/tools/history_test.js`
// (no Electron needed — history.js only touches the electron `app` path when
// no data dir override is set, and the tests always set one).

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const history = require('../main/history');

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

function seedFile(dir, entries) {
  fs.writeFileSync(
    path.join(dir, 'history.jsonl'),
    entries.map((e) => JSON.stringify(e)).join('\n') + '\n'
  );
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

// 1. pure cap: keeps the newest N, drops oldest first
test('capEntries keeps last N entries and rotates oldest out', () => {
  const entries = [1, 2, 3, 4, 5].map((i) => makeEntry(i, `entry ${i}`));
  const capped = history.capEntries(entries, 3);
  assert.deepStrictEqual(capped.map((e) => e.text), ['entry 3', 'entry 4', 'entry 5']);
});

// 2. cap below 1 is clamped to 1
test('capEntries clamps cap to at least 1', () => {
  const entries = [1, 2, 3].map((i) => makeEntry(i, `entry ${i}`));
  const capped = history.capEntries(entries, 0);
  assert.strictEqual(capped.length, 1);
  assert.strictEqual(capped[0].text, 'entry 3');
});

// 3. 51st append evicts the oldest (default cap 50 from config)
test('append at cap 50 evicts the oldest entry', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 50 });
  const now = Date.now();
  seedFile(dir, Array.from({ length: 50 }, (_, i) => makeEntry(now - (50 - i) * 1000, `entry ${i}`)));
  history.append({ duration_ms: 1000, text: 'the 51st' });
  assertWellFormed(dir);
  const lines = readLines(dir);
  assert.strictEqual(lines.length, 50, 'still exactly 50 entries');
  const texts = lines.map((l) => JSON.parse(l).text);
  assert(!texts.includes('entry 0'), 'oldest entry rotated out');
  assert.strictEqual(texts[texts.length - 1], 'the 51st', 'newest entry is last on disk');
  assert(!fs.existsSync(path.join(dir, 'history.jsonl.tmp')), 'no leftover temp file');
});

// 4. append trims to a smaller configured cap
test('append trims to configured cap (3)', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 3 });
  history.append({ duration_ms: 1000, text: 'first' });
  history.append({ duration_ms: 1000, text: 'second' });
  history.append({ duration_ms: 1000, text: 'third' });
  history.append({ duration_ms: 1000, text: 'fourth' });
  assertWellFormed(dir);
  const texts = readLines(dir).map((l) => JSON.parse(l).text);
  assert.deepStrictEqual(texts, ['second', 'third', 'fourth'], 'oldest rotated out first');
});

// 5. missing config falls back to the 50-entry default cap
test('append falls back to default cap 50 without config', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({});
  seedFile(dir, Array.from({ length: 50 }, (_, i) => makeEntry(i, `entry ${i}`)));
  history.append({ duration_ms: 1000, text: 'newest' });
  assertWellFormed(dir);
  const lines = readLines(dir);
  assert.strictEqual(lines.length, 50);
  assert(!lines.map((l) => JSON.parse(l).text).includes('entry 0'), 'default cap rotated oldest out');
});

// 6. corrupt/partial lines are rewritten away on the next append,
//    malformed-ts entries survive (time never prunes them)
test('append survives corrupt lines and keeps malformed-ts entries', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 50 });
  const malformed = { id: 'm1', duration_ms: 5, text: 'no ts here' };
  fs.writeFileSync(
    path.join(dir, 'history.jsonl'),
    [
      '{"id":"broken","ts":', // truncated line (simulated partial write)
      JSON.stringify(malformed),
    ].join('\n') + '\n'
  );
  history.append({ duration_ms: 1000, text: 'fresh' });
  const count = assertWellFormed(dir);
  assert.strictEqual(count, 2, 'corrupt line rewritten away, rest kept');
  const texts = readLines(dir).map((l) => JSON.parse(l).text);
  assert(texts.includes('no ts here'), 'malformed-ts entry kept — no time-based pruning');
  assert(texts.includes('fresh'));
});

// 7. old entries are never dropped: only count is bounded
test('list keeps entries older than any time window', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 50 });
  const now = Date.now();
  seedFile(dir, [
    makeEntry(now - 30 * 24 * 3600 * 1000, 'a month old'),
    makeEntry(now - 30 * 1000, 'fresh'),
  ]);
  const listed = history.list();
  assert.strictEqual(listed.length, 2, 'no time-based retention');
  assert.strictEqual(listed[0].text, 'fresh', 'newest first');
  assert.strictEqual(listed[1].text, 'a month old');
});

// 8. list persists a trim when the configured cap shrank below file size
test('list trims file when config cap shrank', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 10 });
  seedFile(dir, Array.from({ length: 50 }, (_, i) => makeEntry(i, `entry ${i}`)));
  const listed = history.list();
  assert.strictEqual(listed.length, 10);
  assert.strictEqual(readLines(dir).length, 10, 'trim persisted');
  assert.strictEqual(JSON.parse(readLines(dir)[0]).text, 'entry 40', 'newest 10 kept');
});

// 9. remove() still works and keeps the cap
test('remove deletes an entry by id', () => {
  const dir = tmpDir();
  history.setDataDir(dir);
  history.setConfigRef({ history_max: 50 });
  const a = makeEntry(1, 'alpha');
  const b = makeEntry(2, 'beta');
  seedFile(dir, [a, b]);
  assert.strictEqual(history.remove(a.id), true);
  assert.strictEqual(history.remove('missing'), false);
  const texts = readLines(dir).map((l) => JSON.parse(l).text);
  assert.deepStrictEqual(texts, ['beta']);
  assertWellFormed(dir);
});

console.log(`\n${passed} tests passed`);
