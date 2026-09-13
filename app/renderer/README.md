# app/renderer — UI layer

Visible renderer files for the Remote Voice Electron app. No build step, no framework — plain HTML/CSS/JS.

## Files

| File | Window | Purpose |
|---|---|---|
| `overlay.html/css/js` | recording overlay | Frameless always-on-top pill (~240x60) shown near the cursor while dictating |
| `settings.html/css/js` | settings | Main settings window (~940x680) |
| `mock.js` | both (dev only) | Fake `window.remotevoice` bridge so the pages render standalone in a browser |

`recorder.*` (hidden recorder window) is owned by the shell agent — not part of the UI scope.

## Preload API contract (`window.remotevoice`)

Provided by `app/preload.js` (owned by the shell agent):

- `onOverlayState(cb)` — `cb({state:'recording'|'processing'|'hidden', level:0..1})`
- `overlayCancel()` — user pressed Esc or clicked the pill
- `settingsGet()` / `settingsSave(config)` / `settingsDefaults()` —
  `settingsDefaults()` resolves to a deep copy of the main-process DEFAULTS
  (from `app/main/config.js`); used by the Settings "Reset to defaults" button
- `listMics()` — `Promise<[{id,label}]>`
- `historyList(query)` / `historyDelete(id)`
- `onEngineStatus(cb)` — `cb({ready, model, error})` pushed on every engine
  state change
- `engineStatusGet()` — `Promise<{ready, model, error}>` snapshot of the
  latest engine status, cached in main. Settings calls this once on load so a
  window opened after the engine became ready shows the true state instead of
  the static "Loading" placeholder (push-only delivery would miss every
  transition that happened before the window existed).

If `window.remotevoice` is missing (dev/browser), `overlay.js` and `settings.js`
synchronously load `mock.js`, which installs a working fake bridge: a demo
overlay state cycle, fake config/mics/history, and a ready engine status.

## Overlay behaviour

- **recording**: dark glassy pill, red pulsing mic dot, 7 level bars driven by
  `level` with smooth attack/decay (requestAnimationFrame, no jerky updates).
- **processing**: blue accent, spinner, "Transcribing…".
- **hidden**: body opacity 0; the shell can also just hide the window.
- Esc and pill click call `overlayCancel()`; a 150ms click-guard after the pill
  appears prevents a stray hotkey-release click from cancelling instantly.
- State transitions are CSS-only / single-class toggles (<16ms).

## Settings sections

- **General** — hotkey display + "Record new hotkey" capture (temp capture-phase
  keydown listener; stores right/left variants via `e.location`, e.g. `right ctrl`);
  push-to-talk / toggle mode; engine status (model + ready indicator from
  `onEngineStatus`); "use LLM cleanup" toggle with collapsed Ollama URL/model inputs.
- **Microphone** — device dropdown (from `listMics()`, plus System Default) and a
  test hint showing the selected device name.
- **Replacement words** — table editor for `pronunciation_fixes` (`wrong → correct`),
  add/inline-edit/delete rows, duplicate-key detection, bulk import of
  `wrong = correct` lines. Matching semantics (case-insensitive word-boundary)
  live in the engine; the UI only states them.
- **History** — debounced searchable list (`historyList(query)`), relative time,
  duration, line-clamped text, copy + delete per row, empty state. A hint line
  states the cap ("History keeps the last 50 dictations.", driven by
  `config.history_max`).

## Save flow (autosave)

There is no Save button. Every control persists immediately through
`settingsSave(config)` — radios/dropdowns/toggles/discrete actions save
instantly, text inputs debounce (~400ms) and flush on blur or on section
switch. A "Saved ✓" toast confirms each autosave.

**Reset to defaults** (General section): in-page confirm modal →
`settingsDefaults()` fetches the main-process DEFAULTS → `settingsSave(defaults)`
writes them through the same persistence path (engine `set_fixes`, hotkey
re-registration, tray rebuild happen in main) → the UI re-renders from the new
config. History data is kept.

## Dev preview (no Electron needed)

Open `settings.html` or `overlay.html` directly in a browser — the mock bridge
loads automatically. The overlay mock cycles hidden → recording (6s) →
processing (1.5s) → hidden.
