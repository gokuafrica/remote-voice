"""
Remote Voice — System Tray App

Push-to-talk / toggle hotkey -> record mic -> transcribe via server -> paste.

Hotkey approach inspired by Handy STT (github.com/cjpais/Handy):
  - Uses keyboard.hook for raw press/release events (like Handy's rdev)
  - Tracks scan codes to detect combo activation and release
  - 30ms debounce on press, instant release (matches Handy's coordinator)
  - State machine: IDLE -> RECORDING -> PROCESSING
"""

import ctypes
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse
import wave
from pathlib import Path

import keyboard
import pystray
import requests
import sounddevice as sd
from PIL import Image, ImageDraw
from pynput.keyboard import Controller as KBController

CONFIG_PATH = Path(__file__).parent / "config.json"
TRAY_CONFIG_PATH = Path(__file__).parent / "tray_config.json"

TRAY_DEFAULTS = {
    "server_url": None,       # e.g. "http://100.x.y.z:8787" — null = localhost from config.json
    "hotkey": "left ctrl+'",
    "mic_device": None,       # clean device name (str) or None for system default
    "sample_rate": 16000,
    "mode": "push_to_talk",   # "push_to_talk" or "toggle"
}

DEBOUNCE_MS = 30  # Handy STT uses 30ms

