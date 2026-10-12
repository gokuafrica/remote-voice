'use strict';
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { EventEmitter } = require('events');
const { install } = require('./lifecycle');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rv-lifecycle-'));
const app = () => Object.assign(new EventEmitter(), { getVersion: () => 'test' });
const proc = (pid) => Object.assign(new EventEmitter(), { pid, ppid: 1 });
const rows = () => fs.readFileSync(path.join(dir, 'lifecycle.jsonl'), 'utf8').trim().split('\n').map(JSON.parse);
try {
  const firstApp = app(); const first = proc(10);
  install(firstApp, first, dir);
  // Simulate loss with no exit event, as occurs with OS termination.
  const secondApp = app(); const second = proc(20);
  install(secondApp, second, dir);
  assert(rows().some(r => r.event === 'previous-session-no-clean-exit' && r.previousPid === 10));
  second.emit('uncaughtExceptionMonitor', new Error('PRIVATE TRANSCRIPT'), 'uncaughtException');
  assert(!fs.readFileSync(path.join(dir, 'lifecycle.jsonl'), 'utf8').includes('PRIVATE TRANSCRIPT'));
  assert.strictEqual(second.listenerCount('uncaughtException'), 0);
  secondApp.emit('before-quit'); secondApp.emit('will-quit'); secondApp.emit('quit', {}, 0); second.emit('exit', 0);
  install(app(), proc(30), dir);
  assert.strictEqual(rows().filter(r => r.event === 'previous-session-no-clean-exit').length, 1);
  // An unwritable/invalid destination must not crash the real application.
  install(app(), proc(40), path.join(dir, 'lifecycle.jsonl', 'invalid'));
  console.log('lifecycle evidence tests passed');
} finally { fs.rmSync(dir, { recursive: true, force: true }); }
