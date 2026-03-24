"""
Remote Voice — macOS Menu Bar Client

Records audio via hotkey, sends to Windows PC server over Tailscale, pastes result.
No transcription runs on Mac. Server (server.py) runs on Windows with NVIDIA GPU.

macOS port of tray.py — replaces Windows-specific libraries:
  pystray → rumps, keyboard → pynput, Win32 clipboard → pbcopy, Ctrl+V → CGEvent Cmd+V
"""

import io
import json
import os
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import requests
import rumps
import sounddevice as sd
from PIL import Image, ImageDraw
from pynput.keyboard import Controller as KBController, Key, KeyCode, Listener
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGEventPost,
    CGEventSetFlags,
    CGEventTapCreate,
    CGEventTapEnable,
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    kCFRunLoopCommonModes,
    kCGEventFlagMaskCommand,
    kCGEventFlagMaskControl,
    kCGEventFlagMaskAlternate,
    kCGEventFlagMaskShift,
    kCGEventKeyDown,
    kCGEventKeyUp,
    kCGHIDEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
    kCGTailAppendEventTap,
)

# macOS virtual keycodes for hotkey suppression
_MAC_KEYCODES = {
    "'": 39, ";": 41, "`": 50, "\\": 42, "/": 44,
    ",": 43, ".": 47, "-": 27, "=": 24,
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3,
    "g": 5, "h": 4, "i": 34, "j": 38, "k": 40, "l": 37,
    "m": 46, "n": 45, "o": 31, "p": 35, "q": 12, "r": 15,
    "s": 1, "t": 17, "u": 32, "v": 9, "w": 13, "x": 7,
    "y": 16, "z": 6,
}
_MOD_FLAGS = {
    "cmd": kCGEventFlagMaskCommand,
    "ctrl": kCGEventFlagMaskControl,
    "alt": kCGEventFlagMaskAlternate,
    "shift": kCGEventFlagMaskShift,
}

TRAY_CONFIG_PATH = Path(__file__).parent / "mac_tray_config.json"
LOG_PATH = Path(__file__).parent / "mac_tray.log"

TRAY_DEFAULTS = {
    "server_url": "http://100.x.y.z:8787",
    "hotkey_modifier": "cmd",
    "hotkey_key": "'",
    "mic_device": None,
    "sample_rate": 16000,
    "mode": "push_to_talk",
}

DEBOUNCE_MS = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def load_tray_config() -> dict:
    if TRAY_CONFIG_PATH.exists():
        with open(TRAY_CONFIG_PATH, "r") as f:
            return {**TRAY_DEFAULTS, **json.load(f)}
    return dict(TRAY_DEFAULTS)


def save_tray_config(cfg: dict):
    with open(TRAY_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=4)


def log(msg: str):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _set_clipboard(text: str) -> bool:
    """Set clipboard text via pbcopy."""
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)
        return True
    except Exception as e:
        log(f"Clipboard error: {e}")
        return False


def _paste():
    """Simulate Cmd+V via CGEvent."""
    V_KEYCODE = 9  # Virtual keycode for 'v' on macOS
    event_down = CGEventCreateKeyboardEvent(None, V_KEYCODE, True)
    CGEventSetFlags(event_down, kCGEventFlagMaskCommand)
    event_up = CGEventCreateKeyboardEvent(None, V_KEYCODE, False)
    CGEventSetFlags(event_up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, event_down)
    CGEventPost(kCGHIDEventTap, event_up)


# ---------------------------------------------------------------------------
# Audio — clean device names & smart fallback
# ---------------------------------------------------------------------------
def _clean_device_name(raw: str) -> str | None:
    """Extract clean display name from a Core Audio device name.

    Returns None for virtual aggregate/multi-output devices.
    """
    raw = raw.strip()
    if any(skip in raw.lower() for skip in ("aggregate", "multi-output")):
        return None
    return raw


def get_unique_devices() -> list[str]:
    """Return deduplicated clean input device names for the menu."""
    devices = sd.query_devices()
    names = set()
    for d in devices:
        if d["max_input_channels"] > 0:
            cn = _clean_device_name(d["name"])
            if cn:
                names.add(cn)
    return sorted(names)