# Host API reliability order — MME is most compatible (esp. Bluetooth)
_API_ORDER = {
    "MME": 0,
    "Windows DirectSound": 1,
    "Windows WASAPI": 2,
    "Windows WDM-KS": 3,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_server_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    return {"server_port": 8787}


def load_tray_config() -> dict:
    if TRAY_CONFIG_PATH.exists():
        with open(TRAY_CONFIG_PATH, "r") as f:
            cfg = {**TRAY_DEFAULTS, **json.load(f)}
        # Migrate: old format stored device index (int), new uses name (str)
        if isinstance(cfg.get("mic_device"), int):
            cfg["mic_device"] = None
        return cfg
    return dict(TRAY_DEFAULTS)


def save_tray_config(cfg: dict):
    with open(TRAY_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)


def log(msg: str):
    print(f"{time.strftime('%H:%M:%S')} {msg}")


def _init_com():
    """Initialize COM on current thread — required for WASAPI audio."""
    try:
        ctypes.windll.ole32.CoInitializeEx(0, 0)
    except Exception:
        pass


def _set_clipboard(text: str) -> bool:
    """Set clipboard text via Win32 API with proper 64-bit type annotations."""
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32

    # Both argtypes AND restype must be set for 64-bit pointer safety.
    # Previous attempt only set restype → OverflowError when passing
    # 64-bit pointer back as argument to GlobalLock/SetClipboardData.
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.restype = ctypes.c_bool
    u32.OpenClipboard.argtypes = [ctypes.c_void_p]
    u32.OpenClipboard.restype = ctypes.c_bool
    u32.EmptyClipboard.restype = ctypes.c_bool
    u32.CloseClipboard.restype = ctypes.c_bool
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p

    if not u32.OpenClipboard(None):
        log("Clipboard: OpenClipboard failed")
        return False
    try:
        u32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            log("Clipboard: GlobalAlloc failed")
            return False
        p = k32.GlobalLock(h)
        if not p:
            log("Clipboard: GlobalLock failed")
            return False
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        u32.SetClipboardData(CF_UNICODETEXT, h)
        return True
    finally:
        u32.CloseClipboard()



# ---------------------------------------------------------------------------
# Audio — clean device names & smart fallback
# ---------------------------------------------------------------------------
def _clean_device_name(raw: str) -> str | None:
    """Extract clean display name from a PortAudio device name.

    Returns None for system redirects (Sound Mapper, Primary Capture).
    Cleans up Bluetooth driver paths and WDM-KS "Wave" suffixes.
    """
    raw = raw.replace("\r", " ").replace("\n", " ").strip()
    if "Sound Mapper" in raw or "Primary Sound" in raw:
        return None
    # Bluetooth: "Headset (@System32\drivers\bthhfenum.sys,...;(ULT WEAR))"
    if "bthhfenum" in raw:
        m = re.search(r"\(([^()]+)\)\)?\s*$", raw)
        return f"{m.group(1)} (Bluetooth)" if m else None
    # Standard: "Microphone (Brio 100)" → "Brio 100"
    m = re.match(
        r"(?:Microphone|Headset Microphone|Headset|Input)\s*\(([^)]+)\)?\s*$",
        raw,
    )
    if m:
        name = m.group(1)
        name = re.sub(r"\s+Wave\s*$", "", name)  # strip WDM-KS artifact
        return name
    return raw


def get_unique_devices() -> list[str]:
    """Return deduplicated clean device names for the menu.

    Each physical device appears once regardless of how many host APIs
    expose it.  MME-truncated names are merged with their full variants.
    """
    devices = sd.query_devices()
    clean_names: list[str] = []
    for d in devices:
        if d["max_input_channels"] > 0:
            cn = _clean_device_name(d["name"])
            if cn:
                clean_names.append(cn)

    # Deduplicate: keep longest variant when names share a prefix
    # (handles MME's 32-char truncation, e.g. "Virtual Desktop Aud" → "Virtual Desktop Audio")
    unique: list[str] = []
    for name in sorted(clean_names, key=len, reverse=True):
        if not any(u.startswith(name) or name.startswith(u) for u in unique):
            unique.append(name)

    return sorted(unique)


def _find_device_indices(device_name: str) -> list[int]:
    """Find all PortAudio indices for a clean device name.

    Returns indices ordered by host API reliability (MME first).
    """
    devices = sd.query_devices()
    host_apis = sd.query_hostapis()
    matches: list[tuple[int, int]] = []

    for idx, d in enumerate(devices):
        if d["max_input_channels"] <= 0:
            continue
        cn = _clean_device_name(d["name"])
        if not cn:
            continue
        # Prefix match handles MME truncation
        if cn == device_name or cn.startswith(device_name) or device_name.startswith(cn):
            api = host_apis[d["hostapi"]]["name"]
            matches.append((idx, _API_ORDER.get(api, 99)))

    matches.sort(key=lambda x: x[1])
    return [idx for idx, _ in matches]


def _build_device_attempts(device_name: str | None, sr: int) -> list[tuple]:
    """Build ordered (device_index, sample_rate) list to try when opening mic."""
    attempts: list[tuple] = []
    if device_name:
        for idx in _find_device_indices(device_name):
            attempts.append((idx, sr))
            attempts.append((idx, None))
    # System default as last resort
    attempts.append((None, sr))
    attempts.append((None, None))
    return attempts


def transcribe(session: requests.Session, audio_bytes: bytes, server_url: str,
               filename: str = "audio.wav", mime: str = "audio/wav") -> str:
    url = (f"{server_url}/asr"
           f"?encode=true&task=transcribe&language=en"
           f"&word_timestamps=false&output=txt")
    resp = session.post(
        url,
        files={"audio_file": (filename, audio_bytes, mime)},
        timeout=(5, 10),  # 5s connect, 10s read
    )
    resp.raise_for_status()
    return resp.text.strip()


# ---------------------------------------------------------------------------
# Tray App
# ---------------------------------------------------------------------------
class RemoteVoiceTray:
    # States (mirrors Handy STT's TranscriptionCoordinator)
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"

    def __init__(self):
        self.tray_config = load_tray_config()
        self.server_config = load_server_config()
        self.state = self.IDLE
        self.frames: list = []
        self.stream = None
        self.icon = None
        self.typer = KBController()
        self.actual_sr = 16000
        self._lock = threading.Lock()
        self._http = requests.Session()
        self._http.headers["Connection"] = "close"  # fresh TCP per request (no stale connections)

        # Scan-code tracking for push-to-talk (like Handy's rdev approach)
        self._pressed_scans: set[int] = set()
        self._combo_active = False
        self._last_activate = 0.0
        self._combo_scan_sets: list[set[int]] = []
        self._parse_hotkey()

        # Keepalive: ping server every 30s to keep Tailscale tunnel warm (remote only)
        self._keepalive_stop = threading.Event()
        if not self._is_server_local():
            self._keepalive_http = requests.Session()
            self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
            self._keepalive_thread.start()
        else:
            self._keepalive_http = None
            self._keepalive_thread = None

    def _resolve_server_url(self) -> str:
        """Return the effective server URL.

        Rewrites 'localhost' to '127.0.0.1' to avoid the IPv6 penalty on
        Windows (server listens on 0.0.0.0/IPv4; connecting to ::1 first
        causes a ~2s timeout before falling back to 127.0.0.1).
        """
        url = self.tray_config.get("server_url")
        if not url:
            port = self.server_config.get("server_port", 8787)
            url = f"http://127.0.0.1:{port}"
        else:
            url = url.replace("://localhost", "://127.0.0.1")
        return url

    def _is_server_local(self) -> bool:
        """Check if the server URL points to this machine."""
        host = urlparse(self._resolve_server_url()).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1")

    # ---- Hotkey parsing & detection ----------------------------------------

    def _parse_hotkey(self):
        """Parse hotkey string into scan code sets for combo detection."""
        hotkey = self.tray_config["hotkey"]
        try:
            parsed = keyboard.parse_hotkey(hotkey)
            step = parsed[0]  # first step only (no multi-step sequences)
            self._combo_scan_sets = [set(part) for part in step]
            log(f"Hotkey '{hotkey}' -> scans: {self._combo_scan_sets}")
        except Exception as e:
            log(f"Failed to parse hotkey '{hotkey}': {e}")
            self._combo_scan_sets = []

    def _is_combo_held(self) -> bool:
        """True when at least one scan code from each hotkey part is pressed."""
        return bool(self._combo_scan_sets) and all(
            part_scans & self._pressed_scans
            for part_scans in self._combo_scan_sets
        )

    def _on_key_event(self, event):
        """Global keyboard hook — tracks pressed keys, detects combo."""
        try:
            sc = event.scan_code
            if event.event_type == keyboard.KEY_DOWN:
                self._pressed_scans.add(sc)
            elif event.event_type == keyboard.KEY_UP:
                self._pressed_scans.discard(sc)

            held = self._is_combo_held()
            mode = self.tray_config.get("mode", "push_to_talk")

            if held and not self._combo_active:
                # Combo just activated — apply debounce (30ms, like Handy)
                now = time.monotonic()
                if (now - self._last_activate) * 1000 >= DEBOUNCE_MS:
                    self._last_activate = now
                    self._combo_active = True
                    log(f"Combo ON  (mode={mode}, state={self.state})")

                    if mode == "push_to_talk":
                        if self.state == self.IDLE:
                            threading.Thread(
                                target=self._do_start, daemon=True
                            ).start()
                    else:  # toggle
                        if self.state == self.IDLE:
                            threading.Thread(
                                target=self._do_start, daemon=True
                            ).start()
                        elif self.state == self.RECORDING:
                            threading.Thread(
                                target=self._do_stop, daemon=True
                            ).start()

            elif not held and self._combo_active:
                # Combo released — no debounce (instant, like Handy)
                self._combo_active = False
                log(f"Combo OFF (mode={mode}, state={self.state})")

                if mode == "push_to_talk" and self.state == self.RECORDING:
                    threading.Thread(
                        target=self._do_stop, daemon=True
                    ).start()

        except Exception as e:
            log(f"Key event error: {e}")

    # ---- Keepalive -----------------------------------------------------------

    def _keepalive_loop(self):
        """Ping server every 30s to keep Tailscale tunnel warm. Only runs for remote servers."""
        self._server_reachable = True
        while not self._keepalive_stop.wait(30):
            try:
                self._keepalive_http.head(self._resolve_server_url(), timeout=5)
                if not self._server_reachable:
                    log("Server connection restored")
                    self._server_reachable = True
            except Exception:
                if self._server_reachable:
                    log("Server unreachable — will keep retrying")
                    self._server_reachable = False

    # ---- Recording ----------------------------------------------------------

    def _do_start(self):
        with self._lock:
            if self.state != self.IDLE:
                return
            self._start_recording()
            # Push-to-talk: if user released the combo while the mic was
            # opening (takes ~50-200ms), stop recording immediately.
            if (self.state == self.RECORDING
                    and not self._combo_active
                    and self.tray_config.get("mode", "push_to_talk") == "push_to_talk"):
                log("Combo released during mic open — auto-stopping")
                self._stop_recording()

    def _do_stop(self):
        with self._lock:
            if self.state != self.RECORDING:
                return
            self._stop_recording()

    def _start_recording(self):
        _init_com()
        # Kill any orphaned streams from a previous timed-out close
        try:
            sd.stop()
        except Exception:
            pass
        self.frames = []
        device_name = self.tray_config.get("mic_device")
        sr = self.tray_config.get("sample_rate", 16000)
        log(f"Rec start (mic={device_name!r}, sr={sr})")

        def callback(indata, frame_count, time_info, status):
            if self.state == self.RECORDING:
                self.frames.append(indata.copy())

        attempts = _build_device_attempts(device_name, sr)

        for try_dev, try_sr in attempts:
            try:
                self.stream = sd.InputStream(
                    samplerate=try_sr,
                    channels=1,
                    dtype="int16",
                    device=try_dev,
                    callback=callback,
                )
                self.stream.start()
                self.actual_sr = int(self.stream.samplerate)
                self.state = self.RECORDING
                self.update_icon("red")
                log(f"Mic opened: dev={try_dev}, sr={self.actual_sr}")
                return
            except Exception as e:
                log(f"Mic fail (dev={try_dev}, sr={try_sr}): {e}")

        log("ERROR: all mic attempts failed")
        self.update_icon("gray")

    def _stop_recording(self):
        self.state = self.PROCESSING
        log(f"Rec stop ({len(self.frames)} frames)")

        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

        if not self.frames:
            log("No audio captured")
            self.state = self.IDLE
            self.update_icon("gray")
            return

        self.update_icon("blue")
        threading.Thread(target=self._process_audio, daemon=True).start()

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
                creationflags=subprocess.CREATE_NO_WINDOW,
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

    def _process_audio(self):
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.actual_sr)
                for frame in self.frames:
                    wf.writeframes(frame.tobytes())

            audio_bytes = buf.getvalue()
            server_url = self._resolve_server_url()
            is_local = self._is_server_local()
            duration = sum(len(f) for f in self.frames) / self.actual_sr
            if is_local or duration < 5:
                filename, mime = "audio.wav", "audio/wav"
            else:
                audio_bytes, filename, mime = self._compress_audio(audio_bytes)

            log(f"Sending {len(audio_bytes)} bytes ({filename}) to {server_url}...")

            # Retry with exponential backoff (handles brief Tailscale disconnects)
            # Each attempt uses a fresh TCP connection (Connection: close header)
            max_attempts = 3
            text = None
            for attempt in range(max_attempts):
                try:
                    text = transcribe(self._http, audio_bytes, server_url, filename, mime)
                    break
                except Exception as e:
                    if attempt < max_attempts - 1:
                        wait = 2 ** attempt
                        log(f"Attempt {attempt + 1} failed: {e} — retrying in {wait}s")
                        time.sleep(wait)
                    else:
                        raise

            log(f"Result: {text[:120]}")

            if text:
                # Ensure modifier keys from hotkey are fully released
                time.sleep(0.3)
                if _set_clipboard(text):
                    time.sleep(0.05)
                    keyboard.send('ctrl+v')
                    log("Pasted via clipboard")
                else:
                    log("Clipboard failed — falling back to typing")
                    keyboard.write(text, delay=0.01)

            self.update_icon("green")
            time.sleep(0.5)
        except Exception as e:
            log(f"Transcription error: {e}")
        finally:
            self.state = self.IDLE
            self.update_icon("gray")
            self.frames = []

    # ---- Tray Icon ----------------------------------------------------------

    def create_icon_image(self, color="gray"):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        colors = {
            "gray": "#888888", "red": "#FF4444",
            "green": "#44CC44", "blue": "#4488FF",
        }
        draw.ellipse([8, 8, 56, 56], fill=colors.get(color, color))
        draw.rectangle([26, 16, 38, 38], fill="white")
        draw.arc([22, 28, 42, 50], 0, 180, fill="white", width=3)
        draw.line([32, 50, 32, 56], fill="white", width=3)
        draw.line([24, 56, 40, 56], fill="white", width=2)
        return img

    def update_icon(self, color):
        if self.icon:
            self.icon.icon = self.create_icon_image(color)

    # ---- Tray Menu ----------------------------------------------------------

    def get_mic_menu(self):
        device_names = get_unique_devices()

        def make_handler(name):
            def handler(icon, item):
                self.tray_config["mic_device"] = name
                save_tray_config(self.tray_config)
                log(f"Mic -> {name!r}")
            return handler

        items = [
            pystray.MenuItem(
                "System Default",
                make_handler(None),
                checked=lambda item: not self.tray_config.get("mic_device"),
                radio=True,
            )
        ]
        for name in device_names:
            items.append(
                pystray.MenuItem(
                    name,
                    make_handler(name),
                    checked=lambda item, n=name: self.tray_config.get("mic_device") == n,
                    radio=True,
                )
            )
        return pystray.Menu(*items)

    def get_mode_menu(self):
        def set_mode(m):
            def handler(icon, item):
                self.tray_config["mode"] = m
                save_tray_config(self.tray_config)
                log(f"Mode -> {m}")
            return handler

        return pystray.Menu(
            pystray.MenuItem(
                "Push to Talk (hold hotkey)",
                set_mode("push_to_talk"),
                checked=lambda item: self.tray_config.get("mode", "push_to_talk") == "push_to_talk",
                radio=True,
            ),
            pystray.MenuItem(
                "Toggle (press twice)",
                set_mode("toggle"),
                checked=lambda item: self.tray_config.get("mode", "push_to_talk") != "push_to_talk",
                radio=True,
            ),
        )

    def _set_server_url(self, icon, item):
        """Open dialog to set server URL."""
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        current = self.tray_config.get("server_url") or self._resolve_server_url()
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

    def build_menu(self):
        hotkey = self.tray_config["hotkey"]
        mode = self.tray_config.get("mode", "push_to_talk")
        mode_label = "hold" if mode == "push_to_talk" else "toggle"
        return pystray.Menu(
            pystray.MenuItem(
                f"Hotkey: {hotkey} ({mode_label})",
                lambda: None, enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Server URL...", self._set_server_url),
            pystray.MenuItem("Mode", self.get_mode_menu()),
            pystray.MenuItem("Microphone", self.get_mic_menu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.quit),
        )

    def quit(self, icon, item):
        self._keepalive_stop.set()
        self._http.close()
        if self._keepalive_http:
            self._keepalive_http.close()
        keyboard.unhook_all()
        icon.stop()

    def run(self):
        hotkey = self.tray_config["hotkey"]
        mode = self.tray_config.get("mode", "push_to_talk")
        log(f"Starting: hotkey='{hotkey}', mode={mode}")

        self.icon = pystray.Icon(
            "Remote Voice",
            self.create_icon_image("gray"),
            "Remote Voice",
            menu=self.build_menu(),
        )

        # Install global keyboard hook (like Handy STT's rdev listener)
        keyboard.hook(self._on_key_event)
        log("Keyboard hook installed")

        self.icon.run()


def main():
    RemoteVoiceTray().run()


if __name__ == "__main__":
    main()
