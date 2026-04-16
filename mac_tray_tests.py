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

        class CallbackAbort(Exception):
            pass

        class InputStream:
            def __init__(self, *args, **kwargs):
                self.samplerate = kwargs.get("samplerate") or 16000

            def start(self):
                return None

            def abort(self):
                return None

            def close(self):
                return None

        sounddevice.CallbackAbort = CallbackAbort
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

    if "AppKit" not in sys.modules:
        appkit = types.ModuleType("AppKit")

        class NSPasteboard:
            @staticmethod
            def generalPasteboard():
                return None

        appkit.NSPasteboard = NSPasteboard
        appkit.NSPasteboardTypeString = "public.utf8-plain-text"
        sys.modules["AppKit"] = appkit


install_mac_stubs()
mac_tray = importlib.import_module("mac_tray")


class MacTrayDiagnosticsTests(unittest.TestCase):
    def make_app(self):
        app = mac_tray.RemoteVoiceMacTray()
        self.addCleanup(app._keepalive_stop.set)
        self.addCleanup(app._listener.stop)
        return app

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

    def test_do_start_refuses_when_audio_is_poisoned(self):
        app = self.make_app()
        app._audio_poisoned = True
        app._audio_poison_reason = "prior close timed out"

        logs = []
        with mock.patch.object(mac_tray, "log", side_effect=logs.append):
            app._do_start()

        self.assertTrue(any("Start blocked" in msg for msg in logs))

    def test_close_timeout_poisons_audio_engine(self):
        app = self.make_app()
        app._stream_inactive.clear()

        class SlowAbortStream:
            def abort(self):
                time.sleep(0.05)

            def close(self):
                return None

        with mock.patch.object(mac_tray, "AUDIO_WATCHDOG_S", 0.01), mock.patch.object(
            mac_tray, "AUDIO_INACTIVE_WAIT_S", 0.01
        ):
            app._close_stream_with_logging(SlowAbortStream(), "stop#1")

        self.assertTrue(app._audio_poisoned)
        self.assertIn("close timed out", app._audio_poison_reason)

    def test_existing_text_clipboard_is_restored_after_paste(self):
        paste = mock.Mock()
        typed = mock.Mock()
        sleep = mock.Mock()
        with (
            mock.patch.object(mac_tray, "_get_clipboard_text", return_value="copied"),
            mock.patch.object(mac_tray, "_set_clipboard", return_value=True) as set_clipboard,
            mock.patch.object(mac_tray, "_clipboard_change_count", side_effect=[7, 7]),
            mock.patch.object(mac_tray, "log"),
        ):
            result = mac_tray._paste_text_preserving_clipboard("dictated", paste, typed, sleep)

        self.assertTrue(result)
        self.assertEqual(
            set_clipboard.call_args_list,
            [mock.call("dictated", transient=True), mock.call("copied")],
        )
        paste.assert_called_once_with()
        typed.assert_not_called()
        self.assertEqual(sleep.call_args_list, [mock.call(0.05), mock.call(0.15)])

    def test_no_text_clipboard_falls_back_to_typer(self):
        paste = mock.Mock()
        typed = mock.Mock()
        with (
            mock.patch.object(mac_tray, "_get_clipboard_text", return_value=None),
            mock.patch.object(mac_tray, "_set_clipboard") as set_clipboard,
            mock.patch.object(mac_tray, "log"),
        ):
            result = mac_tray._paste_text_preserving_clipboard("dictated", paste, typed, mock.Mock())

        self.assertFalse(result)
        typed.assert_called_once_with("dictated")
        paste.assert_not_called()
        set_clipboard.assert_not_called()

    def test_changed_clipboard_change_count_skips_restore(self):
        paste = mock.Mock()
        typed = mock.Mock()
        with (
            mock.patch.object(mac_tray, "_get_clipboard_text", return_value="copied"),
            mock.patch.object(mac_tray, "_set_clipboard", return_value=True) as set_clipboard,
            mock.patch.object(mac_tray, "_clipboard_change_count", side_effect=[7, 8]),
            mock.patch.object(mac_tray, "log"),
        ):
            result = mac_tray._paste_text_preserving_clipboard("dictated", paste, typed, mock.Mock())

        self.assertTrue(result)
        set_clipboard.assert_called_once_with("dictated", transient=True)
        paste.assert_called_once_with()
        typed.assert_not_called()

    def test_transient_write_marks_pasteboard_as_autogenerated(self):
        class FakePasteboard:
            def __init__(self):
                self.cleared = False
                self.writes = []

            def clearContents(self):
                self.cleared = True

            def setString_forType_(self, text, pasteboard_type):
                self.writes.append((text, pasteboard_type))
                return True

        fake = FakePasteboard()
        fake_ns_pasteboard = types.SimpleNamespace(generalPasteboard=lambda: fake)

        with mock.patch.object(mac_tray, "NSPasteboard", fake_ns_pasteboard):
            result = mac_tray._set_clipboard("dictated", transient=True)

        self.assertTrue(result)
        self.assertTrue(fake.cleared)
        self.assertIn(("dictated", mac_tray.NSPasteboardTypeString), fake.writes)
        self.assertIn(("", mac_tray.PASTEBOARD_TRANSIENT_TYPE), fake.writes)
        self.assertIn(("", mac_tray.PASTEBOARD_AUTOGENERATED_TYPE), fake.writes)


if __name__ == "__main__":
    unittest.main()
