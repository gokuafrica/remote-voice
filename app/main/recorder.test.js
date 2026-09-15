'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const Module = require('module');

// recorder.js only needs these Electron surfaces for construction. Mock them
// so this test exercises the real stop path without opening a window.
const originalLoad = Module._load;
Module._load = function load(request, parent, isMain) {
  if (request === 'electron') return { BrowserWindow: class {}, ipcMain: { on() {} } };
  return originalLoad.call(this, request, parent, isMain);
};

const recorder = require('./recorder');
Module._load = originalLoad;

async function main() {
  const tempDir = os.tmpdir();
  const before = new Set(fs.readdirSync(tempDir).filter((name) => /^remote-voice-\d+\.wav$/i.test(name)));
  const pcm = Buffer.from([0, 0, 0, 0, 0xff, 0x7f, 0x00, 0x80]);

  recorder.recording = true;
  recorder.frames = [pcm];
  const result = await recorder.stop();

  assert(result);
  assert.strictEqual(result.sampleRate, 16000);
  assert.deepStrictEqual(result.pcm, pcm);
  assert.strictEqual(result.bytes, pcm.length);

  const after = fs.readdirSync(tempDir).filter((name) => /^remote-voice-\d+\.wav$/i.test(name));
  assert.deepStrictEqual(after.filter((name) => !before.has(name)), []);
  console.log('recorder PCM test passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
