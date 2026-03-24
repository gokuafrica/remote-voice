# Implementation Plan: Windows Tray App Feature Parity with Mac Client

**IMPORTANT FOR IMPLEMENTING AGENT:** Read this entire document before writing any code. Read `CLAUDE.md` for project conventions. Read `tray.py` (the file you're modifying) and `mac_tray.py` (the reference implementation) thoroughly. When implementation is complete and verified, DELETE this file from the repo.

---

## What You're Doing

The Windows tray client (`tray.py`) is missing several features that the Mac client (`mac_tray.py`) already has. Your job is to add these features to `tray.py` so both clients have the same networking capabilities. The Mac client was designed for remote connections over Tailscale; the Windows client currently assumes the server is always on localhost.

**The goal:** A second Windows PC (or any machine) should be able to run `tray.py` and connect to the main server PC over Tailscale, with the same reliability as the Mac client.

## Ground Rules

1. **Only modify `tray.py` and `tray_config.json` (its defaults).** Do not modify `server.py`, `mac_tray.py`, `gui.py`, `config.json`, `tests.py`, or `requirements.txt`.
2. **All changes must be backwards-compatible.** If `tray_config.json` doesn't have `server_url`, the app should behave exactly as before (localhost with port from `config.json`).
3. Read `CLAUDE.md` for project conventions — especially "propose before committing".
4. Read `mac_tray.py` for reference implementations of each feature.

## Project Context

Read these files for full understanding:
- `tray.py` — the Windows client you're modifying (574 lines)
- `mac_tray.py` — the Mac client with all features already implemented
- `CLAUDE.md` — project conventions
- `tray_config.json` — current Windows config format
- `config.json` — server config (has `server_port`)

### How the System Works

```
Client (PC/Mac/Phone)  -->  Windows Server PC (port 8787)
                              |
                              +-- Parakeet V2 (GPU transcription)
                              +-- Regex cleanup
                              +-- Optional Ollama LLM
                              |
                         <--  Cleaned text returned
```

The server exposes: `POST /asr?encode=true&task=transcribe&language=en&word_timestamps=false&output=txt` with multipart form data (`audio_file` field). Returns plain text.

### Current Windows tray.py Architecture

```
tray_config.json → load_tray_config() → TRAY_DEFAULTS dict
config.json → load_server_config() → server_port (for localhost URL)

Recording: sounddevice.InputStream → frames → WAV (io.BytesIO) → HTTP POST → paste
Tray UI: pystray with Menu (hotkey info, mode submenu, mic submenu, quit)
```

---

## Features to Add (in recommended implementation order)

### Feature 1: Configurable Server URL

**Current behavior (tray.py lines 54-58, 231-241):**
- `load_server_config()` reads `config.json` for `server_port`
- `transcribe()` hardcodes `http://localhost:{port}/asr`
- Only works when server runs on the same machine

**Target behavior:**
- New `server_url` field in `tray_config.json` (optional, for backwards compatibility)
- If `server_url` is set, use it directly (e.g. `http://100.x.y.z:8787`)
- If `server_url` is not set or is `null`, fall back to `http://localhost:{port}` from `config.json` (current behavior)

**Changes:**

1. Update `TRAY_DEFAULTS` (line 33-38) — add `"server_url": null`

2. Update `transcribe()` function (lines 231-241) — accept `server_url` instead of `port`:
   ```python
   def transcribe(audio_bytes: bytes, server_url: str) -> str:
       url = (f"{server_url}/asr"
              f"?encode=true&task=transcribe&language=en"
              f"&word_timestamps=false&output=txt")
       ...
   ```

3. In `_process_audio()` (line 427-429) — resolve the URL:
   ```python
   server_url = self.tray_config.get("server_url")
   if not server_url:
       port = self.server_config.get("server_port", 8787)
       server_url = f"http://localhost:{port}"
   text = transcribe(audio_bytes, server_url)
   ```

4. `load_server_config()` is still needed for the fallback path — don't remove it.

**Reference:** `mac_tray.py` lines 70-71, 176-189, 549-550

---

### Feature 2: Persistent HTTP Session

**Current behavior (tray.py line 235-240):**
- Each `transcribe()` call uses bare `requests.post()` — creates a new TCP connection every time

**Target behavior:**
- Use `requests.Session()` for connection pooling and reuse
- Especially important for remote Tailscale connections where TCP handshake adds latency

**Changes:**

1. In `RemoteVoiceTray.__init__()` (line 253-268) — create session:
   ```python
   self._http = requests.Session()
   ```

2. Update `transcribe()` — accept session as first parameter:
   ```python
   def transcribe(session: requests.Session, audio_bytes: bytes, server_url: str) -> str:
       ...
       resp = session.post(url, files=..., timeout=(5, 30))
       ...
   ```
   Note the split timeout: `(5, 30)` = 5s connect timeout, 30s read timeout. This makes remote connections fail fast on connection issues instead of hanging for 30s.

3. In `_process_audio()` — pass session:
   ```python
   text = transcribe(self._http, audio_bytes, server_url)
   ```

4. In `quit()` (line 542-544) — close session:
   ```python
   def quit(self, icon, item):
       self._http.close()
       keyboard.unhook_all()
       icon.stop()
   ```

**Reference:** `mac_tray.py` lines 176-189, 209, 558, 625

---

### Feature 3: Retry with Exponential Backoff

**Current behavior (tray.py line 429):**
- Single `transcribe()` call — if it fails, exception is caught and logged, no retry

**Target behavior:**
- 3 total attempts (original + 2 retries)
- Exponential backoff: 1s wait after first failure, 2s after second
- Handles brief Tailscale disconnects and network hiccups

**Changes:**

In `_process_audio()`, wrap the `transcribe()` call (around line 429):
```python
max_attempts = 3
text = None
for attempt in range(max_attempts):
    try:
        text = transcribe(self._http, audio_bytes, server_url)
        break
    except Exception as e:
        if attempt < max_attempts - 1:
            wait = 2 ** attempt  # 1s, 2s
            log(f"Attempt {attempt + 1} failed: {e} — retrying in {wait}s")
            time.sleep(wait)
        else:
            raise
```

**Reference:** `mac_tray.py` lines 553-566

---

### Feature 4: Keepalive Ping

**Current behavior:**
- No keepalive mechanism — Tailscale tunnel and HTTP connection go cold between recordings

**Target behavior:**
- Background thread pings the server every 30 seconds with a lightweight HEAD request
- Keeps Tailscale tunnel warm and HTTP connection alive
- Silent failures (server may be offline)
- Only meaningful for remote connections, but harmless for localhost

**Changes:**

1. In `__init__()` — start keepalive thread:
   ```python
   self._keepalive_stop = threading.Event()
   self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
   self._keepalive_thread.start()
   ```

2. Add keepalive method to the class:
   ```python
   def _keepalive_loop(self):
       """Ping server every 30s to keep Tailscale tunnel and HTTP connection warm."""
       while not self._keepalive_stop.wait(30):
           try:
               server_url = self.tray_config.get("server_url")
               if not server_url:
                   port = self.server_config.get("server_port", 8787)
                   server_url = f"http://localhost:{port}"
               self._http.head(server_url, timeout=5)
           except Exception:
               pass
   ```

3. In `quit()` — stop keepalive:
   ```python
   self._keepalive_stop.set()
   ```

**Reference:** `mac_tray.py` lines 268-271, 409-416, 623

---

### Feature 5: Audio Compression

**Current behavior (tray.py lines 416-426):**
- Records to WAV in memory (`io.BytesIO`) and sends raw, uncompressed
- A 15-second recording is ~480KB

**Target behavior:**
- Compress audio before sending to reduce upload size (~10-20x smaller)
- On Windows, use `ffmpeg` if available (should be — it's in the server setup instructions)
- Fall back to raw WAV if ffmpeg is not installed
- The server already supports all common audio formats via ffmpeg decoding

**Changes:**

1. Add imports at top of file:
   ```python
   import os
   import subprocess
   import tempfile
   ```

2. Add compression method to the class:
   ```python
   def _compress_audio(self, wav_bytes: bytes) -> tuple[bytes, str, str]:
       """Compress WAV to OGG/Opus via ffmpeg. Falls back to WAV."""
       try:
           wav_path = os.path.join(tempfile.gettempdir(), "rv_recording.wav")
           ogg_path = os.path.join(tempfile.gettempdir(), "rv_recording.ogg")
           with open(wav_path, "wb") as f:
               f.write(wav_bytes)
           subprocess.run(
               ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libopus", "-b:a", "32k", ogg_path],
               check=True, capture_output=True, timeout=10,
           )
           with open(ogg_path, "rb") as f:
               data = f.read()
           log(f"Compressed: {len(wav_bytes)} -> {len(data)} bytes ({len(data)*100//len(wav_bytes)}%)")
           os.unlink(wav_path)
           os.unlink(ogg_path)
           return data, "audio.ogg", "audio/ogg"
       except FileNotFoundError:
           log("ffmpeg not found, sending uncompressed WAV")
       except Exception as e:
           log(f"Compression failed: {e}")
       return wav_bytes, "audio.wav", "audio/wav"
   ```

3. Update `transcribe()` to accept filename and mime type:
   ```python
   def transcribe(session, audio_bytes, server_url,
                  filename="audio.wav", mime="audio/wav"):
       ...
       files={"audio_file": (filename, audio_bytes, mime)},
       ...
   ```

4. In `_process_audio()` — compress before sending:
   ```python
   audio_bytes = buf.getvalue()
   audio_bytes, filename, mime = self._compress_audio(audio_bytes)
   ...
   text = transcribe(self._http, audio_bytes, server_url, filename, mime)
   ```

**Reference:** `mac_tray.py` lines 483-535

---

### Feature 6: Server URL Menu Item (UI)

**Current behavior (tray.py lines 526-540):**
- Menu has: hotkey info, mode submenu, mic submenu, quit
- No way to change server URL from the UI

**Target behavior:**
- Add "Server URL..." menu item that opens an input dialog
- pystray doesn't have built-in input dialogs, so use `tkinter.simpledialog` (available in Python stdlib on Windows)
- Saves the new URL to `tray_config.json`

**Changes:**

1. Add import:
   ```python
   import tkinter as tk
   from tkinter import simpledialog
   ```

2. Add method to the class:
   ```python
   def _set_server_url(self, icon, item):
       """Open dialog to set server URL."""
       root = tk.Tk()
       root.withdraw()  # hide main window
       current = self.tray_config.get("server_url") or ""
       if not current:
           port = self.server_config.get("server_port", 8787)
           current = f"http://localhost:{port}"
       result = simpledialog.askstring(
           "Server URL",
           "Enter server URL (e.g. http://100.x.y.z:8787):",
           initialvalue=current,
           parent=root,
       )
       root.destroy()
       if result is not None:
           result = result.strip()
           self.tray_config["server_url"] = result if result else None
           save_tray_config(self.tray_config)
           log(f"Server URL -> {result!r}")
   ```

3. In `build_menu()` (line 526-540) — add the menu item after the separator:
   ```python
   pystray.Menu.SEPARATOR,
   pystray.MenuItem("Server URL...", self._set_server_url),
   pystray.MenuItem("Mode", self.get_mode_menu()),
   ...
   ```

**Reference:** `mac_tray.py` lines 592-603 (uses rumps.Window — pystray needs tkinter instead)

---

## What NOT to Change

- **Hotkey system** — keep keyboard.hook + scan codes as-is
- **Clipboard** — keep Win32 ctypes clipboard as-is
- **Paste** — keep keyboard.send('ctrl+v') as-is
- **Device discovery** — keep _API_ORDER, _clean_device_name, etc. as-is
- **COM init** — keep _init_com() as-is
- **Icon generation** — keep create_icon_image() as-is
- **Recording logic** — keep _start_recording(), _stop_recording() as-is (except the _process_audio changes above)

---

## Backwards Compatibility

The key constraint: **existing users who run `tray.py` on the same machine as the server must not be affected.** This means:

- If `tray_config.json` has no `server_url` field → use `http://localhost:{port}` from `config.json` (current behavior)
- If `tray_config.json` has `"server_url": null` → same as above
- If `tray_config.json` has `"server_url": "http://100.x.y.z:8787"` → use that URL directly
- `load_server_config()` must remain for the localhost fallback
- The `server_config` attribute on the class must remain

---

## Verification Checklist

After implementation, verify:

1. **Backwards compat:** Delete `tray_config.json`, run `tray.py` — should work exactly as before (localhost, no errors)
2. **Server URL config:** Set `"server_url": "http://100.x.y.z:8787"` in `tray_config.json` — should connect to remote server
3. **Server URL dialog:** Right-click tray icon → "Server URL..." → enter URL → verify it saves to `tray_config.json`
4. **Persistent session:** Multiple recordings in a row should reuse the connection (check server logs — same source port)
5. **Retry:** Kill the server, do a recording, restart server within 3 seconds — should succeed on retry
6. **Keepalive:** Leave the app idle for 2+ minutes, then record — should not be noticeably slower than immediate recording
7. **Compression:** Check logs for "Compressed: X -> Y bytes" — should show ~10-20x reduction if ffmpeg is installed
8. **Compression fallback:** Rename `ffmpeg.exe` temporarily — should fall back to WAV without errors
9. **No existing files modified** except `tray.py`

---

## Cleanup

**IMPORTANT: After implementation is complete and verified, DELETE this file (`IMPLEMENTATION_PLAN_TRAY_PARITY.md`) from the repository.** It is a temporary planning document and should not remain in the codebase.
