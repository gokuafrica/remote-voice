'use strict';

// Dev-only Fake `window.remotevoice`. Loaded automatically by overlay.js and
// settings.js when the real preload bridge is missing (e.g. opening the HTML
// files directly in a browser). Never present in a packaged app.

(() => {
  if (typeof window === 'undefined' || window.remotevoice) return;

  const CONFIG = {
    hotkey: 'right ctrl',
    mode: 'toggle',
    mic_device: null,
    sample_rate: 16000,
    voice_model: 'nemo-parakeet-tdt-0.6b-v2',
    pronunciation_fixes: {
      'new lion': 'new line',
      newline: 'new line',
      coma: 'comma',
      endless: 'end list',
      deepformat: 'deep format',
      'd format': 'deep format',
      lnm: 'LLM',
      czech: 'check',
      colin: 'colon',
      clawed: 'claude',
      codecs: 'codex',
      eno: 'inu',
    },
    overlay_position: 'bottom',
    history_max: 200,
    ollama_url: 'http://localhost:11434',
    ollama_model: 'qwen2.5:3b',
    cleanup_prompt: '',
    use_llm: false,
  };

  const MICS = [
    { id: 'default', label: 'System Default' },
    { id: 'mic-mock-1', label: 'Microphone Array (Intel Smart Sound)' },
    { id: 'mic-mock-2', label: 'NVIDIA Broadcast (NVIDIA Broadcast)' },
  ];

  const NOW = Date.now();
  const HISTORY = [
    { id: 'h1', ts: NOW - 4 * 60 * 1000, duration_ms: 5200, text: 'Okay so the plan for the new line sprint is to ship the overlay first, then settings, then the engine hot reload.' },
    { id: 'h2', ts: NOW - 42 * 60 * 1000, duration_ms: 2400, text: 'Reminder: ask Colin about the codecs migration before Friday.' },
    { id: 'h3', ts: NOW - 3 * 3600 * 1000, duration_ms: 11000, text: 'The parakeet model runs at roughly 0.4 seconds end to end on CUDA, which is exactly what we wanted.' },
    { id: 'h4', ts: NOW - 26 * 3600 * 1000, duration_ms: 3300, text: 'Comma placement still feels off in long sentences, maybe worth an LLM pass later.' },
    { id: 'h5', ts: NOW - 6 * 24 * 3600 * 1000, duration_ms: 1800, text: 'Test after reinstall, mic array sounds much cleaner than the webcam mic.' },
  ];

  const stateListeners = new Set();
  const engineListeners = new Set();
  let mockState = 'hidden';
  let mockLevel = 0;
  let tickTimer = null;
  let phase = 0;

  function emit(listeners, payload) {
    for (const cb of listeners) {
      try { cb(payload); } catch (err) { console.error('[mock] listener failed', err); }
    }
  }

  function tick() {
    phase += 1;
    if (mockState === 'recording') {
      mockLevel = Math.min(1, Math.max(0.05, 0.45 + 0.4 * Math.sin(phase / 3) + (Math.random() - 0.5) * 0.35));
      emit(stateListeners, { state: 'recording', level: mockLevel });
    }
  }

  function ensureTicker() {
    if (tickTimer) return;
    tickTimer = setInterval(tick, 80);
  }

  window.remotevoice = {
    onOverlayState(cb) {
      stateListeners.add(cb);
      if (mockState === 'hidden') {
        // Demo cycle: hidden -> recording for ~6s -> processing for ~1.5s -> hidden.
        setTimeout(() => {
          mockState = 'recording';
          ensureTicker();
          emit(stateListeners, { state: 'recording', level: 0.3 });
          setTimeout(() => {
            mockState = 'processing';
            emit(stateListeners, { state: 'processing', level: 0 });
            setTimeout(() => {
              mockState = 'hidden';
              emit(stateListeners, { state: 'hidden', level: 0 });
            }, 1500);
          }, 6000);
        }, 700);
      }
      return () => stateListeners.delete(cb);
    },

    overlayCancel() {
      mockState = 'hidden';
      emit(stateListeners, { state: 'hidden', level: 0 });
    },

    async settingsGet() {
      return JSON.parse(JSON.stringify(CONFIG));
    },

    async settingsSave(config) {
      Object.assign(CONFIG, config);
      return true;
    },

    async listMics() {
      return MICS.map((m) => ({ ...m }));
    },

    async historyList(query) {
      const q = (query || '').toLowerCase();
      const rows = q
        ? HISTORY.filter((h) => h.text.toLowerCase().includes(q))
        : HISTORY.slice();
      return rows.map((h) => ({ ...h }));
    },

    async historyDelete(id) {
      const i = HISTORY.findIndex((h) => h.id === id);
      if (i >= 0) HISTORY.splice(i, 1);
      return true;
    },

    onEngineStatus(cb) {
      engineListeners.add(cb);
      setTimeout(() => emit(engineListeners, { ready: true, model: CONFIG.voice_model, error: null }), 400);
      return () => engineListeners.delete(cb);
    },
  };

  console.info('[remotevoice] using dev mock bridge');
})();
