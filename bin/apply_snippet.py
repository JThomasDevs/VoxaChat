"""Apply a chat fence body to a workspace file (user-confirmed)."""
from __future__ import annotations

from pathlib import Path
from typing import List

import fs_tools as ft


def apply_snippet(path: str, content: str, workspace_roots: List[str]) -> str:
    if path is None or not str(path).strip():
        raise ValueError("path required")
    if not workspace_roots:
        raise ValueError("workspace_roots required")
    roots = [Path(r).expanduser() for r in workspace_roots]
    p = str(path).strip()
    body = "" if content is None else content
    resolved = ft.resolve_in_workspace(p, roots, for_write=True)
    ft.run_tool("write_file", {"path": p, "content": body}, roots)
    return str(resolved)