def _find_device_indices(device_name: str) -> list[int]:
    """Find PortAudio index for a clean device name."""
    devices = sd.query_devices()
    for idx, d in enumerate(devices):
        if d["max_input_channels"] > 0 and _clean_device_name(d["name"]) == device_name:
            return [idx]
    return []


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
    url = (
        f"{server_url}/asr"
        f"?encode=true&task=transcribe&language=en"
        f"&word_timestamps=false&output=txt"
    )
    resp = session.post(
        url,
        files={"audio_file": (filename, audio_bytes, mime)},
        timeout=(5, 30),  # 5s connect, 30s read
    )
    resp.raise_for_status()
    return resp.text.strip()


# ---------------------------------------------------------------------------
# Tray App
# ---------------------------------------------------------------------------
class RemoteVoiceMacTray(rumps.App):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"

    def __init__(self):
        super().__init__("Remote Voice", quit_button=None)
        self.tray_config = load_tray_config()
        self.state = self.IDLE
        self.frames: list = []
        self.stream = None
        self.typer = KBController()
        self.actual_sr = 16000
        self._lock = threading.Lock()
        self._http = requests.Session()  # persistent connection to server

        # Key tracking for pynput
        self._pressed_keys: set = set()
        self._combo_active = False
        self._last_activate = 0.0
        self._hotkey_mod = self.tray_config.get("hotkey_modifier", "cmd")
        self._hotkey_key = self.tray_config.get("hotkey_key", "'")

        # Icons
        self._init_icons()
        self.icon = self._icon_paths["gray"]

        # Menu
        hotkey_display = f"{self._hotkey_mod}+{self._hotkey_key}"
        mode = self.tray_config.get("mode", "push_to_talk")
        mode_label = "hold" if mode == "push_to_talk" else "toggle"

        mode_menu = rumps.MenuItem("Mode")
        mode_menu[" Push to Talk (hold hotkey)"] = rumps.MenuItem(
            "Push to Talk (hold hotkey)", callback=self._set_push_to_talk
        )
        mode_menu["Toggle (press twice)"] = rumps.MenuItem(
            "Toggle (press twice)", callback=self._set_toggle
        )

        mic_menu = rumps.MenuItem("Microphone")
        mic_menu["System Default"] = rumps.MenuItem(
            "System Default", callback=self._select_mic
        )
        for name in get_unique_devices():
            mic_menu[name] = rumps.MenuItem(name, callback=self._select_mic)

        self.menu = [
            rumps.MenuItem(f"Hotkey: {hotkey_display} ({mode_label})"),
            None,
            rumps.MenuItem("Server URL...", callback=self._set_server_url),
            mode_menu,
            mic_menu,
            None,
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        # Keyboard listener (pynput detects hotkey)
        self._listener = Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        log("Keyboard listener started")

        # CGEvent tap suppresses hotkey keystrokes from reaching active app
        self._setup_key_suppression()

        # First-run check
        if not TRAY_CONFIG_PATH.exists() or self.tray_config.get("server_url") == TRAY_DEFAULTS["server_url"]:
            save_tray_config(self.tray_config)
            rumps.alert(
                title="Welcome to Remote Voice!",
                message="Set your Windows PC's Tailscale IP via the 'Server URL...' menu item.",
            )

        # Keepalive: ping server every 30s to keep Tailscale tunnel warm
        self._keepalive_stop = threading.Event()
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)
        self._keepalive_thread.start()

        hotkey = f"{self._hotkey_mod}+{self._hotkey_key}"
        log(f"Starting: hotkey='{hotkey}', mode={mode}")

    # ---- Icons --------------------------------------------------------------

    def create_icon_image(self, color="gray"):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        colors = {
            "gray": "#888888",
            "red": "#FF4444",
            "green": "#44CC44",
            "blue": "#4488FF",
        }
        draw.ellipse([8, 8, 56, 56], fill=colors.get(color, color))
        draw.rectangle([26, 16, 38, 38], fill="white")
        draw.arc([22, 28, 42, 50], 0, 180, fill="white", width=3)
        draw.line([32, 50, 32, 56], fill="white", width=3)
        draw.line([24, 56, 40, 56], fill="white", width=2)
        return img

    def _init_icons(self):
        self._icon_paths = {}
        for color in ("gray", "red", "blue", "green"):
            img = self.create_icon_image(color)
            path = os.path.join(tempfile.gettempdir(), f"rv_icon_{color}.png")
            img.save(path)
            self._icon_paths[color] = path

    def update_icon(self, color):
        path = self._icon_paths.get(color, self._icon_paths["gray"])
        self.icon = path

    # ---- Hotkey suppression (CGEvent tap) ------------------------------------

    def _setup_key_suppression(self):
        """Create a CGEvent tap to suppress hotkey keystrokes from reaching apps.

        pynput can't suppress events on macOS — it only monitors. Without this,
        holding Cmd+' for push-to-talk types '''''... into the active app.
        The tap runs after pynput's listener so detection still works.
        """
        keycode = _MAC_KEYCODES.get(self._hotkey_key)
        mod_flag = _MOD_FLAGS.get(self._hotkey_mod)
        if keycode is None or mod_flag is None:
            log(f"Cannot suppress hotkey: unknown key={self._hotkey_key!r} or mod={self._hotkey_mod!r}")
            return

        def tap_callback(proxy, event_type, event, refcon):
            if event_type in (kCGEventKeyDown, kCGEventKeyUp):
                kc = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                flags = CGEventGetFlags(event)
                if kc == keycode and (flags & mod_flag):
                    return None  # suppress
            return event

        event_mask = (1 << kCGEventKeyDown) | (1 << kCGEventKeyUp)
        tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGTailAppendEventTap,  # after pynput's tap so detection still works
            0,  # active tap (can suppress events)
            event_mask,
            tap_callback,
            None,
        )
        if tap:
            source = CFMachPortCreateRunLoopSource(None, tap, 0)
            CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
            CGEventTapEnable(tap, True)
            self._event_tap = tap  # prevent garbage collection
            self._event_tap_source = source
            log("Hotkey suppression tap installed")
        else:
            log("WARNING: Could not create event tap — hotkey may leak to active app")

    # ---- Key tracking (pynput) ----------------------------------------------

    def _normalize_key(self, key):
        if key in (Key.cmd_l, Key.cmd_r, Key.cmd):
            return "cmd"
        if key in (Key.ctrl_l, Key.ctrl_r, Key.ctrl):
            return "ctrl"
        if key in (Key.alt_l, Key.alt_r, Key.alt):
            return "alt"
        if key in (Key.shift_l, Key.shift_r, Key.shift):
            return "shift"
        if isinstance(key, KeyCode) and key.char:
            return key.char.lower()
        return key

    def _is_combo_held(self) -> bool:
        return self._hotkey_mod in self._pressed_keys and self._hotkey_key in self._pressed_keys

    def _on_press(self, key):
        try:
            self._pressed_keys.add(self._normalize_key(key))

            held = self._is_combo_held()
            mode = self.tray_config.get("mode", "push_to_talk")

            if held and not self._combo_active:
                now = time.monotonic()
                if (now - self._last_activate) * 1000 >= DEBOUNCE_MS:
                    self._last_activate = now
                    self._combo_active = True
                    log(f"Combo ON  (mode={mode}, state={self.state})")

                    if mode == "push_to_talk":
                        if self.state == self.IDLE:
                            threading.Thread(target=self._do_start, daemon=True).start()
                    else:  # toggle
                        if self.state == self.IDLE:
                            threading.Thread(target=self._do_start, daemon=True).start()
                        elif self.state == self.RECORDING:
                            threading.Thread(target=self._do_stop, daemon=True).start()
        except Exception as e:
            log(f"Key press error: {e}")

    def _on_release(self, key):
        try:
            self._pressed_keys.discard(self._normalize_key(key))

            held = self._is_combo_held()
            mode = self.tray_config.get("mode", "push_to_talk")

            if not held and self._combo_active:
                self._combo_active = False
                log(f"Combo OFF (mode={mode}, state={self.state})")

                if mode == "push_to_talk" and self.state == self.RECORDING:
                    threading.Thread(target=self._do_stop, daemon=True).start()
        except Exception as e:
            log(f"Key release error: {e}")

    # ---- Keepalive -----------------------------------------------------------

    def _keepalive_loop(self):
        """Ping server every 30s to keep Tailscale tunnel and HTTP connection warm."""
        while not self._keepalive_stop.wait(30):
            try:
                server_url = self.tray_config.get("server_url", TRAY_DEFAULTS["server_url"])
                self._http.head(server_url, timeout=5)
            except Exception:
                pass  # silent — server may be offline

    # ---- Recording ----------------------------------------------------------

    def _do_start(self):
        with self._lock:
            if self.state != self.IDLE:
                return
            self._start_recording()
            if (
                self.state == self.RECORDING
                and not self._combo_active
                and self.tray_config.get("mode", "push_to_talk") == "push_to_talk"
            ):
                log("Combo released during mic open — auto-stopping")
                self._stop_recording()

    def _do_stop(self):
        with self._lock:
            if self.state != self.RECORDING:
                return
            self._stop_recording()

    def _start_recording(self):
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

    def _compress_audio(self, wav_path: str) -> tuple[bytes, str, str]:
        """Compress WAV before sending. Tries ffmpeg (Opus), afconvert (AAC), falls back to WAV."""
        wav_size = os.path.getsize(wav_path)

        # Try ffmpeg → OGG/Opus (best compression for speech, ~10-20x smaller)
        try:
            ogg_path = wav_path.replace(".wav", ".ogg")
            subprocess.run(
                ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libopus", "-b:a", "32k", ogg_path],
                check=True, capture_output=True, timeout=10,
            )
            with open(ogg_path, "rb") as f:
                data = f.read()
            log(f"Compressed (Opus): {wav_size} -> {len(data)} bytes ({len(data)*100//wav_size}%)")
            os.unlink(ogg_path)
            return data, "audio.ogg", "audio/ogg"
        except FileNotFoundError:
            log("ffmpeg not found, trying afconvert")
        except Exception as e:
            log(f"ffmpeg failed: {e}")

        # Try afconvert → M4A/AAC (macOS built-in)
        try:
            m4a_path = wav_path.replace(".wav", ".m4a")
            result = subprocess.run(
                ["afconvert", "-f", "m4af", "-d", "aac", wav_path, m4a_path],
                check=True, capture_output=True, timeout=10,
            )
            with open(m4a_path, "rb") as f:
                data = f.read()
            log(f"Compressed (AAC): {wav_size} -> {len(data)} bytes ({len(data)*100//wav_size}%)")
            os.unlink(m4a_path)
            return data, "audio.m4a", "audio/mp4"
        except Exception as e:
            stderr = getattr(e, "stderr", b"")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            log(f"afconvert failed: {e} | stderr: {stderr}")

        # Fall back to WAV
        log("Sending uncompressed WAV")
        with open(wav_path, "rb") as f:
            return f.read(), "audio.wav", "audio/wav"

    def _process_audio(self):
        try:
            wav_path = os.path.join(tempfile.gettempdir(), "rv_recording.wav")
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.actual_sr)
                for frame in self.frames:
                    wf.writeframes(frame.tobytes())

            audio_bytes, filename, mime = self._compress_audio(wav_path)
            os.unlink(wav_path)

            server_url = self.tray_config.get("server_url", TRAY_DEFAULTS["server_url"])
            log(f"Sending {len(audio_bytes)} bytes ({filename}) to {server_url}...")

            # Retry with exponential backoff (handles brief Tailscale disconnects)
            max_attempts = 3
            text = None
            for attempt in range(max_attempts):
                try:
                    text = transcribe(self._http, audio_bytes, server_url, filename, mime)
                    break
                except Exception as e:
                    if attempt < max_attempts - 1:
                        wait = 2 ** attempt  # 1s, 2s
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
                    _paste()
                    log("Pasted via clipboard")
                else:
                    log("Clipboard failed — falling back to typing")
                    self.typer.type(text)

            self.update_icon("green")
            time.sleep(0.5)
        except Exception as e:
            log(f"Transcription error: {e}")
        finally:
            self.state = self.IDLE
            self.update_icon("gray")
            self.frames = []

    # ---- Menu callbacks -----------------------------------------------------

    def _set_server_url(self, sender):
        w = rumps.Window(
            message="Enter your Windows PC's Tailscale IP and port:",
            title="Server URL",
            default_text=self.tray_config.get("server_url", TRAY_DEFAULTS["server_url"]),
            dimensions=(320, 24),
        )
        response = w.run()
        if response.clicked:
            self.tray_config["server_url"] = response.text.strip()
            save_tray_config(self.tray_config)
            log(f"Server URL -> {response.text.strip()}")

    def _set_push_to_talk(self, sender):
        self.tray_config["mode"] = "push_to_talk"
        save_tray_config(self.tray_config)
        log("Mode -> push_to_talk")

    def _set_toggle(self, sender):
        self.tray_config["mode"] = "toggle"
        save_tray_config(self.tray_config)
        log("Mode -> toggle")

    def _select_mic(self, sender):
        name = None if sender.title == "System Default" else sender.title
        self.tray_config["mic_device"] = name
        save_tray_config(self.tray_config)
        log(f"Mic -> {name!r}")

    def _quit(self, sender):
        self._keepalive_stop.set()
        self._listener.stop()
        self._http.close()
        log("Quitting")
        rumps.quit_application()


def main():
    try:
        LOG_PATH.unlink(missing_ok=True)
    except Exception:
        pass
    RemoteVoiceMacTray().run()


if __name__ == "__main__":
    main()
