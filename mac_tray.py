"""
Remote Voice — macOS Menu Bar Client

Records audio via hotkey, sends to Windows PC server over Tailscale, pastes result.
No transcription runs on Mac. Server (server.py) runs on Windows with NVIDIA GPU.

macOS port of tray.py — replaces Windows-specific libraries:
  pystray → rumps, keyboard → pynput, Win32 clipboard → pbcopy, Ctrl+V → CGEvent Cmd+V
"""

import faulthandler
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
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
    kCGEventFlagsChanged,
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
_EVENT_TYPE_NAMES = {
    kCGEventFlagsChanged: "flags_changed",
    kCGEventKeyDown: "key_down",
    kCGEventKeyUp: "key_up",
}

TRAY_CONFIG_PATH = Path(__file__).parent / "mac_tray_config.json"

TRAY_DEFAULTS = {
    "server_url": "http://100.x.y.z:8787",
    "hotkey_modifier": "cmd",
    "hotkey_key": "'",
    "mic_device": None,
    "sample_rate": 16000,
    "mode": "push_to_talk",
}

DEBOUNCE_MS = 30
AUDIO_WATCHDOG_S = 2.0
MIC_STRESS_CYCLES = 25
MIC_STRESS_HOLD_S = 0.35
MIC_STRESS_PAUSE_S = 0.20
AUDIO_INACTIVE_WAIT_S = 0.25


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
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def _thread_label() -> str:
    t = threading.current_thread()
    return f"{t.name}@{threading.get_ident()}"


def _dump_all_thread_stacks(reason: str):
    log(f"THREAD DUMP START ({reason})")
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    log(f"THREAD DUMP END   ({reason})")


def _run_with_watchdog(label: str, func, timeout_s: float = AUDIO_WATCHDOG_S):
    log(f"{label} BEGIN [thread={_thread_label()}]")
    timer = threading.Timer(timeout_s, _dump_all_thread_stacks, args=(f"{label} hung > {timeout_s:.1f}s",))
    timer.daemon = True
    timer.start()
    started = time.monotonic()
    try:
        return func()
    finally:
        elapsed_ms = (time.monotonic() - started) * 1000
        timer.cancel()
        log(f"{label} END ({elapsed_ms:.1f}ms) [thread={_thread_label()}]")


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


@dataclass
class HotkeySuppressState:
    quote_latched: bool = False
    last_suppress_ts: float = 0.0


@dataclass
class HotkeySuppressDecision:
    suppress: bool
    action: str
    state: HotkeySuppressState


def evaluate_hotkey_suppression(
    *,
    event_type: str,
    keycode: int,
    hotkey_keycode: int,
    modifier_flag_active: bool,
    modifier_pressed: bool,
    combo_active: bool,
    state: HotkeySuppressState,
    now: float,
) -> HotkeySuppressDecision:
    """Latch the hotkey key until release so stray apostrophes cannot leak."""
    if keycode != hotkey_keycode:
        return HotkeySuppressDecision(False, "not_hotkey_key", state)

    context_active = (
        modifier_flag_active
        or modifier_pressed
        or combo_active
        or state.quote_latched
    )

    if event_type == "key_down":
        if context_active:
            return HotkeySuppressDecision(
                True,
                "suppress_hotkey_down",
                HotkeySuppressState(True, now),
            )
        return HotkeySuppressDecision(False, "pass_through", state)

    if event_type == "key_up":
        if state.quote_latched:
            return HotkeySuppressDecision(
                True,
                "suppress_latched_keyup",
                HotkeySuppressState(False, now),
            )
        if modifier_flag_active or modifier_pressed or combo_active:
            return HotkeySuppressDecision(
                True,
                "suppress_hotkey_keyup",
                HotkeySuppressState(False, now),
            )
        return HotkeySuppressDecision(False, "pass_through", state)

    return HotkeySuppressDecision(False, "pass_through", state)


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
    devices = _run_with_watchdog("Audio device query (menu)", sd.query_devices)
    names = set()
    for d in devices:
        if d["max_input_channels"] > 0:
            cn = _clean_device_name(d["name"])
            if cn:
                names.add(cn)
    return sorted(names)


