'use strict';

const koffi = require('koffi');
const { clipboard } = require('electron');

const user32 = koffi.load('user32.dll');

const MOUSEINPUT = koffi.struct('MOUSEINPUT', {
  dx: 'long', dy: 'long', mouseData: 'uint32', dwFlags: 'uint32',
  time: 'uint32', dwExtraInfo: 'uintptr_t',
});
const KEYBDINPUT = koffi.struct('KEYBDINPUT', {
  wVk: 'uint16', wScan: 'uint16', dwFlags: 'uint32',
  time: 'uint32', dwExtraInfo: 'uintptr_t',
});
const HARDWAREINPUT = koffi.struct('HARDWAREINPUT', {
  uMsg: 'uint32', wParamL: 'uint16', wParamH: 'uint16',
});
const INPUTUNION = koffi.union('SP_INPUTUNION', {
  mi: MOUSEINPUT, ki: KEYBDINPUT, hi: HARDWAREINPUT,
});
const INPUT = koffi.struct('SP_INPUT', { type: 'uint32', u: INPUTUNION });

const SendInput = user32.func('uint32 SendInput(uint32, SP_INPUT*, int32)');
const GetClipboardSequenceNumber = user32.func('uint32 GetClipboardSequenceNumber()');

const VK_CONTROL = 0x11;
const VK_V = 0x56;
const KEYEVENTF_KEYUP = 0x0002;
const INPUT_KEYBOARD = 1;

const OPEN_RETRIES = 5;
const RETRY_DELAY_MS = 20;

// NOTE: Electron >= 44 made the main-process clipboard API async
// (readText/writeText return Promises; no image API in main). Feeding a
// Promise object back into writeText is the "conversion failure" error.

function log(msg) {
  console.log(`[paste] ${msg}`);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function keyInput(vk, keyUp) {
  return {
    type: INPUT_KEYBOARD,
    u: { ki: { wVk: vk, wScan: 0, dwFlags: keyUp ? KEYEVENTF_KEYUP : 0, time: 0, dwExtraInfo: 0 } },
  };
}

function sendCtrlV() {
  const inputs = [
    keyInput(VK_CONTROL, false),
    keyInput(VK_V, false),
    keyInput(VK_V, true),
    keyInput(VK_CONTROL, true),
  ];
  const sent = SendInput(inputs.length, inputs, koffi.sizeof(INPUT));
  if (sent !== inputs.length) {
    log(`SendInput sent ${sent}/${inputs.length}`);
    return false;
  }
  return true;
}

async function retry(action, what) {
  let lastErr = null;
  for (let attempt = 0; attempt < OPEN_RETRIES; attempt++) {
    try {
      return await action();
    } catch (e) {
      lastErr = e;
      if (attempt < OPEN_RETRIES - 1) await sleep(RETRY_DELAY_MS * (attempt + 1));
    }
  }
  log(`${what} failed after ${OPEN_RETRIES} attempts: ${lastErr && lastErr.message}`);
  return undefined;
}

function seq() {
  try {
    return GetClipboardSequenceNumber();
  } catch (e) {
    log(`sequence unavailable: ${e.message}`);
    return 0;
  }
}

async function snapshotClipboard() {
  const snap = { text: null };
  try {
    const text = await clipboard.readText();
    if (text) snap.text = text;
  } catch (e) {
    log(`clipboard read failed: ${e.message}`);
  }
  return snap;
}

async function restoreClipboard(snap) {
  const done = await retry(async () => {
    if (snap.text !== null) await clipboard.writeText(snap.text);
    else await clipboard.clear();
    return true;
  }, 'clipboard restore');
  if (done) log('clipboard restored');
  return !!done;
}

/**
 * Paste text at the cursor via clipboard + Ctrl+V, preserving clipboard content.
 * Never throws.
 */
async function pasteText(text, { preDelayMs = 250 } = {}) {
  try {
    if (!text) {
      log('nothing to paste');
      return false;
    }
    if (preDelayMs > 0) await sleep(preDelayMs);

    const seqBefore = seq();
    const snap = await snapshotClipboard();

    const wrote = await retry(async () => {
      await clipboard.writeText(text);
      return true;
    }, 'clipboard write');
    if (!wrote) {
      log('could not write clipboard — aborting paste');
      return false;
    }
    const seqAfterWrite = seq();

    sendCtrlV();
    log(`pasted ${text.length} chars via clipboard`);
    await sleep(150);

    const seqNow = seq();
    if (seqAfterWrite && seqNow && seqNow !== seqAfterWrite) {
      log('clipboard changed during paste — skipping restore');
      return true;
    }
    await restoreClipboard(snap);
    return true;
  } catch (e) {
    log(`paste failed: ${e.message}`);
    return false;
  }
}

module.exports = { pasteText, sendCtrlV, _test: { INPUT, koffi } };
