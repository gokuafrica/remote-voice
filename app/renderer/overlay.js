'use strict';

(() => {
  if (!window.spokenly) {
    // Dev/browser mode: load the mock bridge synchronously via XHR so the
    // rest of this file runs identically to the real Electron environment.
    try {
      const xhr = new XMLHttpRequest();
      xhr.open('GET', 'mock.js', false); // sync, same-directory, dev only
      xhr.send();
      if (xhr.status === 200) {
        // eslint-disable-next-line no-new-func
        new Function(xhr.responseText)();
      }
    } catch (err) {
      console.error('[overlay] failed to load mock bridge:', err);
    }
  }

  if (!window.spokenly) {
    console.error('[overlay] no spokenly bridge and no mock; overlay idle');
    return;
  }

  const api = window.spokenly;

  const body = document.body;
  const pill = document.getElementById('pill');
  const bars = document.getElementById('bars');
  const label = document.getElementById('pill-label');

  for (let i = 0; i < 7; i += 1) bars.appendChild(document.createElement('i'));
  const barEls = Array.from(bars.children);

  // Click-guard: ignore clicks within 150ms of the pill appearing so a stray
  // hotkey-release click cannot cancel a recording instantly.
  let shownAt = 0;

  // Smooth level decay — bars ease toward the target instead of snapping,
  // so the visual never looks jerky even with uneven level updates.
  const SMOOTH_UP = 0.45; // fast attack
  const SMOOTH_DOWN = 0.12; // slow release
  const IDLE_FLOOR = 0.05;
  let target = 0;
  let current = 0;
  let rafId = 0;

  const raf = typeof window.requestAnimationFrame === 'function'
    ? window.requestAnimationFrame.bind(window)
    : (cb) => setTimeout(() => cb(performance.now()), 16);
  const rafCancel = typeof window.cancelAnimationFrame === 'function'
    ? window.cancelAnimationFrame.bind(window)
    : clearTimeout;

  function paint() {
    current += (target - current) * (target > current ? SMOOTH_UP : SMOOTH_DOWN);
    const lvl = Math.max(IDLE_FLOOR, current);
    body.style.setProperty('--lvl', lvl.toFixed(3));
    for (const el of barEls) el.style.setProperty('--lvl', lvl.toFixed(3));
    if (target > IDLE_FLOOR || current > 0.004) {
      rafId = raf(paint);
    } else {
      current = 0;
      rafId = 0;
    }
  }

  function ensureRaf() {
    if (!rafId) rafId = raf(paint);
  }

  function show(state) {
    pill.classList.remove('hidden', 'processing');
    if (state === 'processing') pill.classList.add('processing');
    body.classList.add('visible');
    shownAt = performance.now();
  }

  function hide() {
    pill.classList.remove('processing');
    pill.classList.add('hidden');
    body.classList.remove('visible');
    target = 0;
    ensureRaf();
  }

  api.onOverlayState(({ state, level }) => {
    if (state === 'recording') {
      label.textContent = 'Listening…';
      show('recording');
      target = Math.min(1, Math.max(0, Number(level) || 0));
      ensureRaf();
    } else if (state === 'processing') {
      label.textContent = 'Transcribing…';
      target = 0;
      ensureRaf();
      show('processing');
    } else {
      hide();
    }
  });

  pill.addEventListener('click', () => {
    if (performance.now() - shownAt < 150) return; // click-guard
    api.overlayCancel();
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      api.overlayCancel();
    }
  });
})();
