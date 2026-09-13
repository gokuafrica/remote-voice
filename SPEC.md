# SPEC — "Spokenly-inspired" Windows-only rework (MVP)

## Goal
Single Windows app, modern UI, integrated tray, direct architecture (no separate server app, no
scheduled task, no firewall rule). Preserves the exact transcription performance of the old stack
(Parakeet TDT 0.6b v2 via onnx-asr + ONNX Runtime CUDA, ~0.3-0.5s end-to-end).

## Stack decision
- **UI/shell**: Electron (plain JS/HTML/CSS, no build step, no framework). Node 20+, `app/` dir.
- **Engine**: the proven Python pipeline from `master/server.py`, ported into a **Python sidecar
  process** owned by the Electron app, speaking **stdio JSON-RPC** (newline-delimited JSON).
  No HTTP, no port. The app spawns it at launch, keeps it resident (model preloaded at startup),
  and kills it on quit.

## Directory layout (this worktree)
```
engine/engine.py          # Python sidecar (port of master/server.py pipeline, stdio)
engine/engine_tests.py    # ported pipeline tests from master/tests.py (regex-only parts)
app/package.json          # electron + uiohook-napi + koffi deps
app/main/*.js             # Electron main-process modules (tray, hotkey, recorder, engine IPC, paste)
app/preload.js            # contextBridge API
app/renderer/*.html|css|js# overlay window + settings window + hidden recorder window
config.json               # merged single config (seeded from master/config.json + tray_config.json)
README.md                 # dev run + build instructions
```

## Engine contract (`app/CONTRACT.md` — authoritative, do not edit without orchestrator)
Stdio, one JSON object per line.
- Request: `{"id": <int>, "op": "...", ...}`
  - `ping` → `{"id":n,"ok":true,"ready":<bool>,"model":"<name>"}` (ready=false until model loaded/warm)
  - `transcribe` `{"wav_path": "<abs 16k-mono WAV path>"}` → `{"id":n,"ok":true,"text":"...","timings":{"transcribe_s":x,"cleanup_s":y}}` or `{"id":n,"ok":false,"error":"..."}` (model reload-on-TDR retry preserved inside engine)
  - `set_fixes` `{"fixes": {"wrong":"correct", ...}}` → `{"id":n,"ok":true}` (hot-reload replacement words, recompile patterns, longest-first)
  - `shutdown` → engine exits 0
- Engine reads config via `--config <path>` argv. Engine prints diagnostics to **stderr only**
  (stdout is protocol). Robust line-buffered stdio (handle partial writes).
- Pipeline preserved verbatim: CUDA DLL bootstrap → provider load CUDA→CPU fallback →
  `apply_pronunciation_fixes()` → regex cleanup (voice commands, emoji, numbers, fillers) → return.
  Optional Ollama LLM step kept behind config flag, off by default for MVP.

## Config schema (single `config.json`)
```json
{
  "hotkey": "right ctrl",
  "mode": "toggle",
  "mic_device": null,
  "sample_rate": 16000,
  "voice_model": "nemo-parakeet-tdt-0.6b-v2",
  "pronunciation_fixes": { "...": "seeded from master/config.json (17 live entries)" },
  "overlay_position": "bottom",
  "history_max": 200,
  "history_retention_hours": 24,
  "ollama_url": "http://localhost:11434",
  "ollama_model": "qwen2.5:3b",
  "cleanup_prompt": "...",
  "use_llm": false
}
```
Seeded at first run from `master/config.json` (pronunciation_fixes) and `master/tray_config.json`
(hotkey/mode/mic_device/sample_rate). Stored in `%APPDATA%/Remote Voice/config.json` when packaged;
beside the app in dev.

## Work split (one agent branch each, merged into `rework/spokenly-v2`)
- `rework/spokenly-v2-engine`: `engine/`, config seed logic, ported tests, latency verification.
- `rework/spokenly-v2-shell`: `app/package.json`, `app/main/**`, `app/preload.js`, smoke-test mode.
- `rework/spokenly-v2-ui`: `app/renderer/**` only (overlay visuals, settings UI, recorder renderer).

## MVP feature list (from Spokenly research)
1. Global hotkey push-to-talk + toggle (default right Ctrl), state machine IDLE→RECORDING→PROCESSING.
2. Integrated tray icon with state colors (gray idle / red recording / blue processing / green success).
3. **Always-on-top transparent overlay pill** near cursor while recording: red mic pulse + live level
   bars; turns blue with spinner while processing; Esc or click cancels. This replaces the old
   barely-visible red tray icon as the primary feedback.
4. Insert-at-cursor via clipboard+Ctrl+V with clipboard preservation (sequence-number guard; keep
   non-text clipboard content by saving/restoring Electron clipboard state).
5. Replacement words (pronunciation fixes) editor in settings, hot-reloaded into engine.
6. Mic device picker with fallback chain (ported device-name cleaning + attempt ordering).
7. History: JSONL in userData, text+timestamp+duration, searchable, "paste last" from tray menu.
8. Settings window: modern dark UI, tabs General / Microphone / Replacement words / History.
9. Smoke-test mode `npm run smoke`: launches app, waits for engine ping ready, reports, exits 0/1.

## Verification (orchestrator runs at the end)
- `python engine/engine_tests.py` passes.
- `npm run smoke` in `app/` exits 0 with engine ready.
- End-to-end: TTS-generated WAV → engine transcribe → correct text, <1s.
- Manual-attention list reported to user.
