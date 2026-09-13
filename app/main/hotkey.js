'use strict';

const { uIOhook, UiohookKey } = require('uiohook-napi');

const DEBOUNCE_MS = 30;
const HOTKEY_LOG_THROTTLE_MS = 1000;

function log(msg) {
  console.log(`[hotkey] ${msg}`);
}

const PHRASE_ALIASES = [
  ['right ctrl', 'ctrlright'], ['ctrl right', 'ctrlright'],
  ['left ctrl', 'ctrlleft'], ['ctrl left', 'ctrlleft'],
  ['right alt', 'altright'], ['alt right', 'altright'],
  ['left alt', 'altleft'], ['alt left', 'altleft'],
  ['right shift', 'shiftright'], ['shift right', 'shiftright'],
  ['left shift', 'shiftleft'], ['shift left', 'shiftleft'],
  ['right meta', 'metaright'], ['meta right', 'metaright'],
  ['left meta', 'metaleft'], ['meta left', 'metaleft'],
  ['right win', 'metaright'], ['left win', 'metaleft'],
  ['escape', 'esc'],
];

function normalizeHotkeyString(hotkey) {
  let s = String(hotkey || '').trim().toLowerCase().replace(/control/g, 'ctrl');
  for (const [from, to] of PHRASE_ALIASES) s = s.split(from).join(to);
  return s;
}

function tokenToCodes(token) {
  const K = UiohookKey;
  const map = {
    ctrlright: [K.CtrlRight], ctrlleft: [K.Ctrl], ctrl: [K.Ctrl, K.CtrlRight],
    altright: [K.AltRight], altleft: [K.Alt], alt: [K.Alt, K.AltRight],
    shiftright: [K.ShiftRight], shiftleft: [K.Shift], shift: [K.Shift, K.ShiftRight],
    metaright: [K.MetaRight], metaleft: [K.Meta], meta: [K.Meta, K.MetaRight],
    win: [K.Meta, K.MetaRight], cmd: [K.Meta, K.MetaRight],
    esc: [K.Escape], space: [K.Space], enter: [K.Enter], tab: [K.Tab],
    backspace: [K.Backspace], insert: [K.Insert], delete: [K.Delete], del: [K.Delete],
    home: [K.Home], end: [K.End], pageup: [K.PageUp], pagedown: [K.PageDown],
    printscreen: [K.PrintScreen], capslock: [K.CapsLock], numlock: [K.NumLock],
  };
  if (map[token]) return map[token];
  const fMatch = /^f(\d{1,2})$/.exec(token);
  if (fMatch) {
    const n = parseInt(fMatch[1], 10);
    if (n >= 1 && n <= 24 && K[`F${n}`] !== undefined) return [K[`F${n}`]];
  }
  if (/^[a-z]$/.test(token) && K[token.toUpperCase()] !== undefined) return [K[token.toUpperCase()]];
  if (/^[0-9]$/.test(token)) {
    const digitMap = { 0: 'Numpad0' }; // digits row not in UiohookKey; ignore unknown
    void digitMap;
    return [];
  }
  // UiohookKey named punctuation entries
  const punct = {
    semicolon: 'Semicolon', equal: 'Equal', comma: 'Comma', minus: 'Minus',
    period: 'Period', slash: 'Slash', backquote: 'Backquote',
    bracketleft: 'BracketLeft', backslash: 'Backslash', bracketright: 'BracketRight',
    quote: 'Quote',
  };
  if (punct[token] && K[punct[token]] !== undefined) return [K[punct[token]]];
  return [];
}

function parseHotkey(hotkeyString) {
  const norm = normalizeHotkeyString(hotkeyString);
  const tokens = norm.split(/[\s+]+/).filter(Boolean);
  if (tokens.length === 0) {
    log(`empty hotkey string, falling back to right ctrl`);
    return parseHotkey('right ctrl');
  }
  const groups = tokens.map(tokenToCodes);
  const missing = tokens.filter((t, i) => groups[i].length === 0);
  if (missing.length) {
    log(`unknown hotkey token(s): ${missing.join(', ')} — hotkey disabled`);
    return null;
  }
  return groups; // e.g. [[ctrlCodes], [rCodes]]; last group is the trigger key
}

class Hotkey {
  constructor() {
    this.groups = null;
    this.raw = null;
    this.mode = 'toggle';
    this.pressed = new Set();
    this.active = false;
    this.lastActivate = 0;
    this.lastHotkeyLog = 0;
    this.handlers = null;
    this.running = false;
  }

