import importlib
import sys
import types
import unittest
from unittest import mock


def install_tray_stubs():
    if "keyboard" not in sys.modules:
        keyboard = types.ModuleType("keyboard")
        keyboard.send = lambda *args, **kwargs: None
        keyboard.write = lambda *args, **kwargs: None
        keyboard.hook = lambda *args, **kwargs: None
        keyboard.unhook_all = lambda *args, **kwargs: None
        keyboard.parse_hotkey = lambda *args, **kwargs: [[(1,), (2,)]]
        keyboard.KEY_DOWN = "down"
        keyboard.KEY_UP = "up"
        sys.modules["keyboard"] = keyboard

    if "pystray" not in sys.modules:
        pystray = types.ModuleType("pystray")

        class Menu:
            SEPARATOR = object()

            def __init__(self, *items):
                self.items = items

        class MenuItem:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

        class Icon:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def run(self):
                return None

            def stop(self):
                return None

        pystray.Menu = Menu
        pystray.MenuItem = MenuItem
        pystray.Icon = Icon
        sys.modules["pystray"] = pystray

    if "requests" not in sys.modules:
        requests = types.ModuleType("requests")
        requests.Session = lambda: types.SimpleNamespace(close=lambda: None)
        sys.modules["requests"] = requests

    if "sounddevice" not in sys.modules:
        sounddevice = types.ModuleType("sounddevice")
        sounddevice.query_devices = lambda: []
        sounddevice.InputStream = type("InputStream", (), {})
        sounddevice.stop = lambda: None
        sys.modules["sounddevice"] = sounddevice

    if "PIL" not in sys.modules:
        pil = types.ModuleType("PIL")
        image_mod = types.ModuleType("PIL.Image")
        draw_mod = types.ModuleType("PIL.ImageDraw")

        class FakeImage:
            def save(self, *args, **kwargs):
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
        keyboard_mod = types.ModuleType("pynput.keyboard")
        keyboard_mod.Controller = type("Controller", (), {})
        pynput.keyboard = keyboard_mod
        sys.modules["pynput"] = pynput
        sys.modules["pynput.keyboard"] = keyboard_mod


install_tray_stubs()
tray = importlib.import_module("tray")


class TrayClipboardTests(unittest.TestCase):
    def run_transaction(self, text="dictated", backup="copied", sequences=(10, 10), set_result=True):
        paste = mock.Mock()
        typed = mock.Mock()
        sleep = mock.Mock()
        with (
            mock.patch.object(tray, "_get_clipboard_text", return_value=backup),
            mock.patch.object(tray, "_set_clipboard", return_value=set_result) as set_clipboard,
            mock.patch.object(tray, "_clipboard_sequence_number", side_effect=list(sequences)),
            mock.patch.object(tray, "log"),
        ):
            result = tray._paste_text_preserving_clipboard(text, paste, typed, sleep)
        return result, paste, typed, sleep, set_clipboard

    def test_existing_text_clipboard_is_restored_after_paste(self):
        result, paste, typed, sleep, set_clipboard = self.run_transaction()

        self.assertTrue(result)
        self.assertEqual(set_clipboard.call_args_list, [mock.call("dictated"), mock.call("copied")])
        paste.assert_called_once_with()
        typed.assert_not_called()
        self.assertEqual(sleep.call_args_list, [mock.call(0.05), mock.call(0.15)])

    def test_no_text_clipboard_falls_back_to_typing(self):
        paste = mock.Mock()
        typed = mock.Mock()
        with (
            mock.patch.object(tray, "_get_clipboard_text", return_value=None),
            mock.patch.object(tray, "_set_clipboard") as set_clipboard,
            mock.patch.object(tray, "log"),
        ):
            result = tray._paste_text_preserving_clipboard("dictated", paste, typed, mock.Mock())

        self.assertFalse(result)
        typed.assert_called_once_with("dictated")
        paste.assert_not_called()
        set_clipboard.assert_not_called()

    def test_clipboard_read_failure_falls_back_to_typing(self):
        paste = mock.Mock()
        typed = mock.Mock()
        with (
            mock.patch.object(tray, "_get_clipboard_text", side_effect=RuntimeError("locked")),
            mock.patch.object(tray, "_set_clipboard") as set_clipboard,
            mock.patch.object(tray, "log"),
        ):
            result = tray._paste_text_preserving_clipboard("dictated", paste, typed, mock.Mock())

        self.assertFalse(result)
        typed.assert_called_once_with("dictated")
        paste.assert_not_called()
        set_clipboard.assert_not_called()

    def test_temporary_write_failure_falls_back_to_typing(self):
        result, paste, typed, _sleep, set_clipboard = self.run_transaction(set_result=False)

        self.assertFalse(result)
        set_clipboard.assert_called_once_with("dictated")
        typed.assert_called_once_with("dictated")
        paste.assert_not_called()

    def test_changed_clipboard_sequence_skips_restore(self):
        result, paste, typed, _sleep, set_clipboard = self.run_transaction(sequences=(10, 11))

        self.assertTrue(result)
        set_clipboard.assert_called_once_with("dictated")
        paste.assert_called_once_with()
        typed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
