"""Collapse failed explore-tool streaks from chat message history."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

EXPLORE_TOOLS = frozenset({"list_dir", "read_file", "search_files"})
PATH_REPAIR_PREFIX = "(system) Path repair:"

_PATH_TOKEN_RE = re.compile(
    r'(?P<q>["\'])(?P<p>[^"\']{2,})(?P=q)'
    r"|(?P<abs>(?:/[^\s,;]+|[A-Za-z]:\\[^\s,;]+))"
)
_CORRECTION_HINTS = (
    "wrong",
    "not where",
    "not the",
    "actually",
    "correct path",
    "it's in",
    "its in",
    "try ",
    "use ",
    "should be",
    "real path",
)


def is_explore_error(content: str) -> bool:
    return str(content or "").lstrip().startswith("error:")


def _tool_call_args(tc: Dict[str, Any]) -> Dict[str, Any]:
    fn = tc.get("function") or {}
    args = fn.get("arguments") or tc.get("args") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            args = {}
    return args if isinstance(args, dict) else {}


def _single_explore_call(
    message: Dict[str, Any],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    if (message.get("role") or "") != "assistant":
        return None
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        return None
    tc = calls[0]
    if not isinstance(tc, dict):
        return None
    fn = tc.get("function") or {}
    name = str(fn.get("name") or tc.get("name") or "")
    if name not in EXPLORE_TOOLS:
        return None
    return name, _tool_call_args(tc)


def explore_path_label(name: str, args: Dict[str, Any]) -> str:
    args = args or {}
    if name == "search_files":
        root = str(args.get("root") or "").strip() or "(unknown)"
        pattern = str(args.get("pattern") or "").strip()
        return f"{root}" + (f" pattern={pattern}" if pattern else "")
    path = str(args.get("path") or "").strip()
    return path or "(unknown)"


def _extract_path_tokens(text: str) -> List[str]:
    out: List[str] = []
    for m in _PATH_TOKEN_RE.finditer(text or ""):
        tok = (m.group("p") or m.group("abs") or "").strip()
        if tok and tok not in out:
            out.append(tok)
    return out


def looks_like_path_correction(text: str) -> bool:
    t = (text or "").strip()
    if not t or t.startswith("(system)"):
        return False
    low = t.lower()
    if not any(h in low for h in _CORRECTION_HINTS):
        return False
    return bool(_extract_path_tokens(t)) or ("path" in low)


def _is_path_repair_note(message: Dict[str, Any]) -> bool:
    if (message.get("role") or "") != "user":
        return False
    return str(message.get("content") or "").startswith(PATH_REPAIR_PREFIX)


def _is_system_user_note(message: Dict[str, Any]) -> bool:
    """Transparent user messages (nudges, etc.) that must not break collapse walks."""
    if (message.get("role") or "") != "user":
        return False
    return str(message.get("content") or "").startswith("(system)")


def _repair_note(
    *,
    n_failed: int,
    working: List[str],
    failed: List[str],
) -> Dict[str, Any]:
    working_s = ", ".join(working) if working else "(none)"
    failed_s = ", ".join(failed) if failed else "(none)"
    content = (
        f"{PATH_REPAIR_PREFIX} collapsed {n_failed} failed explore steps.\n"
        f"Working: {working_s}\n"
        f"Do not retry: {failed_s}\n"
        "Continue from the working paths. If the user asked for a report/audit on "
        "already-read files, reply with findings — do not re-list directories."
    )
    return {"role": "user", "content": content}


def _pair_at(
    messages: List[Dict[str, Any]], idx: int
) -> Optional[Tuple[int, int, str, Dict[str, Any], str]]:
    """If messages[idx] is assistant explore and idx+1 matching tool, return pair info."""
    if idx < 0 or idx + 1 >= len(messages):
        return None
    parsed = _single_explore_call(messages[idx])
    if not parsed:
        return None
    name, args = parsed
    tool = messages[idx + 1]
    if (tool.get("role") or "") != "tool":
        return None
    if (tool.get("name") or "") != name:
        return None
    return idx, idx + 1, name, args, str(tool.get("content") or "")


def collapse_explore_failures(messages: List[Dict[str, Any]]) -> bool:
    """Mutate messages in place; return True if a failed explore streak was collapsed."""
    if not messages:
        return False

    keep_end: List[Dict[str, Any]] = []
    scan_end = len(messages)
    working: List[str] = []

    last = messages[-1]
    # Case A: successful explore tool at end.
    if (last.get("role") or "") == "tool":
        name = last.get("name") or ""
        content = str(last.get("content") or "")
        if name in EXPLORE_TOOLS and not is_explore_error(content):
            pair = _pair_at(messages, len(messages) - 2)
            if not pair:
                return False
            a_i, t_i, pname, args, _ = pair
            if t_i != len(messages) - 1 or pname != name:
                return False
            working = [explore_path_label(pname, args)]
            keep_end = [messages[a_i], messages[t_i]]
            scan_end = a_i
    # Case B: user path correction at end.
    elif (last.get("role") or "") == "user" and looks_like_path_correction(
        str(last.get("content") or "")
    ):
        working = _extract_path_tokens(str(last.get("content") or ""))
        keep_end = [last]
        scan_end = len(messages) - 1
    else:
        return False

    # Walk backward collecting contiguous failed explore pairs (and skip prior repair notes).
    failed_paths: List[str] = []
    remove_indices: List[int] = []
    i = scan_end - 1
    while i >= 0:
        m = messages[i]
        if _is_path_repair_note(m) or _is_system_user_note(m):
            i -= 1
            continue
        if (m.get("role") or "") == "user":
            # Allow short path-correction users inside the streak; keep them outside remove set.
            if looks_like_path_correction(str(m.get("content") or "")):
                i -= 1
                continue
            break
        if (m.get("role") or "") == "tool":
            # Expect this tool is the second half of a pair starting at i-1.
            pair = _pair_at(messages, i - 1)
            if not pair:
                # Ambiguous / non-explore → stop streak (do not remove across it).
                break
            a_i, t_i, name, args, content = pair
            if t_i != i:
                break
            if not is_explore_error(content):
                break
            label = explore_path_label(name, args)
            if label not in failed_paths:
                failed_paths.append(label)
            remove_indices.extend([a_i, t_i])
            i = a_i - 1
            continue
        if (m.get("role") or "") == "assistant":
            # Unpaired / multi-call assistant → fail safe, abort entire collapse.
            if _single_explore_call(m) is None and m.get("tool_calls"):
                return False
            break
        break

    if not remove_indices:
        return False

    # Idempotency: if an adjacent prior repair already lists same working and no new fails, skip.
    # (We still collapse when remove_indices is non-empty — that IS a new streak.)

    remove_set = set(remove_indices)
    new_msgs: List[Dict[str, Any]] = []
    for idx, m in enumerate(messages):
        if idx >= scan_end:
            break
        if idx in remove_set:
            continue
        new_msgs.append(m)

    note = _repair_note(
        n_failed=len(remove_indices) // 2,
        working=working,
        failed=list(reversed(failed_paths)),
    )
    # Insert note just before keep_end content.
    new_msgs.append(note)
    new_msgs.extend(keep_end)

    messages[:] = new_msgs
    return True