  register(config, handlers) {
    this.handlers = handlers;
    this.applyConfig(config);
    if (!this.running) {
      // uiohook-napi's emitter only ever emits the string event names
      // 'input'/'keydown'/'keyup' (its handler() maps e.type to names);
      // subscribing with numeric EventType values never fires.
      uIOhook.on('keydown', (e) => this._onKey(e, true));
      uIOhook.on('keyup', (e) => this._onKey(e, false));
      try {
        uIOhook.start();
        this.running = true;
        log('global keyboard hook started (keydown/keyup listeners attached)');
      } catch (e) {
        this.running = false;
        log(`global keyboard hook FAILED to start: ${e.message}`);
      }
    }
  }

  applyConfig(configOrModule) {
    const cfg = configOrModule && typeof configOrModule.get === 'function'
      ? configOrModule.get()
      : (configOrModule || {});
    this.raw = cfg.hotkey;
    this.mode = cfg.mode === 'push_to_talk' ? 'push_to_talk' : 'toggle';
    this.groups = parseHotkey(cfg.hotkey);
    if (this.groups) {
      log(`hotkey '${cfg.hotkey}' -> groups ${JSON.stringify(this.groups)}, mode=${this.mode}`);
    }
    // config changed while active: reset edge state
    this.active = false;
  }

  isHeld() {
    if (!this.groups) return false;
    return this._allGroupsSatisfied();
  }

  _allGroupsSatisfied() {
    return this.groups.every((g) => g.some((c) => this.pressed.has(c)));
  }

  _groupSatisfiedByEvent(group, e) {
    if (group.some((c) => this.pressed.has(c))) return true;
    if (group.includes(UiohookKey.Ctrl) || group.includes(UiohookKey.CtrlRight)) {
      if (e.ctrlKey) return true;
    }
    if (group.includes(UiohookKey.Alt) || group.includes(UiohookKey.AltRight)) {
      if (e.altKey) return true;
    }
    if (group.includes(UiohookKey.Shift) || group.includes(UiohookKey.ShiftRight)) {
      if (e.shiftKey) return true;
    }
    if (group.includes(UiohookKey.Meta) || group.includes(UiohookKey.MetaRight)) {
      if (e.metaKey) return true;
    }
    return false;
  }

  _matches(e) {
    if (!this.groups) return false;
    const keyGroup = this.groups[this.groups.length - 1];
    if (!keyGroup.includes(e.keycode)) return false;
    // all modifier groups must be held
    for (let i = 0; i < this.groups.length - 1; i++) {
      if (!this._groupSatisfiedByEvent(this.groups[i], e)) return false;
    }
    return true;
  }

  _onKey(e, isDown) {
    if (!this.groups || !this.handlers) return;

    if (isDown) this.pressed.add(e.keycode);
    else this.pressed.delete(e.keycode);

    // throttled debug: any key event whose keycode participates in the combo
    const isHotkeyKey = this.groups.some((g) => g.includes(e.keycode));
    if (isHotkeyKey) {
      const now = Date.now();
      if (now - this.lastHotkeyLog >= HOTKEY_LOG_THROTTLE_MS) {
        this.lastHotkeyLog = now;
        log(`${isDown ? 'down' : 'up'} keycode=${e.keycode} held=[${[...this.pressed].join(',')}]`);
      }
    }

    // Esc cancels an active recording
    if (isDown && e.keycode === UiohookKey.Escape) {
      if (this.handlers.onCancel) this.handlers.onCancel('esc');
    }

    const matches = this._matches(e);
    const now = Date.now();

    if (isDown && matches && !this.active) {
      if (now - this.lastActivate >= DEBOUNCE_MS) {
        this.lastActivate = now;
        this.active = true;
        log(`activate (mode=${this.mode})`);
        if (this.handlers.onPress) this.handlers.onPress();
      }
    } else if (!isDown && this.active) {
      // release when the combo is no longer fully held
      if (!this._allGroupsSatisfied()) {
        this.active = false;
        log(`release`);
        if (this.handlers.onRelease) this.handlers.onRelease();
      }
    }
  }

  stop() {
    if (this.running) {
      try { uIOhook.stop(); } catch (e) { log(`stop failed: ${e.message}`); }
      this.running = false;
    }
  }
}

module.exports = new Hotkey();
