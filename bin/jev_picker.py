"""Build System One picker requests and call hosted TypeSafe / Jev API."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

import fs_tools as ft

REPLY_KEY = "reply"
ASK_CLARIFY_KEY = "ask_clarify"
PROPOSE_PLAN_KEY = "propose_plan"
UPDATE_TODO_KEY = "update_todo"
CONTROL_KEYS = frozenset({ASK_CLARIFY_KEY, PROPOSE_PLAN_KEY, UPDATE_TODO_KEY})
QUESTION_ID = "next_action"
STATE_CHAR_BUDGET = 16000
DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_TEV1_MODEL = "tev1:4b"
TOOL_KEYS = [t["function"]["name"] for t in ft.TOOL_SCHEMAS]
SENSITIVE_PRE_OLLAMA = frozenset(
    {"delete_file", "write_file", "edit_file", "move_path", "shell_run"}
)
READ_FILE_KEY = "read_file"
LIST_DIR_KEY = "list_dir"
SEARCH_FILES_KEY = "search_files"
EXPLORE_KEYS = frozenset({READ_FILE_KEY, LIST_DIR_KEY, SEARCH_FILES_KEY})
LATEST_USER_MAX = 400
_REPORT_HINTS = ("report", "summary", "audit", "findings")


JEV_KIND_CANCELLED = "cancelled"
JEV_KIND_UNREACHABLE = "unreachable"
JEV_KIND_HTTP = "http"
JEV_KIND_MALFORMED = "malformed"
JEV_KIND_UNKNOWN_OPTION = "unknown_option"
JEV_KIND_TEV1_FAILED = "tev1_failed"


class JevPickerError(Exception):
    def __init__(self, message: str, *, kind: str = JEV_KIND_UNREACHABLE):
        self.message = message
        self.kind = kind
        super().__init__(message)


def typesafe_base_url() -> str:
    return (
        (os.environ.get("TYPESAFE_BASE_URL") or DEFAULT_BASE_URL)
        .strip()
        .rstrip("/")
    )


def typesafe_api_key() -> str:
    return (os.environ.get("TYPESAFE_API_KEY") or "").strip()


def jev_model() -> str:
    return (
        (os.environ.get("VOXA_JEV_MODEL") or os.environ.get("TYPESAFE_DEFAULT_MODEL") or DEFAULT_MODEL)
        .strip()
        or DEFAULT_MODEL
    )


def ollama_base_url() -> str:
    return (
        (os.environ.get("OLLAMA_URL") or DEFAULT_OLLAMA_URL)
        .strip()
        .rstrip("/")
    )


def tev1_model() -> str:
    return (
        (os.environ.get("VOXA_TEV1_MODEL") or DEFAULT_TEV1_MODEL).strip()
        or DEFAULT_TEV1_MODEL
    )


def choice_criteria() -> Dict[str, str]:
    crit: Dict[str, str] = {
        REPLY_KEY: (
            "Final answer or status for the user when the task is done or blocked; "
            "do not use to announce the next tool step"
        ),
        ASK_CLARIFY_KEY: (
            "ONLY when blocked without a user answer — ask a real question that ends "
            "with '?'. Never for progress updates, plans, or 'I'll start…' narration"
        ),
        PROPOSE_PLAN_KEY: "Propose an implementation plan and todo list for user approval",
        UPDATE_TODO_KEY: "Update todo checklist progress during an approved implementation",
    }
    for t in ft.TOOL_SCHEMAS:
        name = t["function"]["name"]
        desc = (t["function"].get("description") or name).split(".")[0].strip()
        crit[name] = desc or name
    return crit


def _tool_call_args(tc: Dict[str, Any]) -> Dict[str, Any]:
    fn = tc.get("function") or {}
    args = fn.get("arguments") or tc.get("args") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except json.JSONDecodeError:
            args = {}
    return args if isinstance(args, dict) else {}


def _read_file_path_from_tool_calls(tool_calls: Any) -> Optional[str]:
    if not isinstance(tool_calls, list):
        return None
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or ""
        if name != READ_FILE_KEY:
            continue
        path = _tool_call_args(tc).get("path")
        if path is not None and str(path).strip():
            return str(path).strip()
    return None


def _is_read_file_error(content: str) -> bool:
    return content.lstrip().startswith("error:")


def _explore_from_tool_calls(tool_calls: Any) -> Optional[Tuple[str, str]]:
    """Return (tool_name, label) for the first explore tool_call, else None."""
    if not isinstance(tool_calls, list):
        return None
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name") or ""
        if name not in EXPLORE_KEYS:
            continue
        args = _tool_call_args(tc)
        if name == SEARCH_FILES_KEY:
            root = str(args.get("root") or "").strip() or "(unknown)"
            pattern = str(args.get("pattern") or "").strip()
            label = f"root={root}" + (f" pattern={pattern}" if pattern else "")
        else:
            path = str(args.get("path") or "").strip() or "(unknown)"
            label = f"path={path}"
        return str(name), label
    return None


def _is_explore_error(content: str) -> bool:
    return content.lstrip().startswith("error:")


def _compact_explore_line(name: str, label: str, content: str) -> str:
    if _is_explore_error(content):
        return f"{name} error {label} {content.strip()}"
    return f"{name} ok {label} ({len(content)} chars)"


def _latest_user_text(
    messages: List[Dict[str, Any]], *, max_chars: int = LATEST_USER_MAX
) -> str:
    for m in reversed(messages):
        if (m.get("role") or "") != "user":
            continue
        text = str(m.get("content") or "").strip()
        if not text or text.startswith("(system)"):
            continue
        if len(text) > max_chars:
            return text[:max_chars]
        return text
    return "(none)"


def prefer_reply_from_files(messages: List[Dict[str, Any]]) -> bool:
    if not _collect_files_read(messages):
        return False
    latest = _latest_user_text(messages).lower()
    if latest == "(none)":
        return False
    return any(h in latest for h in _REPORT_HINTS)


def _collect_files_read(messages: List[Dict[str, Any]]) -> List[str]:
    """Successful read_file paths from full history (deduped, first-seen order)."""
    seen: List[str] = []
    pending_path: Optional[str] = None
    for m in messages:
        role = m.get("role") or ""
        if role == "assistant" and m.get("tool_calls"):
            pending_path = _read_file_path_from_tool_calls(m.get("tool_calls"))
            continue
        if role != "tool" or (m.get("name") or "") != READ_FILE_KEY:
            continue
        content = m.get("content") or ""
        if _is_read_file_error(str(content)):
            pending_path = None
            continue
        path = pending_path
        if path and path not in seen:
            seen.append(path)
        pending_path = None
    return seen


def build_state(
    messages: List[Dict[str, Any]],
    *,
    roots: List[str],
    writes_armed: bool,
    now_iso: Optional[str] = None,
    mode: str = "normal",
    plan_approved: bool = False,
    todos: Optional[List[Dict[str, Any]]] = None,
) -> str:
    now = now_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    todo_bits = []
    for t in todos or []:
        todo_bits.append(
            f"{t.get('id', '?')}:{t.get('status', 'pending')}:{t.get('label', '')}"
        )
    todo_line = "; ".join(todo_bits) if todo_bits else "(none)"
    files_read = _collect_files_read(messages)
    files_line = " | ".join(files_read) if files_read else "(none)"
    latest = _latest_user_text(messages)
    header = (
        f"Current date/time: {now}\n"
        f"workspace_roots: {roots}\n"
        f"writes_armed={str(writes_armed).lower()}\n"
        f"mode={mode}\n"
        f"plan_approved={str(plan_approved).lower()}\n"
        f"todos: {todo_line}\n"
        f"files_read: {files_line}\n"
        f"latest_user: {latest}\n"
        "---\n"
    )
    lines: List[str] = []
    pending_explore: Optional[Tuple[str, str]] = None
    for m in messages:
        role = m.get("role") or "unknown"
        if role == "system":
            continue
        if role == "assistant" and m.get("tool_calls"):
            pending_explore = _explore_from_tool_calls(m.get("tool_calls"))
        content = m.get("content")
        if role == "tool" and (m.get("name") or "") in EXPLORE_KEYS:
            name = str(m.get("name") or "")
            label = "path=(unknown)"
            if pending_explore and pending_explore[0] == name:
                label = pending_explore[1]
            content = _compact_explore_line(name, label, str(content or ""))
            pending_explore = None
        elif not content and m.get("tool_calls"):
            content = f"[tool_calls]{m.get('tool_calls')}"
        lines.append(f"{role}: {content or ''}")
    body = "\n".join(lines)
    while len(header) + len(body) > STATE_CHAR_BUDGET and len(lines) > 1:
        lines.pop(0)
        body = "\n".join(lines)
    if len(header) + len(body) > STATE_CHAR_BUDGET:
        available = max(0, STATE_CHAR_BUDGET - len(header))
        role_prefix, separator, content = body.partition(": ")
        prefix = role_prefix + separator
        content_budget = max(0, available - len(prefix))
        body = prefix + (content[-content_budget:] if content_budget else "")
    return header + body


def build_request(
    state: str, *, model: Optional[str] = None, prefer_reply: bool = False
) -> Dict[str, Any]:
    instructions = (
        "Pick the single best next action. If the user already gave a clear "
        "multi-step task (e.g. audit/read files) and work is in progress, "
        "prefer the next tool over ask_clarify or reply. Use ask_clarify only "
        "when you cannot proceed without a user answer."
    )
    criteria = choice_criteria()
    if prefer_reply:
        instructions = (
            "Pick the single best next action. The user is asking for a "
            "report/summary/audit/findings and files_read is non-empty — "
            "prefer reply with findings over more list_dir/search_files/read_file. "
            "Use ask_clarify only when you cannot proceed without a user answer."
        )
        criteria = dict(criteria)
        criteria[REPLY_KEY] = (
            "Final answer or audit/report findings from already-read files; "
            "prefer this over more exploration when the user asked for a report"
        )
    return {
        "state": state,
        "model": model or jev_model(),
        "questions": {
            QUESTION_ID: {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria,
            }
        },
    }


async def _post_systemone(
    client: httpx.AsyncClient,
    *,
    base: str,
    api_key: str,
    payload: Dict[str, Any],
    cancel: Optional[asyncio.Event],
    unreachable_prefix: str,
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{base.rstrip('/')}/v1/systemone"
    try:
        if cancel is not None and cancel.is_set():
            raise JevPickerError(
                "Jev cancelled: stopped by user", kind=JEV_KIND_CANCELLED
            )
        post_task = asyncio.create_task(
            client.post(url, json=payload, headers=headers, timeout=60)
        )
        if cancel is None:
            r = await post_task
        else:
            cancel_task = asyncio.create_task(cancel.wait())
            done, pending = await asyncio.wait(
                {post_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            if cancel_task in done and cancel.is_set():
                post_task.cancel()
                raise JevPickerError(
                    "Jev cancelled: stopped by user", kind=JEV_KIND_CANCELLED
                )
            r = await post_task
    except JevPickerError:
        raise
    except Exception as e:
        raise JevPickerError(
            f"{unreachable_prefix}: {e}", kind=JEV_KIND_UNREACHABLE
        ) from e
    if r.status_code >= 400:
        raise JevPickerError(
            f"{unreachable_prefix}: HTTP {r.status_code}: {r.text[:300]}",
            kind=JEV_KIND_HTTP,
        )
    return r.json()


def _parse_choice(data: Dict[str, Any]) -> Tuple[str, float]:
    try:
        ans = (data.get("answers") or {})[QUESTION_ID]
        choice = ans["choice"]
        conf = float(ans.get("confidence") or 0.0)
    except Exception as e:
        raise JevPickerError(
            f"Jev response malformed: {e}", kind=JEV_KIND_MALFORMED
        ) from e
    if (
        choice != REPLY_KEY
        and choice not in TOOL_KEYS
        and choice not in CONTROL_KEYS
    ):
        raise JevPickerError(
            f"Jev returned unknown option: {choice!r}",
            kind=JEV_KIND_UNKNOWN_OPTION,
        )
    return str(choice), conf


async def pick_next_action(
    client: httpx.AsyncClient,
    *,
    messages: List[Dict[str, Any]],
    roots: List[str],
    writes_armed: bool,
    mode: str = "normal",
    plan_approved: bool = False,
    todos: Optional[List[Dict[str, Any]]] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    ollama_url: Optional[str] = None,
    tev1: Optional[str] = None,
    cancel: Optional[asyncio.Event] = None,
) -> Tuple[str, float, str]:
    key = (api_key if api_key is not None else typesafe_api_key()).strip()
    state = build_state(
        messages,
        roots=roots,
        writes_armed=writes_armed,
        mode=mode,
        plan_approved=plan_approved,
        todos=todos,
    )
    prefer_reply = prefer_reply_from_files(messages)
    jev_err: Optional[str] = None
    if key:
        payload = build_request(state, model=model, prefer_reply=prefer_reply)
        try:
            data = await _post_systemone(
                client,
                base=(base_url or typesafe_base_url()),
                api_key=key,
                payload=payload,
                cancel=cancel,
                unreachable_prefix="Jev unreachable",
            )
            choice, conf = _parse_choice(data)
            return choice, conf, "jev"
        except JevPickerError as e:
            if e.kind in (
                JEV_KIND_CANCELLED,
                JEV_KIND_MALFORMED,
                JEV_KIND_UNKNOWN_OPTION,
            ):
                raise
            jev_err = e.message
    else:
        jev_err = "Jev unavailable (no API key)"

    tev1_name = (tev1 if tev1 is not None else tev1_model()).strip() or DEFAULT_TEV1_MODEL
    o_base = (ollama_url if ollama_url is not None else ollama_base_url()).rstrip("/")
    payload = build_request(state, model=tev1_name, prefer_reply=prefer_reply)
    try:
        data = await _post_systemone(
            client,
            base=o_base,
            api_key="ollama",
            payload=payload,
            cancel=cancel,
            unreachable_prefix="Tev1 fallback failed",
        )
        choice, conf = _parse_choice(data)
        return choice, conf, "tev1"
    except JevPickerError as e:
        if e.kind == JEV_KIND_CANCELLED:
            raise
        detail = e.message
        if e.kind == JEV_KIND_MALFORMED:
            detail = detail.replace(
                "Jev response malformed", "Tev1 response malformed", 1
            )
        elif e.kind == JEV_KIND_UNKNOWN_OPTION:
            detail = detail.replace(
                "Jev returned unknown", "Tev1 returned unknown", 1
            )
        raise JevPickerError(
            f"{jev_err}; {detail}", kind=JEV_KIND_TEV1_FAILED
        ) from e
