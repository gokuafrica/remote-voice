# Tray App: Hotkey + Mic Recording Issues

## What Works

- **The server** (`server.py`) works perfectly — Parakeet V2 transcription + Ollama cleanup, GPU-accelerated, ~1.3s round-trip.
- **The phone app** (whisper-to-input) works perfectly — records on phone, sends to server, text appears in any app.
- **The server GUI** (`gui.py`) works — configure models, prompt, start/stop server, view logs.
- **The hotkey detection itself works** — `keyboard.add_hotkey("left ctrl+'", callback)` fires correctly (confirmed via `tray.log`).

## What Doesn't Work

The **tray app** (`tray.py`) fails at **recording audio from the mic** after the hotkey fires. The icon flashes red then immediately goes back to gray.

## Problem 1: Mic Fails to Open

From `tray.log`:
```
Recording started (mic=27, sr=16000)
Mic error: Error starting stream: Unanticipated host error [PaErrorCode -9999]:
'WdmSyncIoctl: DeviceIoControl GLE = 0x00000490' [Windows WDM-KS error 0]
```

- Device 27 is `Headset (ULT WEAR)` via WASAPI (Sony Bluetooth headset).
- Paradoxically, the same device works fine in an isolated test script.
- Other WASAPI devices (Brio 100, Steam Streaming, Virtual Desktop Audio) fail with "Invalid sample rate" at 16000 Hz.
- The mic might fail because of Bluetooth A2DP/HFP profile switching, or because something about the tray app's thread/event loop context interferes with PortAudio.

## Problem 2: Hotkey Spam (Solved with Debounce)

`keyboard.add_hotkey` fires repeatedly while keys are held, causing dozens of start_recording attempts per second. Added 500ms debounce — this is fixed.

## Problem 3: Push-to-Talk vs Toggle

Originally tried push-to-talk (hold to record, release to stop). This was extremely problematic:

### Approaches Tried

1. **`keyboard` library — `on_press_key` + `on_release_key`**: Constant red/gray flickering. The `is_pressed` check was unreliable for the `'` key in combo with Left Ctrl.

2. **`keyboard` library — `keyboard.hook` with state tracking**: Same flickering. `is_pressed` for combo keys is unreliable.

3. **`keyboard` library — `keyboard.wait` + release polling**: Hotkey fires but `is_pressed("'")` immediately returns False after `wait()` consumes the event.

4. **`pynput` — `Listener` with `on_press`/`on_release` + `KeyCode.from_char`**: Hotkey never detected. Root cause: `KeyCode.from_char("'")` has `vk=None`, but actual key events have `vk=222`. They don't compare equal. When Ctrl is held, the `'` key doesn't produce a `char` at all.

5. **`pynput` — `Listener` with VK-code normalization**: Fixed the key matching (verified with unit tests), but still had issues — likely the mic problem was masking the hotkey problem.

6. **`keyboard.add_hotkey` with toggle mode**: Hotkey works correctly (confirmed in logs). But mic recording fails (Problem 1 above).

### Current State

The current `tray.py` uses `keyboard.add_hotkey` in **toggle mode** (press once to start, press again to stop). The hotkey detection works. The mic opening fails.

## Suggestions for Next Agent

### Investigate Handy STT's Approach

Handy STT (a Tauri app) successfully does push-to-talk with local mic recording. Worth investigating:

- **Repo**: Handy STT is a Tauri app — check its GitHub for how it handles:
  - Global hotkey registration (it uses Tauri's global shortcut API)
  - Audio recording (might use `cpal` or a Rust audio library, not PortAudio)
  - Push-to-talk hold/release detection
- The user mentioned Handy also had hotkey detection issues until they enabled Tauri global shortcut — so the approach matters.

### Possible Fixes

1. **Mic issue**: Try opening the stream using **MME or DirectSound host API** instead of WASAPI. PortAudio's WASAPI backend has known issues with Bluetooth devices. Use `sounddevice` with `extra_settings` or pass the MME device index instead.

2. **Mic issue alternative**: Use `pyaudio` instead of `sounddevice` — different PortAudio bindings, might behave differently.

3. **Mic issue alternative 2**: Use Windows' native `audioclient` API via `comtypes` for recording — bypasses PortAudio entirely.

4. **Push-to-talk**: Consider using Windows `RegisterHotKey` API (via ctypes) for the press detection, and a low-level keyboard hook (`SetWindowsHookEx`) for release detection. This is what most professional apps do on Windows.

5. **Push-to-talk alternative**: Use `pynput.keyboard.GlobalHotKeys` for toggle mode (which works) and abandon push-to-talk.

## File Overview

| File | Status | Purpose |
|------|--------|---------|
| `server.py` | Working | FastAPI server, Parakeet V2 + Ollama |
| `gui.py` | Working | Server config GUI |
| `tray.py` | Broken (mic) | System tray app for local transcription |
| `config.json` | Working | Server settings |
| `tray_config.json` | Working | Tray app settings (hotkey, mic, sample rate) |
| `tray.log` | Debug output | Check this for error details |
| `start.bat` | Working | Start server from command line |
| `setup.bat` | Working | One-time admin setup (firewall + auto-start) |
| `Remote Voice.bat` | Working | Launch server GUI |
| `Remote Voice Tray.bat` | Broken (same as tray.py) | Launch tray app |

## Environment

- Windows 11 Home, Python 3.14.3
- RTX 4070 Ti, NVIDIA driver 591.86
- Bluetooth headset: Sony ULT WEAR
- Webcam mic: Brio 100
- Hotkey: `left ctrl+'`
- `keyboard` library version: 0.13.5
- `pynput` version: 1.8.1
- `sounddevice` version: 0.5.5
