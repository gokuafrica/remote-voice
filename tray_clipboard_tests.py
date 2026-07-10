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
    def run_transaction(
        self,
        text="dictated",
        snapshot_ok=True,
        snapshot=mock.sentinel.snapshot,
        backup="copied",
        sequences=(10, 10),
        set_result=True,
        restore_result=True,
        ole_initialized=True,
        text_side_effect=None,
    ):
        paste = mock.Mock()
        typed = mock.Mock()
        sleep = mock.Mock()
        with (
            mock.patch.object(tray, "_init_ole_clipboard", return_value=ole_initialized),
            mock.patch.object(
                tray,
                "_capture_clipboard_snapshot",
                return_value=(snapshot_ok, snapshot),
            ) as capture_snapshot,
            mock.patch.object(
                tray,
                "_get_clipboard_text",
                return_value=backup,
                side_effect=text_side_effect,
            ) as get_clipboard_text,
            mock.patch.object(tray, "_set_clipboard", return_value=set_result) as set_clipboard,
            mock.patch.object(
                tray,
                "_restore_clipboard_snapshot",
                return_value=restore_result,
            ) as restore_clipboard,
            mock.patch.object(tray, "_clipboard_sequence_number", side_effect=list(sequences)),
            mock.patch.object(tray, "_uninit_ole_clipboard") as uninit_ole,
            mock.patch.object(tray, "log"),
        ):
            result = tray._paste_text_preserving_clipboard(text, paste, typed, sleep)
        return {
            "result": result,
            "paste": paste,
            "typed": typed,
            "sleep": sleep,
            "set_clipboard": set_clipboard,
            "restore_clipboard": restore_clipboard,
            "capture_snapshot": capture_snapshot,
            "get_clipboard_text": get_clipboard_text,
            "uninit_ole": uninit_ole,
        }

    def test_non_text_clipboard_snapshot_is_restored_after_paste(self):
        tx = self.run_transaction()

        self.assertTrue(tx["result"])
        tx["capture_snapshot"].assert_called_once()
        tx["get_clipboard_text"].assert_not_called()
        tx["set_clipboard"].assert_called_once_with("dictated")
        tx["restore_clipboard"].assert_called_once_with(mock.sentinel.snapshot, tx["sleep"])
        tx["paste"].assert_called_once_with()
        tx["typed"].assert_not_called()
        self.assertEqual(tx["sleep"].call_args_list, [mock.call(0.05), mock.call(0.15)])
        tx["uninit_ole"].assert_called_once_with()

    def test_text_clipboard_is_restored_when_snapshot_is_unavailable(self):
        tx = self.run_transaction(snapshot_ok=False, backup="copied")

        self.assertTrue(tx["result"])
        tx["capture_snapshot"].assert_called_once()
        tx["get_clipboard_text"].assert_called_once_with()
        self.assertEqual(
            tx["set_clipboard"].call_args_list,
            [mock.call("dictated"), mock.call("copied")],
        )
        tx["restore_clipboard"].assert_not_called()
        tx["paste"].assert_called_once_with()
        tx["typed"].assert_not_called()

    def test_no_clipboard_backup_falls_back_to_typing(self):
        tx = self.run_transaction(snapshot_ok=False, backup=None)

        self.assertFalse(tx["result"])
        tx["typed"].assert_called_once_with("dictated")
        tx["paste"].assert_not_called()
        tx["set_clipboard"].assert_not_called()
        tx["restore_clipboard"].assert_not_called()

    def test_temporary_write_failure_falls_back_to_typing(self):
        tx = self.run_transaction(set_result=False)

        self.assertFalse(tx["result"])
        tx["set_clipboard"].assert_called_once_with("dictated")
        tx["typed"].assert_called_once_with("dictated")
        tx["paste"].assert_not_called()
        tx["restore_clipboard"].assert_not_called()

    def test_changed_clipboard_sequence_skips_restore(self):
        tx = self.run_transaction(sequences=(10, 11))

        self.assertTrue(tx["result"])
        tx["set_clipboard"].assert_called_once_with("dictated")
        tx["restore_clipboard"].assert_not_called()
        tx["paste"].assert_called_once_with()
        tx["typed"].assert_not_called()

    def test_clipboard_read_failure_falls_back_to_typing(self):
        tx = self.run_transaction(
            snapshot_ok=False,
            backup=None,
            text_side_effect=RuntimeError("locked"),
        )

        self.assertFalse(tx["result"])
        tx["get_clipboard_text"].assert_called_once_with()
        tx["typed"].assert_called_once_with("dictated")
        tx["paste"].assert_not_called()
        tx["set_clipboard"].assert_not_called()

    def make_tray_for_hotkey(self):
        app = object.__new__(tray.RemoteVoiceTray)
        app.tray_config = {"hotkey": "right ctrl", "mode": "toggle"}
        app.state = tray.RemoteVoiceTray.IDLE
        app._pressed_scans = set()
        app._right_ctrl_pressed = False
        app._combo_active = False
        app._last_activate = 0.0
        app._combo_scan_sets = [set([57629, 29, 57373])]
        return app

    def key_event(self, event_type, name, scan_code):
        return types.SimpleNamespace(
            event_type=event_type,
            name=name,
            scan_code=scan_code,
        )

    def test_right_ctrl_hotkey_ignores_left_ctrl_event(self):
        app = self.make_tray_for_hotkey()

        with mock.patch.object(app, "_do_start") as start:
            app._on_key_event(self.key_event(tray.keyboard.KEY_DOWN, "left ctrl", 29))

        start.assert_not_called()
        self.assertFalse(app._combo_active)

    def test_right_ctrl_hotkey_accepts_right_ctrl_event(self):
        app = self.make_tray_for_hotkey()

        with mock.patch.object(app, "_do_start") as start:
            app._on_key_event(self.key_event(tray.keyboard.KEY_DOWN, "right ctrl", 29))

        start.assert_called_once_with()
        self.assertTrue(app._combo_active)


if __name__ == "__main__":
    unittest.main()
