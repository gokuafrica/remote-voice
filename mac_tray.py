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
    CGEventPost,
    CGEventSetFlags,
    kCGEventFlagMaskCommand,
    kCGHIDEventTap,
)

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


def transcribe(audio_bytes: bytes, server_url: str) -> str:
    url = (
        f"{server_url}/asr"
        f"?encode=true&task=transcribe&language=en"
        f"&word_timestamps=false&output=txt"
    )
    resp = requests.post(
        url,
        files={"audio_file": ("audio.wav", audio_bytes, "audio/wav")},
        timeout=30,
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

        self.menu = [
            rumps.MenuItem(f"Hotkey: {hotkey_display} ({mode_label})", callback=None),
            None,
            rumps.MenuItem("Server URL...", callback=self._set_server_url),
            rumps.MenuItem("Mode", [
                rumps.MenuItem("Push to Talk (hold hotkey)", callback=self._set_push_to_talk),
                rumps.MenuItem("Toggle (press twice)", callback=self._set_toggle),
            ]),
            rumps.MenuItem("Microphone", self._build_mic_menu()),
            None,
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        # Keyboard listener
        self._listener = Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        log("Keyboard listener started")

        # First-run check
        if not TRAY_CONFIG_PATH.exists() or self.tray_config.get("server_url") == TRAY_DEFAULTS["server_url"]:
            save_tray_config(self.tray_config)
            rumps.alert(
                title="Welcome to Remote Voice!",
                message="Set your Windows PC's Tailscale IP via the 'Server URL...' menu item.",
            )

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
            server_url = self.tray_config.get("server_url", TRAY_DEFAULTS["server_url"])
            log(f"Sending {len(audio_bytes)} bytes to {server_url}...")
            text = transcribe(audio_bytes, server_url)
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

    def _build_mic_menu(self):
        device_names = get_unique_devices()
        items = [rumps.MenuItem("System Default", callback=self._select_mic)]
        for name in device_names:
            items.append(rumps.MenuItem(name, callback=self._select_mic))
        return items

    def _quit(self, sender):
        self._listener.stop()
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
