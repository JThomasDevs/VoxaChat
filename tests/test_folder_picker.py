"""Unit tests for native folder picker helpers."""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Ensure bin/ is importable the same way server.py does.
BIN = os.path.join(os.path.dirname(__file__), "..", "bin")
sys.path.insert(0, os.path.abspath(BIN))

import folder_picker as fp  # noqa: E402


class FolderPickerTests(unittest.TestCase):
    def test_pick_folder_returns_abspath(self):
        fake_tk = types.ModuleType("tkinter")
        fake_tk.TclError = type("TclError", (Exception,), {})
        fake_root = MagicMock()
        fake_tk.Tk = MagicMock(return_value=fake_root)

        fake_fd = types.ModuleType("tkinter.filedialog")
        chosen = os.path.join(os.getcwd(), "tmp-pick")
        fake_fd.askdirectory = MagicMock(return_value=chosen)

        with patch.dict(sys.modules, {"tkinter": fake_tk, "tkinter.filedialog": fake_fd}):
            with patch("os.path.isdir", return_value=True):
                result = fp.pick_folder()

        self.assertEqual(result, os.path.abspath(chosen))
        fake_root.withdraw.assert_called_once()
        fake_root.destroy.assert_called_once()

    def test_pick_folder_cancelled_returns_none(self):
        fake_tk = types.ModuleType("tkinter")
        fake_tk.TclError = type("TclError", (Exception,), {})
        fake_root = MagicMock()
        fake_tk.Tk = MagicMock(return_value=fake_root)

        fake_fd = types.ModuleType("tkinter.filedialog")
        fake_fd.askdirectory = MagicMock(return_value="")

        with patch.dict(sys.modules, {"tkinter": fake_tk, "tkinter.filedialog": fake_fd}):
            result = fp.pick_folder()

        self.assertIsNone(result)

    def test_pick_folder_raises_when_tkinter_missing(self):
        real_import = __import__

        def _import(name, *args, **kwargs):
            if name == "tkinter" or name.startswith("tkinter."):
                raise ImportError("no tk")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_import):
            with self.assertRaises(fp.FolderPickerError):
                fp.pick_folder()


if __name__ == "__main__":
    unittest.main()
