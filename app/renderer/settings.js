'use strict';

(() => {
  if (!window.remotevoice) {
    // Dev/browser mode: load the mock bridge synchronously via XHR so this
    // page renders standalone in a plain browser.
    try {
      const xhr = new XMLHttpRequest();
      xhr.open('GET', 'mock.js', false); // sync, same-directory, dev only
      xhr.send();
      if (xhr.status === 200) {
        // eslint-disable-next-line no-new-func
        new Function(xhr.responseText)();
      }
    } catch (err) {
      console.error('[settings] failed to load mock bridge:', err);
    }
  }

  if (!window.remotevoice) {
    console.error('[settings] no remotevoice bridge and no mock');
    return;
  }

  const api = window.remotevoice;
  const $ = (id) => document.getElementById(id);

  let config = null;

  // ---------------------------------------------------------------- helpers

  function toast(msg) {
    const el = $('toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.remove('show'), 1800);
  }

  // ------------------------------------------------------------- autosave

  // Every control change persists immediately: radios/dropdowns/toggles save
  // instantly, text inputs debounce. Each save always sends the full collected
  // config — idempotent, and matches the existing settingsSave contract.
  let saveTimer = null;
  let saving = false;
  let resave = false;

  function collect() {
    config.hotkey = $('hotkey-display').textContent;
    const mode = document.querySelector('input[name="mode"]:checked');
    if (mode) config.mode = mode.value;
    config.auto_start = $('auto-start').checked;
    config.mic_device = $('mic-select').value === 'default' ? null : $('mic-select').value;
    config.use_llm = $('use-llm').checked;
    config.ollama_url = $('ollama-url').value.trim();
    config.ollama_model = $('ollama-model').value.trim();
    config.pronunciation_fixes = fixesToObject();
    return { ...config };
  }

  async function saveAll() {
    if (!config) return;
    if (saving) {
      resave = true; // a change landed mid-save — run once more after
      return;
    }
    saving = true;
    try {
      await api.settingsSave(collect());
      toast('Saved ✓');
    } catch (err) {
      console.error('[settings] save failed', err);
      toast('Save failed — see console');
    } finally {
      saving = false;
      if (resave) {
        resave = false;
        scheduleSave(50);
      }
    }
  }

  function scheduleSave(delay = 400) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      saveTimer = null;
      await saveAll();
    }, delay);
  }

  function flushSave() {
    if (!saveTimer) return Promise.resolve();
    clearTimeout(saveTimer);
    saveTimer = null;
    return saveAll();
  }

  // ------------------------------------------------------------ navigation

  const nav = $('nav');
  nav.addEventListener('click', async (e) => {
    const btn = e.target.closest('.nav-item');
    if (!btn) return;
    await flushSave(); // commit any debounced edit before leaving the section
    for (const item of nav.children) item.classList.toggle('active', item === btn);
    for (const panel of document.querySelectorAll('.panel')) {
      panel.classList.toggle('active', panel.id === `section-${btn.dataset.section}`);
    }
    if (btn.dataset.section === 'history') refreshHistory();
    if (btn.dataset.section === 'microphone') refreshMics();
  });

  // ---------------------------------------------------------------- engine

  function renderEngineStatus(payload) {
    const { ready, model, error } = payload || {};
    const ind = $('engine-indicator');
    const status = $('engine-status');
    if (ready) {
      ind.textContent = 'ready';
      ind.className = 'engine-indicator ok';
      $('engine-model').textContent = model || 'unknown model';
      status.textContent = `Engine ready — ${model || 'model loaded'}`;
    } else if (error) {
      ind.textContent = 'error';
      ind.className = 'engine-indicator err';
      $('engine-model').textContent = error;
      status.textContent = `Engine error — ${error}`;
    } else {
      ind.textContent = 'loading';
      ind.className = 'engine-indicator';
      $('engine-model').textContent = 'Loading model…';
      status.textContent = 'Engine: loading model…';
    }
  }

  api.onEngineStatus(renderEngineStatus);

  // ---------------------------------------------------------------- hotkey

  function renderHotkey() {
    $('hotkey-display').textContent = config.hotkey || 'not set';
  }

  const captureBtn = $('hotkey-capture');
  let capturing = false;

  function prettyKey(e) {
    const key = e.key;
    const lower = key.toLowerCase();
    if (key === 'Control') return `${e.location === 2 ? 'right ' : 'left '}ctrl`;
    if (key === 'Shift') return `${e.location === 2 ? 'right ' : 'left '}shift`;
    if (key === 'Alt') return `${e.location === 2 ? 'right ' : 'left '}alt`;
    if (key === 'Meta') return 'win';
    if (key.startsWith('Arrow')) return lower;
    if (key === ' ') return 'space';
    if (key === 'Escape') return null; // Esc cancels capture
    if (key.length === 1) return lower;
    return lower; // F1..F12, Home, numpad keys, etc. keep names lowercase
  }

  function hotkeyToString(e) {
    const parts = [];
    if (e.ctrlKey && e.key !== 'Control') parts.push(`${e.location === 2 ? 'right ' : 'left '}ctrl`);
    if (e.altKey && e.key !== 'Alt') parts.push(`${e.location === 2 ? 'right ' : 'left '}alt`);
    if (e.shiftKey && e.key !== 'Shift') parts.push(`${e.location === 2 ? 'right ' : 'left '}shift`);
    if (e.metaKey && e.key !== 'Meta') parts.push('win');
    const main = prettyKey(e);
    if (!main) return null;
    parts.push(main);
    return parts.join(' + ').replace(/\s+/g, ' ');
  }

  function startCapture() {
    if (capturing) return;
    capturing = true;
    document.body.classList.add('capturing-hotkey');
    captureBtn.textContent = 'Press a key… (Esc to cancel)';

    function onKeyDown(e) {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'Escape') return finish();
      const str = hotkeyToString(e);
      if (!str) return finish();
      $('hotkey-display').textContent = str;
      saveAll(); // instant: hotkey capture is a discrete action
      finish();
    }

    function finish() {
      capturing = false;
      document.body.classList.remove('capturing-hotkey');
      captureBtn.textContent = 'Record new hotkey';
      window.removeEventListener('keydown', onKeyDown, true);
    }

    window.addEventListener('keydown', onKeyDown, true);
  }

  captureBtn.addEventListener('click', startCapture);

  // ------------------------------------------------------------------ mode

  for (const radio of document.querySelectorAll('input[name="mode"]')) {
    radio.addEventListener('change', () => {
      if (radio.checked) saveAll(); // instant: discrete control
    });
  }

  $('auto-start').addEventListener('change', () => saveAll());

  // ------------------------------------------------------------------- LLM

  $('use-llm').addEventListener('change', () => {
    $('llm-collapse').classList.toggle('open', $('use-llm').checked);
    saveAll(); // instant: discrete control
  });

  for (const id of ['ollama-url', 'ollama-model']) {
    $(id).addEventListener('input', () => scheduleSave(400));
    $(id).addEventListener('blur', flushSave);
  }

  // ------------------------------------------------------------------- mic

  async function refreshMics() {
    const sel = $('mic-select');
    try {
      const mics = await api.listMics();
      const current = config.mic_device || null;
      sel.innerHTML = '';
      // "System Default" must always be present; the tray stores null for it
      const defOpt = document.createElement('option');
      defOpt.value = 'default';
      defOpt.textContent = 'System Default';
      if (!current) defOpt.selected = true;
      sel.appendChild(defOpt);
      // The main process identifies mics by LABEL (recorder matchDevice and
      // the tray both compare labels), so the option value is the label, not
      // the deviceId.
      for (const m of mics) {
        const opt = document.createElement('option');
        opt.value = m.label;
        opt.textContent = m.label;
        if (m.label && m.label === current) opt.selected = true;
        sel.appendChild(opt);
      }
      updateMicHint();
    } catch (err) {
      console.error('[settings] listMics failed', err);
      $('mic-hint').textContent = 'Could not list devices.';
    }
  }

  function updateMicHint() {
    const sel = $('mic-select');
    const opt = sel.selectedOptions && sel.selectedOptions[0];
    $('mic-hint').textContent = opt
      ? `Test: input will use "${opt.textContent}".`
      : 'Test: select a device to see its name here.';
  }

  $('mic-select').addEventListener('change', () => {
    updateMicHint();
    saveAll(); // instant: dropdown
  });

  // ------------------------------------------------------- replacement words

  const fixRows = $('fix-rows');

  function fixesToObject() {
    const obj = {};
    for (const row of fixRows.querySelectorAll('.fix-row')) {
      const [wrongEl, correctEl] = row.querySelectorAll('input');
      const wrong = wrongEl.value.trim();
      const correct = correctEl.value.trim();
      if (wrong && correct && !wrongEl.classList.contains('dupe')) obj[wrong] = correct;
    }
    return obj;
  }

  function isDupe(value, exceptEl) {
    const v = value.trim().toLowerCase();
    if (!v) return false;
    for (const row of fixRows.querySelectorAll('.fix-row')) {
      const wrongEl = row.querySelector('input');
      if (wrongEl === exceptEl) continue;
      if (wrongEl.value.trim().toLowerCase() === v) return true;
    }
    return false;
  }

  function validateRow(wrongEl) {
    const dupe = isDupe(wrongEl.value, wrongEl);
    wrongEl.classList.toggle('dupe', dupe);
    wrongEl.title = dupe ? 'Duplicate key — this row will be skipped on save' : '';
  }

  function addFixRow(wrong = '', correct = '') {
    const row = document.createElement('div');
    row.className = 'fix-row';

    const wrongIn = document.createElement('input');
    wrongIn.type = 'text';
    wrongIn.placeholder = 'e.g. new lion';
    wrongIn.spellcheck = false;
    wrongIn.value = wrong;

    const correctIn = document.createElement('input');
    correctIn.type = 'text';
    correctIn.placeholder = 'e.g. new line';
    correctIn.spellcheck = false;
    correctIn.value = correct;

    const del = document.createElement('button');
    del.className = 'btn danger';
    del.textContent = '✕';
    del.title = 'Delete pair';
    del.setAttribute('aria-label', 'Delete pair');

    wrongIn.addEventListener('input', () => { validateRow(wrongIn); scheduleSave(400); });
    correctIn.addEventListener('input', () => scheduleSave(400));
    del.addEventListener('click', () => {
      row.remove();
      if (!fixRows.children.length) renderFixEmpty();
      saveAll(); // instant: discrete action
    });

    row.append(wrongIn, correctIn, del);
    fixRows.appendChild(row);
    const empty = fixRows.querySelector('.fix-empty');
    if (empty) empty.remove();
    return row;
  }

  function renderFixEmpty() {
    const div = document.createElement('div');
    div.className = 'fix-empty';
    div.textContent = 'No replacement words yet. Click "+ Add pair" or use bulk import below.';
    fixRows.appendChild(div);
  }

  function renderFixes(fixes) {
    fixRows.innerHTML = '';
    const entries = Object.entries(fixes || {});
    if (!entries.length) {
      renderFixEmpty();
      return;
    }
    for (const [wrong, correct] of entries) addFixRow(wrong, String(correct));
  }

  $('fix-add').addEventListener('click', () => {
    const row = addFixRow();
    row.querySelector('input').focus();
  });

  $('import-btn').addEventListener('click', () => {
    const text = $('import-text').value;
    let added = 0;
    for (const line of text.split(/\r?\n/)) {
      const m = line.match(/^\s*(.+?)\s*=\s*(.+?)\s*$/);
      if (!m) continue;
      const wrong = m[1].replace(/^["']|["']$/g, '');
      const correct = m[2].replace(/^["']|["']$/g, '');
      if (!wrong || !correct) continue;
      const row = addFixRow(wrong, correct);
      validateRow(row.querySelector('input'));
      added += 1;
    }
    $('import-text').value = '';
    if (added) {
      toast(`Imported ${added} pair${added === 1 ? '' : 's'}`);
      saveAll(); // instant: bulk action
    } else {
      toast('No valid `wrong = correct` lines found');
    }
  });

  // --------------------------------------------------------------- history

  function relTime(ts) {
    const diff = Date.now() - ts;
    const s = Math.floor(diff / 1000);
    if (s < 45) return 'just now';
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    if (d < 7) return `${d}d ago`;
    return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function fmtDur(ms) {
    const s = Math.round(ms / 1000);
    if (s < 60) return `${s}s`;
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  }

  async function refreshHistory() {
    const list = $('history-list');
    const q = $('history-search').value.trim();
    let rows;
    try {
      rows = await api.historyList(q || undefined);
    } catch (err) {
      console.error('[settings] historyList failed', err);
      return;
    }
    list.innerHTML = '';
    $('history-empty').classList.toggle('hidden', rows.length > 0);
    for (const h of rows) {
      const row = document.createElement('div');
      row.className = 'hist-row';

      const meta = document.createElement('div');
      meta.className = 'hist-meta';
      const time = document.createElement('span');
      time.className = 'hist-time';
      time.textContent = relTime(h.ts);
      time.title = new Date(h.ts).toLocaleString();
      const dur = document.createElement('span');
      dur.className = 'hist-dur';
      dur.textContent = fmtDur(h.duration_ms);
      meta.append(time, dur);

      const text = document.createElement('div');
      text.className = 'hist-text';
      text.textContent = h.text;

      const actions = document.createElement('div');
      actions.className = 'hist-actions';

      const copy = document.createElement('button');
      copy.className = 'btn ghost';
      copy.textContent = 'Copy';
      copy.title = 'Copy text';
      copy.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(h.text);
          copy.textContent = 'Copied';
          setTimeout(() => { copy.textContent = 'Copy'; }, 1200);
        } catch {
          toast('Copy failed');
        }
      });

      const del = document.createElement('button');
      del.className = 'btn danger';
      del.textContent = '✕';
      del.title = 'Delete entry';
      del.setAttribute('aria-label', 'Delete history entry');
      del.addEventListener('click', async () => {
        try {
          await api.historyDelete(h.id);
          row.remove();
          if (!list.children.length) $('history-empty').classList.remove('hidden');
          toast('Deleted');
        } catch {
          toast('Delete failed');
        }
      });

      actions.append(copy, del);
      row.append(meta, text, actions);
      list.appendChild(row);
    }
  }

  let searchTimer = 0;
  $('history-search').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(refreshHistory, 250); // debounced
  });

  // ------------------------------------------------------------ reset

  const resetModal = $('reset-modal');

  function openResetModal() {
    resetModal.classList.remove('hidden');
    $('reset-cancel').focus();
  }

  function closeResetModal() {
    resetModal.classList.add('hidden');
  }

  $('reset-btn').addEventListener('click', openResetModal);
  $('reset-cancel').addEventListener('click', closeResetModal);
  resetModal.addEventListener('click', (e) => {
    if (e.target === resetModal) closeResetModal();
  });

  $('reset-confirm').addEventListener('click', async () => {
    closeResetModal();
    try {
      await flushSave(); // never clobber a pending edit with stale values
      const defaults = await api.settingsDefaults();
      await api.settingsSave({ ...defaults }); // same persistence path as autosave
      config = await api.settingsGet();
      renderConfig();
      await refreshMics(); // re-select device from the new config (System Default)
      toast('Reset to defaults ✓');
    } catch (err) {
      console.error('[settings] reset failed', err);
      toast('Reset failed — see console');
    }
  });

  window.addEventListener('beforeunload', () => {
    if (saveTimer) flushSave(); // best-effort flush of a debounced edit
  });

  // ------------------------------------------------------------------ init

  function renderConfig() {
    renderHotkey();
    const modeInput = document.querySelector(`input[name="mode"][value="${config.mode === 'push_to_talk' ? 'push_to_talk' : 'toggle'}"]`);
    if (modeInput) modeInput.checked = true;
    $('auto-start').checked = !!config.auto_start;
    $('use-llm').checked = !!config.use_llm;
    $('llm-collapse').classList.toggle('open', !!config.use_llm);
    $('ollama-url').value = config.ollama_url || '';
    $('ollama-model').value = config.ollama_model || '';
    $('sample-rate').textContent = `${config.sample_rate || 16000} Hz`;
    const cap = Number(config.history_max);
    $('history-hint').textContent = `History keeps the last ${Number.isFinite(cap) && cap > 0 ? Math.round(cap) : 50} dictations.`;
    renderFixes(config.pronunciation_fixes);
  }

  async function init() {
    config = await api.settingsGet();
    renderConfig();
    // pull the current engine status so a window opened after the engine
    // became ready shows the true state instead of the static placeholder
    if (typeof api.engineStatusGet === 'function') {
      try {
        renderEngineStatus(await api.engineStatusGet());
      } catch (err) {
        console.error('[settings] engineStatusGet failed', err);
      }
    }
    await refreshMics();
    refreshHistory();
  }

  init().catch((err) => console.error('[settings] init failed', err));
})();