def _find_device_indices(device_name: str) -> list[int]:
    """Find PortAudio index for a clean device name."""
    devices = _run_with_watchdog(f"Audio device query (match {device_name!r})", sd.query_devices)
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
        timeout=(5, 10),  # 5s connect, 10s read
    )
    resp.raise_for_status()
    return resp.text.strip()


# ---------------------------------------------------------------------------
# Tray App
# ---------------------------------------------------------------------------
class RemoteVoiceMacTray(rumps.App):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    PROCESSING = "processing"

    def __init__(self):
        super().__init__("Remote Voice", quit_button=None)
        self.tray_config = load_tray_config()
        self.state = self.IDLE
        self.frames = deque()
        self.stream = None
        self.typer = KBController()
        self.actual_sr = 16000
        self._lock = threading.Lock()
        self._op_lock = threading.Lock()
        self._audio_op_seq = 0
        self._stream_inactive = threading.Event()
        self._stop_requested = threading.Event()
        self._audio_poisoned = False
        self._audio_poison_reason = ""
        self._http = requests.Session()
        self._http.headers["Connection"] = "close"  # fresh TCP per request (no stale connections)
        self._keepalive_http = requests.Session()  # separate session for keepalive
        self._stress_test_active = False

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
            rumps.MenuItem(f"Run Mic Stress Test ({MIC_STRESS_CYCLES}x)", callback=self._start_mic_stress_test),
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

    def _next_audio_op(self, kind: str) -> str:
        with self._op_lock:
            self._audio_op_seq += 1
            seq = self._audio_op_seq
        return f"{kind}#{seq}"

    def _set_state(self, new_state: str, reason: str):
        old_state = self.state
        self.state = new_state
        log(f"State {old_state} -> {new_state} ({reason})")

    def _poison_audio(self, reason: str):
        if not self._audio_poisoned:
            log(f"ERROR: audio engine poisoned ({reason})")
        self._audio_poisoned = True
        self._audio_poison_reason = reason
        self._set_state(self.IDLE, f"audio disabled: {reason}")
        self.update_icon("gray")

    def _build_input_stream(self, callback, finished_callback, try_dev, try_sr):
        return sd.InputStream(
            samplerate=try_sr,
            channels=1,
            dtype="int16",
            device=try_dev,
            callback=callback,
            finished_callback=finished_callback,
        )

    def _close_stream_with_logging(self, stream, op_id: str):
        def _close():
            inactive = False
            try:
                inactive = self._stream_inactive.wait(AUDIO_INACTIVE_WAIT_S)
                if inactive:
                    log(f"{op_id} stream became inactive via callback")
                else:
                    log(f"{op_id} stream still active after {AUDIO_INACTIVE_WAIT_S:.2f}s — aborting")
                    _run_with_watchdog(f"{op_id} stream.abort", stream.abort)
                    inactive = self._stream_inactive.wait(AUDIO_INACTIVE_WAIT_S)
                    if inactive:
                        log(f"{op_id} stream became inactive after abort")
                    else:
                        log(f"{op_id} stream still active after abort")
            except Exception as e:
                log(f"{op_id} stream.abort error: {e}")
            try:
                _run_with_watchdog(f"{op_id} stream.close", stream.close)
            except Exception as e:
                log(f"{op_id} stream.close error: {e}")

        worker = threading.Thread(target=_close, daemon=True, name=f"rv-close-{op_id}")
        worker.start()
        worker.join(timeout=AUDIO_WATCHDOG_S)
        if worker.is_alive():
            log(f"WARNING: {op_id} stream close timed out — continuing anyway")
            self._poison_audio(f"{op_id} close timed out")
        else:
            log(f"{op_id} stream close finished")

    def _open_input_stream(self, op_id: str, device_name: str | None, sr: int, callback):
        attempts = _build_device_attempts(device_name, sr)
        log(f"{op_id} Device attempts: {attempts}")

        for try_dev, try_sr in attempts:
            result = {}
            done = threading.Event()

            def finished_callback():
                self._stream_inactive.set()

            def _open():
                stream = None
                try:
                    stream = _run_with_watchdog(
                        f"{op_id} InputStream(dev={try_dev}, sr={try_sr})",
                        lambda try_dev=try_dev, try_sr=try_sr: self._build_input_stream(
                            callback,
                            finished_callback,
                            try_dev,
                            try_sr,
                        ),
                    )
                    _run_with_watchdog(
                        f"{op_id} stream.start(dev={try_dev}, sr={try_sr})",
                        stream.start,
                    )
                    result["stream"] = stream
                    result["actual_sr"] = int(stream.samplerate)
                    log(f"{op_id} Mic opened: dev={try_dev}, sr={result['actual_sr']}")
                except Exception as e:
                    result["error"] = e
                    if stream is not None:
                        try:
                            _run_with_watchdog(f"{op_id}/cleanup stream.close", stream.close)
                        except Exception as cleanup_error:
                            log(f"{op_id}/cleanup stream.close error: {cleanup_error}")
                finally:
                    done.set()

            worker = threading.Thread(target=_open, daemon=True, name=f"rv-open-{op_id}")
            worker.start()
            if not done.wait(AUDIO_WATCHDOG_S):
                log(f"ERROR: {op_id} mic open timed out (dev={try_dev}, sr={try_sr})")
                self._poison_audio(f"{op_id} open timed out")
                return None, None

            if "stream" in result:
                return result["stream"], result["actual_sr"]

            log(f"{op_id} Mic fail (dev={try_dev}, sr={try_sr}): {result['error']}")

        return None, None

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

        self._hotkey_suppress_state = HotkeySuppressState()

        def tap_callback(proxy, event_type, event, refcon):
            if event_type in (kCGEventFlagsChanged, kCGEventKeyDown, kCGEventKeyUp):
                kc = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                flags = CGEventGetFlags(event)
                if event_type in (kCGEventKeyDown, kCGEventKeyUp):
                    decision = evaluate_hotkey_suppression(
                        event_type=_EVENT_TYPE_NAMES[event_type],
                        keycode=kc,
                        hotkey_keycode=keycode,
                        modifier_flag_active=bool(flags & mod_flag),
                        modifier_pressed=self._hotkey_mod in self._pressed_keys,
                        combo_active=self._combo_active,
                        state=self._hotkey_suppress_state,
                        now=time.monotonic(),
                    )
                    self._hotkey_suppress_state = decision.state
                    if decision.suppress:
                        return None
            return event

        event_mask = (
            (1 << kCGEventFlagsChanged)
            | (1 << kCGEventKeyDown)
            | (1 << kCGEventKeyUp)
        )
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
            normalized = self._normalize_key(key)
            self._pressed_keys.add(normalized)

            held = self._is_combo_held()
            mode = self.tray_config.get("mode", "push_to_talk")

            if held and not self._combo_active:
                now = time.monotonic()
                if (now - self._last_activate) * 1000 >= DEBOUNCE_MS:
                    self._last_activate = now
                    self._combo_active = True
                    log(f"Combo ON  (mode={mode}, state={self.state})")

                    if self._stress_test_active:
                        log("Combo ignored while mic stress test is running")
                    elif mode == "push_to_talk":
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
            normalized = self._normalize_key(key)
            self._pressed_keys.discard(normalized)

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
        """Ping server every 30s to keep Tailscale tunnel warm."""
        self._server_reachable = True  # assume connected at start
        while not self._keepalive_stop.wait(30):
            try:
                server_url = self.tray_config.get("server_url", TRAY_DEFAULTS["server_url"])
                self._keepalive_http.head(server_url, timeout=5)
                if not self._server_reachable:
                    log("Server connection restored")
                    self._server_reachable = True
            except Exception:
                if self._server_reachable:
                    log("Server unreachable — will keep retrying")
                    self._server_reachable = False

    # ---- Recording ----------------------------------------------------------

    def _do_start(self):
        op_id = self._next_audio_op("start")
        wait_started = time.monotonic()
        log(f"{op_id} Start requested (state={self.state}, combo_active={self._combo_active})")
        with self._lock:
            waited_ms = (time.monotonic() - wait_started) * 1000
            log(f"{op_id} Audio lock acquired after {waited_ms:.1f}ms")
            if self._audio_poisoned:
                log(f"{op_id} Start blocked — audio restart required ({self._audio_poison_reason})")
                return
            if self.state != self.IDLE:
                log(f"{op_id} Start ignored (state={self.state})")
                return
            opened = self._start_recording(op_id)
            if (
                opened
                and self.state == self.RECORDING
                and not self._combo_active
                and self.tray_config.get("mode", "push_to_talk") == "push_to_talk"
            ):
                log(f"{op_id} Combo released during mic open — auto-stopping")
                self._stop_recording(self._next_audio_op("auto-stop"))
        log(f"{op_id} Audio lock released")

    def _do_stop(self):
        op_id = self._next_audio_op("stop")
        wait_started = time.monotonic()
        log(f"{op_id} Stop requested (state={self.state})")
        with self._lock:
            waited_ms = (time.monotonic() - wait_started) * 1000
            log(f"{op_id} Audio lock acquired after {waited_ms:.1f}ms")
            if self.state != self.RECORDING:
                log(f"{op_id} Stop ignored (state={self.state})")
                return
            self._stop_recording(op_id)
        log(f"{op_id} Audio lock released")

    def _start_recording(self, op_id: str) -> bool:
        if self._audio_poisoned:
            log(f"{op_id} Start refused — audio restart required ({self._audio_poison_reason})")
            return False

        self._set_state(self.STARTING, f"{op_id} preparing to open microphone")
        self.frames.clear()
        self._stop_requested.clear()
        self._stream_inactive.clear()
        device_name = self.tray_config.get("mic_device")
        sr = self.tray_config.get("sample_rate", 16000)
        log(f"{op_id} Rec start (mic={device_name!r}, sr={sr})")

        def callback(indata, frame_count, time_info, status):
            if self.state == self.RECORDING:
                self.frames.append(indata.copy())
            if self._stop_requested.is_set():
                raise sd.CallbackAbort()

        stream, actual_sr = self._open_input_stream(op_id, device_name, sr, callback)
        if stream is None:
            log(f"{op_id} ERROR: all mic attempts failed")
            self.stream = None
            self._set_state(self.IDLE, f"{op_id} all mic attempts failed")
            self.update_icon("gray")
            return False

        self.stream = stream
        self.actual_sr = actual_sr
        self._set_state(self.RECORDING, f"{op_id} microphone opened")
        self.update_icon("red")
        return True

    def _stop_recording(self, op_id: str, *, process_audio: bool = True):
        self._set_state(self.STOPPING, f"{op_id} stopping microphone")
        log(f"{op_id} Rec stop ({len(self.frames)} frames)")
        self._stop_requested.set()

        if self.stream:
            stream = self.stream
            self.stream = None
            self._close_stream_with_logging(stream, op_id)

        if not self.frames:
            log(f"{op_id} No audio captured")
            self._set_state(self.IDLE, f"{op_id} stop complete with no audio")
            self.update_icon("gray")
            return

        if not process_audio:
            log(f"{op_id} Discarding captured audio from stress test")
            self.frames = []
            self._set_state(self.IDLE, f"{op_id} stress cycle complete")
            self.update_icon("gray")
            return

        self._set_state(self.PROCESSING, f"{op_id} sending audio for transcription")
        self.update_icon("blue")
        threading.Thread(target=self._process_audio, daemon=True).start()

    def _run_mic_stress_test(self):
        log(
            f"Mic stress test starting ({MIC_STRESS_CYCLES} cycles, "
            f"hold={MIC_STRESS_HOLD_S:.2f}s, pause={MIC_STRESS_PAUSE_S:.2f}s)"
        )
        self._stress_test_active = True
        try:
            for cycle in range(1, MIC_STRESS_CYCLES + 1):
                with self._lock:
                    if self.state != self.IDLE:
                        log(f"Mic stress test aborted before cycle {cycle} (state={self.state})")
                        return
                    start_id = self._next_audio_op(f"stress-start-{cycle}")
                    log(f"{start_id} Stress cycle {cycle}/{MIC_STRESS_CYCLES} begin")
                    opened = self._start_recording(start_id)
                    if not opened:
                        log(f"{start_id} Stress cycle {cycle} failed during start")
                        return

                time.sleep(MIC_STRESS_HOLD_S)

                with self._lock:
                    if self.state != self.RECORDING:
                        log(f"Mic stress test aborted during cycle {cycle} (state={self.state})")
                        return
                    stop_id = self._next_audio_op(f"stress-stop-{cycle}")
                    self._stop_recording(stop_id, process_audio=False)

                time.sleep(MIC_STRESS_PAUSE_S)

            log("Mic stress test completed")
        finally:
            self._stress_test_active = False

    def _run_encoder(self, cmd: list[str], timeout: int = 10) -> bool:
        """Run an audio encoder subprocess with proper timeout and cleanup."""
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc.wait(timeout=timeout)
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, cmd[0])
            return True
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
        except FileNotFoundError:
            raise

    def _compress_audio(self, wav_path: str) -> tuple[bytes, str, str]:
        """Compress WAV before sending. Tries ffmpeg (Opus), afconvert (AAC), falls back to WAV."""
        wav_size = os.path.getsize(wav_path)

        # Try ffmpeg → OGG/Opus (best compression for speech, ~10-20x smaller)
        try:
            ogg_path = wav_path.replace(".wav", ".ogg")
            self._run_encoder(
                ["ffmpeg", "-y", "-i", wav_path, "-c:a", "libopus", "-b:a", "32k", ogg_path]
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
            self._run_encoder(
                ["afconvert", "-f", "m4af", "-d", "aac", wav_path, m4a_path]
            )
            with open(m4a_path, "rb") as f:
                data = f.read()
            log(f"Compressed (AAC): {wav_size} -> {len(data)} bytes ({len(data)*100//wav_size}%)")
            os.unlink(m4a_path)
            return data, "audio.m4a", "audio/mp4"
        except Exception as e:
            log(f"afconvert failed: {e}")

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

            duration = sum(len(f) for f in self.frames) / self.actual_sr
            if duration < 5:
                with open(wav_path, "rb") as f:
                    audio_bytes = f.read()
                filename, mime = "audio.wav", "audio/wav"
            else:
                audio_bytes, filename, mime = self._compress_audio(wav_path)
            os.unlink(wav_path)

            server_url = self.tray_config.get("server_url", TRAY_DEFAULTS["server_url"])
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
            self._set_state(self.IDLE, "transcription finished")
            self.update_icon("gray")
            self.frames = deque()

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

    def _start_mic_stress_test(self, sender):
        if self._stress_test_active:
            log("Mic stress test already running")
            return
        if self.state != self.IDLE:
            log(f"Mic stress test unavailable while state={self.state}")
            return
        threading.Thread(target=self._run_mic_stress_test, daemon=True, name="rv-mic-stress").start()

    def _quit(self, sender):
        self._keepalive_stop.set()
        self._listener.stop()
        self._http.close()
        self._keepalive_http.close()
        log("Quitting")
        rumps.quit_application()


def main():
    RemoteVoiceMacTray().run()


if __name__ == "__main__":
    main()
