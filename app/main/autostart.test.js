'use strict';

const assert = require('assert');
const { sync } = require('./autostart');

function makeApp(isPackaged) {
  const calls = [];
  return {
    isPackaged,
    getAppPath: () => 'C:\\src\\remote-voice\\app',
    setLoginItemSettings: (options) => calls.push(options),
    calls,
  };
}

{
  const app = makeApp(true);
  const result = sync(app, true, { platform: 'win32', execPath: 'C:\\Program Files\\Remote Voice\\Remote Voice.exe' });
  assert.deepStrictEqual(result.options, {
    openAtLogin: true,
    path: 'C:\\Program Files\\Remote Voice\\Remote Voice.exe',
  });
  assert.deepStrictEqual(app.calls, [result.options]);
}

{
  const app = makeApp(false);
  const result = sync(app, false, { platform: 'win32', execPath: 'C:\\node_modules\\electron.exe' });
  assert.strictEqual(result.enabled, false);
  assert.deepStrictEqual(app.calls[0], {
    openAtLogin: false,
    path: 'C:\\node_modules\\electron.exe',
    args: ['C:\\src\\remote-voice\\app'],
  });
}

{
  const app = makeApp(true);
  const result = sync(app, true, { platform: 'linux', execPath: '/usr/bin/electron' });
  assert.deepStrictEqual(result, { supported: false, enabled: false });
  assert.deepStrictEqual(app.calls, []);
}

console.log('autostart tests passed');
