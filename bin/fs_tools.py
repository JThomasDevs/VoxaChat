"""Filesystem tools for Voxa chat agent (Ollama tool-calling).

Path safety: every path is resolved and must stay under granted workspace
roots. Symlink escapes and a hard denylist fail closed.
"""
from __future__ import annotations

import difflib
import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_READ_BYTES = 256_000
MAX_SEARCH_HITS = 200
MAX_LIST_DEPTH = 3
WRITE_TOOLS = frozenset({"write_file", "mkdir", "delete_file", "move_path", "edit_file"})
DENY_DIR_NAMES = frozenset({".ssh", ".gnupg", ".aws"})
DENY_WRITE_SUFFIXES = (".pem", ".key")
# Repo root for optional .env loading (bin/fs_tools.py → parents[1])
_REPO_ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path | None = None, *, override: bool = False) -> int:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Returns number of keys applied. Does not override existing env vars unless
    override=True. Shell `export` in shell_run cannot reach the server process;
    put secrets in repo-root `.env` instead.
    """
    env_path = path or (_REPO_ROOT / ".env")
    if not env_path.is_file():
        return 0
    applied = 0
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if not key:
            continue
        if not override and key in os.environ and os.environ.get(key, "") != "":
            continue
        os.environ[key] = val
        applied += 1
    return applied


def _resolve_openweather_api_key() -> str:
    """Prefer process env; fall back to repo .env (reloaded each call)."""
    key = (os.environ.get("OPENWEATHERMAP_API_KEY") or "").strip()
    if key:
        return key
    load_dotenv(_REPO_ROOT / ".env", override=False)
    return (os.environ.get("OPENWEATHERMAP_API_KEY") or "").strip()

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files/directories under a path. Use when the user asks what's in a folder, to explore the project, or before reading unknown paths. Use path \".\" for the primary granted workspace root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (absolute, or relative to the primary workspace root; \".\" = primary root)",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "How deep to recurse (default 1, max 3)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file (max ~256KB). Use instead of guessing file contents when answering about code or configs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Glob search for files under a root (e.g. **/*.py). Use to locate files by name/pattern before reading.",
            "parameters": {
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "Search root directory"},
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. **/*.py",
                    },
                },
                "required": ["root", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a whole text file. Prefer edit_file for small surgical changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mkdir",
            "description": "Create a directory (including parents).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a single file (not a directory).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_path",
            "description": "Rename or move a file/directory within the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "src": {"type": "string"},
                    "dst": {"type": "string"},
                },
                "required": ["src", "dst"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Current weather for a city (OpenWeatherMap). Use for weather/temperature/forecast questions. Requires OPENWEATHERMAP_API_KEY in the server env or repo-root .env — do NOT try to set it via shell_run (exports die with that subprocess).",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name, e.g. 'Monument, CO' or 'NYC'"},
                    "units": {"type": "string", "enum": ["metric", "imperial", "standard"]},
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_run",
            "description": "Run a shell command (git, pytest, npm, pip, make, etc.). Always confirmed in the UI — call it when a command is needed; do not ask in chat text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command line to execute."},
                    "cwd": {"type": "string", "description": "Working directory; defaults to the primary granted workspace root."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Surgical find-and-replace. Call with apply=false first (unified diff), then apply=true to write. Prefer over write_file for small edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to file."},
                    "old": {"type": "string", "description": "Exact substring to find."},
                    "new": {"type": "string", "description": "Replacement text."},
                    "apply": {"type": "boolean", "description": "Set true on second call to commit."},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "DuckDuckGo Instant Answer API. Best for known topics/entities (abstract + related links). Sparse for arbitrary web queries — if empty, try web_fetch on a known URL or answer from knowledge; do not retry the same query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query."},
                    "limit": {"type": "integer", "description": "How many results to return (1-10, default 5)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and return extracted text. Use after web_search, or when the user pastes a link you need to read.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch."},
                    "max_chars": {"type": "integer", "description": "Truncate response to this many characters (default 8000)."},
                },
                "required": ["url"],
            },
        },
    },
]


class FsError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def normalize_roots(roots: List[str]) -> List[Path]:
    out: List[Path] = []
    for r in roots or []:
        if not r or not str(r).strip():
            continue
        p = Path(r).expanduser()
        try:
            resolved = p.resolve()
        except OSError as e:
            raise FsError(f"invalid_root: {r}: {e}") from e
        if not resolved.exists() or not resolved.is_dir():
            raise FsError(f"invalid_root: {r} is not an existing directory")
        out.append(resolved)
    if not out:
        raise FsError("no_workspace_roots: grant at least one workspace directory")
    return out


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _denied_by_name(path: Path) -> Optional[str]:
    home = Path(os.path.expanduser("~")).resolve()
    for part in path.parts:
        if part in DENY_DIR_NAMES:
            return f"denylist: path includes blocked segment '{part}'"
    # Explicit home secret dirs even if somehow named differently in roots
    for name in DENY_DIR_NAMES:
        blocked = (home / name).resolve()
        if path == blocked or _is_under(path, blocked):
            return f"denylist: access to {blocked} is blocked"
    return None


def _denied_write_suffix(path: Path) -> Optional[str]:
    name = path.name.lower()
    for suf in DENY_WRITE_SUFFIXES:
        if name.endswith(suf):
            return f"denylist: writes to *{suf} files are blocked"
    return None


def resolve_in_workspace(
    path: str,
    roots: List[Path],
    *,
    for_write: bool = False,
    must_exist: bool = False,
) -> Path:
    """Resolve path; must land under a granted root after symlink resolution.

    Relative paths (including ``.``) are resolved against the **primary**
    granted workspace root (``roots[0]``), never the process cwd. Absolute
    and ``~``-prefixed paths resolve normally, then must fall under a root.

    For writes to new files, resolve the parent and join the final name so
    missing targets still get a safe absolute path.
    """
    if not roots:
        raise FsError("no_workspace: add a workspace root first")

    text = "" if path is None else str(path).strip()
    if not text:
        raise FsError("invalid_path: empty path")

    raw = Path(text).expanduser()
    # Relative tool paths use the primary workspace as cwd — not the server's
    # process directory (which is often the Voxa repo).
    if not raw.is_absolute():
        raw = (roots[0] / raw).resolve(strict=False)

    if for_write and not raw.exists():
        parent = raw.parent
        try:
            parent_resolved = parent.resolve(strict=False)
            # Resolve existing parents fully when possible
            if parent.exists():
                parent_resolved = parent.resolve()
        except OSError as e:
            raise FsError(f"invalid_path: {path}: {e}") from e
        candidate = (parent_resolved / raw.name).resolve(strict=False)
        # If parent exists, rebuild with resolved parent
        if parent.exists():
            candidate = parent.resolve() / raw.name
    else:
        try:
            if must_exist or raw.exists():
                candidate = raw.resolve()
            else:
                # read of missing file
                candidate = raw.resolve(strict=False)
                if raw.exists():
                    candidate = raw.resolve()
        except OSError as e:
            raise FsError(f"invalid_path: {path}: {e}") from e

    deny = _denied_by_name(candidate)
    if deny:
        raise FsError(deny)

    if for_write:
        suf = _denied_write_suffix(candidate)
        if suf:
            raise FsError(suf)

    if not any(_is_under(candidate, root) or candidate == root for root in roots):
        raise FsError(f"outside_workspace: {candidate} is not under granted roots")

    # Symlink escape: if the path exists, ensure resolved path still under roots
    if candidate.exists() or candidate.is_symlink():
        try:
            fully = candidate.resolve()
        except OSError as e:
            raise FsError(f"invalid_path: {path}: {e}") from e
        deny2 = _denied_by_name(fully)
        if deny2:
            raise FsError(deny2)
        if not any(_is_under(fully, root) or fully == root for root in roots):
            raise FsError(f"outside_workspace: {fully} escapes granted roots via symlink")
        if for_write:
            suf = _denied_write_suffix(fully)
            if suf:
                raise FsError(suf)
        return fully

    return candidate


def _list_dir(path: Path, depth: int) -> str:
    depth = max(1, min(int(depth), MAX_LIST_DEPTH))
    lines: List[str] = []

    def walk(current: Path, remaining: int, prefix: str = "") -> None:
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            raise FsError(f"list_dir failed: {e}") from e
        for ent in entries:
            rel = f"{prefix}{ent.name}"
            kind = "dir" if ent.is_dir() and not ent.is_symlink() else (
                "symlink" if ent.is_symlink() else "file"
            )
            lines.append(f"{rel}\t{kind}")
            if kind == "dir" and remaining > 1:
                walk(ent, remaining - 1, prefix=f"{rel}/")

    walk(path, depth)
    return "\n".join(lines) if lines else "(empty)"


def _read_file(path: Path) -> str:
    if not path.is_file():
        raise FsError(f"not_a_file: {path}")
    size = path.stat().st_size
    if size > MAX_READ_BYTES:
        raise FsError(f"too_large: {size} bytes (max {MAX_READ_BYTES})")
    data = path.read_bytes()
    sample = data[:8192]
    if b"\0" in sample:
        raise FsError("binary_file: refusing to read binary content")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _search_files(root: Path, pattern: str) -> str:
    if not root.is_dir():
        raise FsError(f"not_a_directory: {root}")
    hits: List[str] = []
    # Support both "*.py" and "**/*.py"
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if fnmatch.fnmatch(p.name, pattern) or fnmatch.fnmatch(rel, pattern):
            hits.append(str(p))
            if len(hits) >= MAX_SEARCH_HITS:
                hits.append(f"... truncated at {MAX_SEARCH_HITS}")
                break
    return "\n".join(hits) if hits else "(no matches)"


def _write_file(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"wrote {path} ({len(content)} chars)"


def _mkdir(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    return f"mkdir {path}"


def _delete_file(path: Path) -> str:
    if path.is_dir() and not path.is_symlink():
        raise FsError("delete_file: refusing to delete a directory")
    if not path.exists():
        raise FsError(f"not_found: {path}")
    path.unlink()
    return f"deleted {path}"


def _move_path(src: Path, dst: Path) -> str:
    if not src.exists():
        raise FsError(f"not_found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return f"moved {src} -> {dst}"


def _get_weather(args: Dict[str, Any]) -> str:
    """OpenWeatherMap current weather for a city.

    Resolves the place via the Geocoding API first (handles 'Monument, CO'),
    then fetches current conditions by lat/lon. Includes observed_at so the
    model cannot invent a date.
    """
    from datetime import datetime, timezone
    import urllib.parse as _urlparse
    import urllib.request as _urlreq

    api_key = _resolve_openweather_api_key()
    location = args.get("location") or args.get("city")
    if not location:
        return "error: missing 'location' argument"
    if not api_key:
        return (
            "error: OPENWEATHERMAP_API_KEY not set. "
            "Add OPENWEATHERMAP_API_KEY=... to the repo-root .env file "
            "(gitignored) and restart the toolbox. "
            "shell_run export cannot set this — it only affects that subprocess."
        )
    units = args.get("units", "imperial")

    def _get_json(url: str) -> Any:
        req = _urlreq.Request(url, headers={"User-Agent": "Voxa/1.0"})
        with _urlreq.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    # Expand common US state abbreviations so geocoding is less ambiguous.
    query = str(location).strip()
    _US_ABBR = {
        "al": "Alabama", "ak": "Alaska", "az": "Arizona", "ar": "Arkansas",
        "ca": "California", "co": "Colorado", "ct": "Connecticut", "de": "Delaware",
        "fl": "Florida", "ga": "Georgia", "hi": "Hawaii", "id": "Idaho",
        "il": "Illinois", "in": "Indiana", "ia": "Iowa", "ks": "Kansas",
        "ky": "Kentucky", "la": "Louisiana", "me": "Maine", "md": "Maryland",
        "ma": "Massachusetts", "mi": "Michigan", "mn": "Minnesota", "ms": "Mississippi",
        "mo": "Missouri", "mt": "Montana", "ne": "Nebraska", "nv": "Nevada",
        "nh": "New Hampshire", "nj": "New Jersey", "nm": "New Mexico", "ny": "New York",
        "nc": "North Carolina", "nd": "North Dakota", "oh": "Ohio", "ok": "Oklahoma",
        "or": "Oregon", "pa": "Pennsylvania", "ri": "Rhode Island", "sc": "South Carolina",
        "sd": "South Dakota", "tn": "Tennessee", "tx": "Texas", "ut": "Utah",
        "vt": "Vermont", "va": "Virginia", "wa": "Washington", "wv": "West Virginia",
        "wi": "Wisconsin", "wy": "Wyoming", "dc": "District of Columbia",
    }
    _US_NAMES = {v.lower() for v in _US_ABBR.values()}
    parts = [p.strip() for p in query.split(",") if p.strip()]
    if len(parts) >= 2:
        state_tok = parts[1].lower()
        if state_tok in _US_ABBR:
            parts[1] = _US_ABBR[state_tok]
            state_tok = parts[1].lower()
        if state_tok in _US_NAMES and parts[-1].upper() != "US":
            parts.append("US")
        query = ", ".join(parts)

    try:
        geo_qs = _urlparse.urlencode({"q": query, "limit": 5, "appid": api_key})
        places = _get_json(f"https://api.openweathermap.org/geo/1.0/direct?{geo_qs}")
    except Exception as e:
        return f"OpenWeatherMap geocode error: {e}"

    if not places:
        return (
            f"error: no place found for {location!r}. "
            "Try 'City, State' or 'City, Country' (e.g. 'Monument, Colorado, US')."
        )

    # Prefer a match whose state appears in the user query; otherwise keep
    # the geocoder's top hit (usually the best-known place).
    loc_l = str(location).lower()
    place = places[0]
    for cand in places:
        state = (cand.get("state") or "").lower()
        if state and state in loc_l:
            place = cand
            break
        abbr = next((a for a, n in _US_ABBR.items() if n.lower() == state), "")
        if abbr and (
            f", {abbr}" in loc_l
            or loc_l.endswith(f" {abbr}")
            or f",{abbr}" in loc_l.replace(" ", "")
        ):
            place = cand
            break

    lat, lon = place.get("lat"), place.get("lon")
    if lat is None or lon is None:
        return f"error: geocode result missing coordinates for {location!r}"

    try:
        wx_qs = _urlparse.urlencode(
            {"lat": lat, "lon": lon, "appid": api_key, "units": units}
        )
        data = _get_json(f"https://api.openweathermap.org/data/2.5/weather?{wx_qs}")
    except Exception as e:
        return f"OpenWeatherMap error: {e}"

    if data.get("cod") != 200:
        return f"OpenWeatherMap: {data.get('message', 'unknown error')}"

    w = data["weather"][0]
    main = data["main"]
    sysinfo = data.get("sys", {})
    observed = datetime.fromtimestamp(int(data.get("dt") or 0), tz=timezone.utc)
    place_label = place.get("name") or data.get("name") or location
    if place.get("state"):
        place_label = f"{place_label}, {place['state']}"
    if place.get("country"):
        place_label = f"{place_label}, {place['country']}"

    return json.dumps({
        "location": place_label,
        "lat": lat,
        "lon": lon,
        "country": sysinfo.get("country") or place.get("country") or "",
        "conditions": w["main"],
        "description": w["description"],
        "temp": main["temp"],
        "feels_like": main["feels_like"],
        "humidity": main["humidity"],
        "wind_speed": data.get("wind", {}).get("speed", 0),
        "units": units,
        "observed_at": observed.isoformat().replace("+00:00", "Z"),
        "source": "OpenWeatherMap",
    })


def preview_write(name: str, args: Dict[str, Any], roots: List[Path]) -> str:
    """Return a preview string without mutating disk (best-effort).

    Raises FsError for outside_workspace / denylist so the agent can prompt
    for a grant. Other preview failures are returned as text.
    """
    try:
        if name == "write_file":
            path = resolve_in_workspace(args.get("path", ""), roots, for_write=True)
            new = args.get("content", "")
            if path.exists() and path.is_file():
                old = _read_file(path)
                diff = difflib.unified_diff(
                    old.splitlines(),
                    new.splitlines(),
                    fromfile=str(path),
                    tofile=str(path) + " (new)",
                    lineterm="",
                )
                text = "\n".join(list(diff)[:200])
                return text or "(no changes)"
            preview = new if len(new) <= 4000 else new[:4000] + "\n…(truncated)"
            return f"NEW FILE {path}\n{preview}"
        if name == "mkdir":
            path = resolve_in_workspace(args.get("path", ""), roots, for_write=True)
            return f"mkdir {path}"
        if name == "delete_file":
            path = resolve_in_workspace(args.get("path", ""), roots, for_write=True)
            return f"delete {path}"
        if name == "move_path":
            src = resolve_in_workspace(args.get("src", ""), roots, for_write=False)
            dst = resolve_in_workspace(args.get("dst", ""), roots, for_write=True)
            return f"move {src} -> {dst}"
        if name == "edit_file":
            # Preview without applying
            preview_args = dict(args or {})
            preview_args["apply"] = False
            return _tool_edit_file(preview_args, roots)
    except FsError:
        raise
    except Exception as e:
        return f"(preview error: {e})"
    return f"{name} {args}"


def resolve_tool_cwd(cwd: str | None, roots: List[Path]) -> str:
    """Resolve shell cwd to an absolute path under a granted workspace root."""
    if not roots:
        raise FsError("no_workspace: add a workspace root first")
    text = "" if cwd is None else str(cwd).strip()
    if not text:
        return str(roots[0].resolve())
    return str(resolve_in_workspace(text, roots))


def _shell_preview(command: str, cwd: str | None) -> str:
    """Best-effort preview for shell_run approval prompt."""
    eff_cwd = cwd or "(primary workspace)"
    return f"shell_run will execute:\n  cwd: {eff_cwd}\n  command: {command}\n"


def _tool_run_shell(args: Dict[str, Any]) -> str:
    """Return a preview string. chat_agent handles the approval gate
    and calls _shell_execute after the user confirms."""
    command = (args.get("command") or "").strip()
    cwd = args.get("cwd")
    if not command:
        return "error: missing 'command'"
    return _shell_preview(command, cwd)


def _kill_shell_proc(proc) -> None:
    import os
    import signal

    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=1)
    except Exception:
        try:
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _shell_execute(
    command: str,
    cwd: str | None,
    timeout: int = 120,
    cancel_event=None,
) -> str:
    """Actually run the command and capture stdout/stderr/exit_code."""
    import os
    import subprocess as _sp
    import time

    eff_cwd = cwd or "."
    popen_kw: dict = {
        "shell": True,
        "cwd": eff_cwd,
        "stdout": _sp.PIPE,
        "stderr": _sp.PIPE,
        "text": True,
    }
    if os.name != "nt":
        popen_kw["start_new_session"] = True
    try:
        proc = _sp.Popen(command, **popen_kw)
    except Exception as e:
        return json.dumps({"error": str(e)})

    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _kill_shell_proc(proc)
                return json.dumps({"error": "shell_run cancelled", "cancelled": True})
            ret = proc.poll()
            if ret is not None:
                stdout, stderr = proc.communicate(timeout=1)
                return json.dumps({
                    "stdout": (stdout or "")[-4000:],
                    "stderr": (stderr or "")[-2000:],
                    "exit_code": ret,
                })
            if time.monotonic() >= deadline:
                _kill_shell_proc(proc)
                return json.dumps({"error": f"shell_run timeout after {timeout}s"})
            time.sleep(0.05)
    except Exception as e:
        _kill_shell_proc(proc)
        return json.dumps({"error": str(e)})


def _tool_edit_file(args: Dict[str, Any], roots: List[Path]) -> str:
    """Find-and-replace edit. First call (apply=False or omitted) returns a
    unified diff. Second call with apply=True writes the change."""
    path = args.get("path")
    old = args.get("old")
    new = args.get("new")
    apply_now = bool(args.get("apply", False))
    if not (path and old is not None and new is not None):
        return "error: required args: path, old, new"
    try:
        resolved = resolve_in_workspace(path, roots, for_write=True, must_exist=True)
    except FsError as e:
        return f"error: {e.message}"
    p = Path(resolved)
    path_str = str(p)
    if not p.is_file():
        return f"error: {path_str} is not a regular file"
    if p.suffix.lower() in DENY_WRITE_SUFFIXES or _denied_by_name(p):
        return "error: write blocked by denylist"
    try:
        text = p.read_text()
    except Exception as e:
        return f"error: read failed: {e}"
    if old not in text:
        return f"error: old_text not found verbatim in {path_str}"
    if not apply_now:
        preview = text.replace(old, new, 1)
        diff = "".join(difflib.unified_diff(
            text.splitlines(keepends=True),
            preview.splitlines(keepends=True),
            fromfile=f"{p.name} (current)",
            tofile=f"{p.name} (proposed)",
        ))
        return json.dumps({
            "status": "preview",
            "path": path_str,
            "diff": diff,
            "hint": "call again with apply=true to commit",
        })
    new_text = text.replace(old, new, 1)
    if new_text == text:
        return "error: no change (old == new after first match)"
    p.write_text(new_text)
    return json.dumps({
        "status": "applied",
        "path": path_str,
        "lines_before": text.count("\n"),
        "lines_after": new_text.count("\n"),
    })


def _tool_web_search(args: Dict[str, Any]) -> str:
    """DuckDuckGo Instant Answer API (official JSON). No HTML scraping."""
    import html as _html
    import re as _re
    import urllib.parse as _u
    import urllib.request as _r

    query = args.get("query") or args.get("q")
    if not query:
        return "error: missing 'query' argument"
    limit = max(1, min(int(args.get("limit", 5)), 10))

    api_url = (
        "https://api.duckduckgo.com/?"
        + _u.urlencode(
            {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "0",
                "t": "voxa",
            }
        )
    )
    req = _r.Request(
        api_url,
        headers={
            "User-Agent": "Voxa/1.0 (local; +https://github.com/local/voxa)",
            "Accept": "application/json",
        },
    )
    try:
        with _r.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
    except Exception as e:
        return json.dumps({
            "query": query,
            "results": [],
            "provider": "duckduckgo_instant_answer",
            "error": f"api fetch failed: {e}",
        })

    def _clean(s: str) -> str:
        return _html.unescape(_re.sub(r"<[^>]+>", "", s or "")).strip()

    def _walk_topics(items: List[Any], out: List[Dict[str, str]]) -> None:
        for item in items or []:
            if len(out) >= limit:
                return
            if not isinstance(item, dict):
                continue
            # Nested topic groups: {"Name": "...", "Topics": [...]}
            if "Topics" in item:
                _walk_topics(item.get("Topics") or [], out)
                continue
            url = (item.get("FirstURL") or "").strip()
            text = _clean(item.get("Text") or "")
            if not url:
                continue
            title = text.split(" - ", 1)[0].strip() if text else url
            snippet = text[len(title):].lstrip(" -") if text.startswith(title) else text
            out.append({"title": title or url, "url": url, "snippet": snippet})

    results: List[Dict[str, str]] = []

    abstract = _clean(data.get("AbstractText") or "")
    abs_url = (data.get("AbstractURL") or "").strip()
    heading = _clean(data.get("Heading") or query)
    if abstract and abs_url:
        results.append({"title": heading, "url": abs_url, "snippet": abstract[:500]})

    # Official "Results" array (often Answer-box style links)
    for item in data.get("Results") or []:
        if len(results) >= limit:
            break
        if not isinstance(item, dict):
            continue
        url = (item.get("FirstURL") or "").strip()
        text = _clean(item.get("Text") or "")
        if url:
            results.append({
                "title": text or url,
                "url": url,
                "snippet": "",
            })

    _walk_topics(data.get("RelatedTopics") or [], results)
    results = results[:limit]

    answer = _clean(data.get("Answer") or "")
    answer_type = (data.get("AnswerType") or "").strip()
    definition = _clean(data.get("Definition") or "")
    definition_url = (data.get("DefinitionURL") or "").strip()

    out: Dict[str, Any] = {
        "query": query,
        "results": results,
        "provider": "duckduckgo_instant_answer",
    }
    if answer:
        out["answer"] = answer
        if answer_type:
            out["answer_type"] = answer_type
    if definition:
        out["definition"] = definition
        if definition_url:
            out["definition_url"] = definition_url
    if not results and not answer and not definition:
        out["error"] = (
            "Instant Answer had no abstract/related topics for this query. "
            "Try a clearer entity name, web_fetch a known URL, or answer without search."
        )
    return json.dumps(out)


def _tool_web_fetch(args: Dict[str, Any]) -> str:
    """GET a URL and return extracted text content."""
    import re as _re
    import urllib.request as _r

    url = args.get("url")
    if not url:
        return "error: missing 'url' argument"
    if not (url.startswith("http://") or url.startswith("https://")):
        return "error: url must start with http:// or https://"
    max_chars = max(500, min(int(args.get("max_chars", 8000)), 50000))
    req = _r.Request(url, headers={"User-Agent": "Voxa/1.0 (compatible; local-dev)"})
    try:
        with _r.urlopen(req, timeout=20) as r:
            ct = r.headers.get("Content-Type", "")
            body = r.read(max_chars * 4)  # cap download before decode
    except Exception as e:
        return f"error: fetch failed: {e}"
    text = ""
    if "text" in ct or "html" in ct or "json" in ct or not ct:
        text = body.decode("utf-8", errors="replace")
    if "<html" in text[:2000].lower() or "text/html" in ct:
        try:
            from bs4 import BeautifulSoup as _bs
            soup = _bs(text, "html.parser")
            for tag in soup(["script", "style", "noscript", "svg"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
        except ImportError:
            text = _re.sub(r"<script[\s\S]*?</script>", "", text, flags=_re.I)
            text = _re.sub(r"<style[\s\S]*?</style>", "", text, flags=_re.I)
            text = _re.sub(r"<[^>]+>", " ", text)
            text = _re.sub(r"\s+", " ", text).strip()
    text = text[:max_chars]
    return json.dumps({"url": url, "content_type": ct, "content": text})


def run_tool(name: str, args: Dict[str, Any], roots: List[Path]) -> str:
    args = args or {}
    if name == "list_dir":
        path = resolve_in_workspace(args.get("path") or ".", roots)
        depth = args.get("depth", 1) or 1
        return _list_dir(path, int(depth))
    if name == "read_file":
        path = resolve_in_workspace(args.get("path", ""), roots, must_exist=True)
        return _read_file(path)
    if name == "search_files":
        root = resolve_in_workspace(args.get("root") or ".", roots)
        return _search_files(root, args.get("pattern", "*") or "*")
    if name == "write_file":
        path = resolve_in_workspace(args.get("path", ""), roots, for_write=True)
        return _write_file(path, args.get("content", "") or "")
    if name == "mkdir":
        path = resolve_in_workspace(args.get("path", ""), roots, for_write=True)
        return _mkdir(path)
    if name == "delete_file":
        path = resolve_in_workspace(args.get("path", ""), roots, for_write=True)
        return _delete_file(path)
    if name == "move_path":
        src = resolve_in_workspace(args.get("src", ""), roots)
        dst = resolve_in_workspace(args.get("dst", ""), roots, for_write=True)
        return _move_path(src, dst)
    if name == "edit_file":
        return _tool_edit_file(args, roots)
    if name == "get_weather":
        return _get_weather(args)
    if name == "web_search":
        return _tool_web_search(args)
    if name == "web_fetch":
        return _tool_web_fetch(args)
    if name == "shell_run":
        # Execution happens only after chat_agent approval via _shell_execute.
        # If somehow invoked directly, run it (caller must have gated already).
        command = (args.get("command") or "").strip()
        if not command:
            return "error: missing 'command'"
        return _shell_execute(command, args.get("cwd"))
    raise FsError(f"unknown_tool: {name}")


def path_from_tool_args(name: str, args: Dict[str, Any]) -> Optional[str]:
    """Best-effort path to mention in workspace_grant_needed events."""
    args = args or {}
    if name == "move_path":
        return args.get("dst") or args.get("src")
    if name == "search_files":
        return args.get("root")
    return args.get("path")


def suggested_root_for_path(path: str) -> str:
    """Pick a directory to grant for an out-of-workspace path."""
    if not path:
        return ""
    p = Path(path).expanduser()
    try:
        if p.exists() and p.is_dir():
            return str(p.resolve())
        parent = p.parent
        if parent.exists():
            return str(parent.resolve())
        return str(parent)
    except OSError:
        return str(p.parent)
