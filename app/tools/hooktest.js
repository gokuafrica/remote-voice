'use strict';

// Standalone hook diagnostic: attaches uiohook-napi listeners on BOTH the
// correct string event names and the (incorrectly used) numeric EventType
// names, prints every event keycode, exits after 30s.

const { uIOhook, UiohookKey, EventType } = require('uiohook-napi');

function ts() { return new Date().toISOString().slice(11, 19); }
function print(tag, e) {
  if (!e) { console.log(`${ts()} [${tag}] <no event object>`); return; }
  console.log(`${ts()} [${tag}] keycode=0x${(e.keycode >>> 0).toString(16)}(${e.keycode})` +
    ` ctrl=${e.ctrlKey} alt=${e.altKey} shift=${e.shiftKey} meta=${e.metaKey}`);
}

// string-name listeners (the API actually used by uiohook-napi)
uIOhook.on('keydown', (e) => print('keydown-str', e));
uIOhook.on('keyup', (e) => print('keyup-str', e));
uIOhook.on('input', (e) => print('input', e));

// numeric-name listeners (what app/main/hotkey.js used to subscribe to)
uIOhook.on(EventType.EVENT_KEY_PRESSED, (e) => print('keydown-num4', e));
uIOhook.on(EventType.EVENT_KEY_RELEASED, (e) => print('keyup-num5', e));

uIOhook.start();
console.log(`${ts()} [hooktest] started; F9 code = ${UiohookKey.F9}; listening 30s...`);

setTimeout(() => {
  try { uIOhook.stop(); } catch (_) {}
  console.log(`${ts()} [hooktest] done`);
  process.exit(0);
}, 30000);
