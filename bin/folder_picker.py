"""Native directory picker for local desktop use (returns absolute path)."""
from __future__ import annotations

import os
import sys
from typing import Optional


class FolderPickerError(RuntimeError):
    """Raised when the native folder dialog cannot be shown."""


def pick_folder(initial_dir: Optional[str] = None) -> Optional[str]:
    """
    Open a system directory chooser and return an absolute path, or None if cancelled.

    Uses tkinter filedialog so the browser receives a real filesystem path
    (unlike the File System Access API's opaque handles).
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:  # pragma: no cover - depends on OS/python build
        raise FolderPickerError(f"tkinter unavailable: {e}") from e

    start = None
    if initial_dir:
        candidate = os.path.abspath(os.path.expanduser(initial_dir))
        if os.path.isdir(candidate):
            start = candidate

    root = tk.Tk()
    root.withdraw()
    try:
        # Keep dialog above the browser window when possible.
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        root.update_idletasks()
        kwargs = {
            "mustexist": True,
            "title": "Choose workspace folder",
        }
        if start:
            kwargs["initialdir"] = start
        chosen = filedialog.askdirectory(**kwargs)
    finally:
        try:
            root.destroy()
        except Exception:
            pass

    if not chosen:
        return None
    path = os.path.abspath(chosen)
    if not os.path.isdir(path):
        raise FolderPickerError(f"not a directory: {path}")
    return path


def pick_folder_cli() -> int:
    """CLI helper for manual smoke tests: prints path or exits 1 if cancelled."""
    try:
        path = pick_folder()
    except FolderPickerError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not path:
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(pick_folder_cli())
