import importlib
import sys
import time
import types
import unittest
from unittest import mock


def install_mac_stubs():
    if "rumps" not in sys.modules:
        rumps = types.ModuleType("rumps")

        class App:
            def __init__(self, *args, **kwargs):
                self.menu = []
                self.icon = None

            def run(self):
                return None

        class MenuItem:
            def __init__(self, title, callback=None):
                self.title = title
                self.callback = callback

            def __setitem__(self, key, value):
                return None

        class Window:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def run(self):
                return types.SimpleNamespace(clicked=False, text="")

        rumps.App = App
        rumps.MenuItem = MenuItem
        rumps.Window = Window
        rumps.alert = lambda **kwargs: None
        rumps.quit_application = lambda: None
        sys.modules["rumps"] = rumps

    if "sounddevice" not in sys.modules:
        sounddevice = types.ModuleType("sounddevice")

        class InputStream:
            def __init__(self, *args, **kwargs):
                self.samplerate = kwargs.get("samplerate") or 16000

            def start(self):
                return None

            def stop(self):
                return None

            def close(self):
                return None

        sounddevice.InputStream = InputStream
        sounddevice.query_devices = lambda: []
        sounddevice.stop = lambda: None
        sys.modules["sounddevice"] = sounddevice

    if "PIL" not in sys.modules:
        pil = types.ModuleType("PIL")
        image_mod = types.ModuleType("PIL.Image")
        draw_mod = types.ModuleType("PIL.ImageDraw")

        class FakeImage:
            def save(self, path):
                return None

        image_mod.new = lambda *args, **kwargs: FakeImage()
        draw_mod.Draw = lambda image: types.SimpleNamespace(
            ellipse=lambda *args, **kwargs: None,
            rectangle=lambda *args, **kwargs: None,
            arc=lambda *args, **kwargs: None,
            line=lambda *args, **kwargs: None,
        )
        pil.Image = image_mod
        pil.ImageDraw = draw_mod
        sys.modules["PIL"] = pil
        sys.modules["PIL.Image"] = image_mod
        sys.modules["PIL.ImageDraw"] = draw_mod

    if "pynput" not in sys.modules:
        pynput = types.ModuleType("pynput")
        keyboard = types.ModuleType("pynput.keyboard")

        class Controller:
            def type(self, text):
                return None

        class KeyCode:
            def __init__(self, char=None):
                self.char = char

        class Listener:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def start(self):
                return None

            def stop(self):
                return None

        keyboard.Controller = Controller
        keyboard.Key = types.SimpleNamespace(
            cmd_l="cmd_l",
            cmd_r="cmd_r",
            cmd="cmd",
            ctrl_l="ctrl_l",
            ctrl_r="ctrl_r",
            ctrl="ctrl",
            alt_l="alt_l",
            alt_r="alt_r",
            alt="alt",
            shift_l="shift_l",
            shift_r="shift_r",
            shift="shift",
        )
        keyboard.KeyCode = KeyCode
        keyboard.Listener = Listener
        pynput.keyboard = keyboard
        sys.modules["pynput"] = pynput
        sys.modules["pynput.keyboard"] = keyboard

    if "Quartz" not in sys.modules:
        quartz = types.ModuleType("Quartz")
        quartz.CGEventCreateKeyboardEvent = lambda *args, **kwargs: object()
        quartz.CGEventGetFlags = lambda *args, **kwargs: 0
        quartz.CGEventGetIntegerValueField = lambda *args, **kwargs: 0
        quartz.CGEventPost = lambda *args, **kwargs: None
        quartz.CGEventSetFlags = lambda *args, **kwargs: None
        quartz.CGEventTapCreate = lambda *args, **kwargs: None
        quartz.CGEventTapEnable = lambda *args, **kwargs: None
        quartz.CFMachPortCreateRunLoopSource = lambda *args, **kwargs: None
        quartz.CFRunLoopAddSource = lambda *args, **kwargs: None
        quartz.CFRunLoopGetCurrent = lambda: None
        quartz.kCFRunLoopCommonModes = 0
        quartz.kCGEventFlagMaskCommand = 1
        quartz.kCGEventFlagMaskControl = 2
        quartz.kCGEventFlagMaskAlternate = 4
        quartz.kCGEventFlagMaskShift = 8
        quartz.kCGEventFlagsChanged = 9
        quartz.kCGEventKeyDown = 10
        quartz.kCGEventKeyUp = 11
        quartz.kCGHIDEventTap = 12
        quartz.kCGKeyboardEventKeycode = 13
        quartz.kCGSessionEventTap = 14
        quartz.kCGTailAppendEventTap = 15
        sys.modules["Quartz"] = quartz


install_mac_stubs()
mac_tray = importlib.import_module("mac_tray")


class MacTrayDiagnosticsTests(unittest.TestCase):
    def test_build_device_attempts_prefers_specific_device_before_default(self):
        with mock.patch.object(mac_tray, "_find_device_indices", return_value=[3, 7]):
            attempts = mac_tray._build_device_attempts("Built-in Mic", 16000)

        self.assertEqual(
            attempts,
            [(3, 16000), (3, None), (7, 16000), (7, None), (None, 16000), (None, None)],
        )

    def test_run_with_watchdog_logs_begin_and_end(self):
        logs = []
        with mock.patch.object(mac_tray, "log", side_effect=logs.append):
            result = mac_tray._run_with_watchdog("diag-step", lambda: 42, timeout_s=0.1)

        self.assertEqual(result, 42)
        self.assertTrue(any("diag-step BEGIN" in msg for msg in logs))
        self.assertTrue(any("diag-step END" in msg for msg in logs))

    def test_run_with_watchdog_dumps_stacks_when_call_runs_too_long(self):
        dumps = []
        with (
            mock.patch.object(mac_tray, "log"),
            mock.patch.object(mac_tray, "_dump_all_thread_stacks", side_effect=dumps.append),
        ):
            mac_tray._run_with_watchdog("slow-step", lambda: time.sleep(0.05), timeout_s=0.01)

        self.assertEqual(len(dumps), 1)
        self.assertIn("slow-step hung", dumps[0])


if __name__ == "__main__":
    unittest.main()
