'use strict';

const { EventEmitter } = require('events');

const STATES = ['IDLE', 'RECORDING', 'PROCESSING'];

const state = new EventEmitter();
state.current = 'IDLE';
state.level = 0;
state.lastError = null;

let _lastLevelSent = 0;

state.set = function (next, meta = {}) {
  if (!STATES.includes(next)) {
    console.log(`[state] invalid state ${next}`);
    return;
  }
  if (state.current === next) return;
  const prev = state.current;
  state.current = next;
  if (next !== 'RECORDING') state.level = 0;
  console.log(`[state] ${prev} -> ${next}${meta.reason ? ` (${meta.reason})` : ''}`);
  state.emit('change', next, prev, meta);
};

state.setLevel = function (level) {
  const v = Math.max(0, Math.min(1, Number(level) || 0));
  state.level = v;
  const now = Date.now();
  if (state.current === 'RECORDING' && now - _lastLevelSent >= 50) {
    _lastLevelSent = now;
    state.emit('level', v);
  }
};

state.is = function (s) {
  return state.current === s;
};

module.exports = state;
