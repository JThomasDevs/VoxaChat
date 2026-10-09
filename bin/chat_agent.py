"""Ollama chat agent loop with filesystem tools for Voxa."""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

import context_repair as cr
import fs_tools as ft
import jev_picker as jp
import plan_run as pr
import vision_models as vm
import write_batch as wb

PENDING_TTL_SEC = 300
MAX_ROUNDS = pr.MAX_ROUNDS_DEFAULT


def windows_mode(body: Optional[Dict[str, Any]] = None) -> bool:
    if body is not None and "windows" in body:
        value = body.get("windows")
        if isinstance(value, bool):
            return value
        if value is not None:
            return str(value).lower().strip() in ("1", "true", "yes", "on")
    env = (os.environ.get("VOXA_WINDOWS") or "").lower().strip()
    return env in ("1", "true", "yes", "on")


# Offer tools when the user message looks tool-related, unless tools_mode=always.
# Weak tool models (e.g. mistral-nemo) otherwise greet with file offers or call tools on chit-chat.
_TOOL_INTENT_RE = re.compile(
    r"("
    # Files / workspace
    r"\b(read|open|list|ls|cat|show|search|find|grep|edit|write|create|save|delete|remove|move|rename|mkdir|"
    r"inspect|check|look\s+at|what'?s\s+in|contents?\s+of)\b.*"
    r"\b(file|files|dir|directory|folder|folders|path|codebase|repo|workspace|disk|project)\b"
    r"|"
    r"\b(file|files|directory|folder|codebase|repo|readme|workspace|project)\b.*"
    r"\b(read|open|list|search|find|edit|write|create|delete|move|rename|fix|change|update)\b"
    r"|"
    r"(^|[\s`\"'])(/[\w./~-]+|~/[\w./-]+|\./[\w./-]+)"  # explicit path
    r"|"
    r"\b\w+\.(py|js|ts|tsx|md|json|yaml|yml|toml|sh|css|html|txt|rs|go)\b"
    r"|"
    # Weather
    r"\b(weather|temperature|forecast|raining|humidity|how\s+hot|how\s+cold)\b"
    r"|"
    # Shell / dev commands
    r"\b(run|execute|shell|terminal|bash|command|npm|pip|pytest|unittest|make|docker|"
    r"install|deps|dependencies|build|compile|lint|format)\b"
    r"|"
    r"\bgit\s+(status|log|diff|add|commit|push|pull|branch|checkout|stash|clone)\b"
    r"|"
    # Web / lookup
    r"\b(web\s*search|search\s+the\s+web|look\s*up|google|duckduckgo|"
    r"latest\s+version|docs?\s+for|documentation|how\s+do\s+i|what\s+is\s+the\s+url)\b"
    r"|"
    r"https?://"
    r"|"
    # Coding tasks that usually need tools
    r"\b(bug|traceback|stack\s*trace|failing\s+test|fix\s+the|debug|"
    r"refactor|patch|diff|pull\s+request|pr\b|commit\b)\b"
    r")",
    re.IGNORECASE,
)

# Lightweight classifiers for per-turn tool hints (help the model pick the right tool).
_HINT_WEATHER = re.compile(r"\b(weather|temperature|forecast|raining|humidity|how\s+hot|how\s+cold)\b", re.I)
_HINT_SHELL = re.compile(
    r"\b(run|execute|shell|terminal|bash|npm|pip|pytest|unittest|make|docker|install|"
    r"build|lint|git\s+\w+|failing\s+test|deps)\b",
    re.I,
)
_HINT_WEB = re.compile(
    r"\b(web\s*search|search\s+the\s+web|look\s*up|google|latest\s+version|docs?\s+for|"
    r"documentation|how\s+do\s+i)\b|https?://",
    re.I,
)
_HINT_FILES = re.compile(
    r"\b(file|files|dir|directory|folder|readme|codebase|repo|workspace|project|"
    r"edit|write|read|list|search|grep|mkdir|delete|move|rename|refactor|patch|diff)\b|"
    r"\b\w+\.(py|js|ts|tsx|md|json|yaml|yml|toml|sh|css|html|txt|rs|go)\b|"
    r"(^|[\s`\"'])(/[\w./~-]+|~/[\w./-]+|\./[\w./-]+)",
    re.I,
)

# id -> {"future": Future, "kind": "write"|"grant", "created": float, "roots": list}
_PENDING: Dict[str, Dict[str, Any]] = {}
# shell pending id -> threading.Event (survives resolve_shell_decision popping _PENDING)
_SHELL_CANCEL_EVENTS: Dict[str, threading.Event] = {}


def _set_future_result(fut: asyncio.Future, value: Dict[str, Any]) -> None:
    """Set a future's result from any thread (resolve may run off the event loop)."""
    if fut.done():
        return
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    loop = fut.get_loop()
    if running is loop or loop.is_closed():
        fut.set_result(value)
        return

    def _set() -> None:
        if not fut.done():
            fut.set_result(value)

    loop.call_soon_threadsafe(_set)


def _cleanup_expired() -> None:
    now = time.time()
    dead = [k for k, v in list(_PENDING.items()) if now - v["created"] > PENDING_TTL_SEC]
    for k in dead:
        entry = _PENDING.pop(k, None)
        _SHELL_CANCEL_EVENTS.pop(k, None)
        if entry and not entry["future"].done():
            entry["future"].set_result({"deny": True, "reason": "timeout"})


def _new_pending(
    kind: str, roots: List[str], *, pending_id: Optional[str] = None, **extra
) -> tuple[str, asyncio.Future]:
    _cleanup_expired()
    pid = pending_id or str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _PENDING[pid] = {
        "future": fut,
        "kind": kind,
        "created": time.time(),
        "roots": list(roots),
    }
    for k, v in extra.items():
        _PENDING[pid][k] = v
    if kind == "shell":
        # Threading event so a turn cancel can kill the approved subprocess.
        ev = _PENDING[pid].setdefault("cancel_event", threading.Event())
        # Approve pops the entry; keep the event reachable until the turn ends.
        _SHELL_CANCEL_EVENTS[pid] = ev
    return pid, fut


def resolve_write_decision(pid: str, approve: bool) -> bool:
    _cleanup_expired()
    entry = _PENDING.pop(pid, None)
    if not entry:
        return False
    if not entry["future"].done():
        entry["future"].set_result({"approve": bool(approve)})
    return True


def resolve_writes_batch_decision(pid: str, approved_ids: List[str]) -> bool:
    """Resolve a pending writes batch (kind=writes_batch) with the approved item ids.

    Ids not listed are denied; an empty list denies the whole batch.
    Unknown id / wrong kind → False (wrong-kind pendings are left untouched).
    """
    _cleanup_expired()
    entry = _PENDING.get(pid)
    if not entry or entry.get("kind") != "writes_batch":
        return False
    _PENDING.pop(pid, None)
    if not entry["future"].done():
        entry["future"].set_result({"approved_ids": list(approved_ids or [])})
    return True


def resolve_tool_permission(pid: str, approve: bool) -> bool:
    _cleanup_expired()
    entry = _PENDING.pop(pid, None)
    if not entry or entry.get("kind") != "tool_permission":
        return False
    if not entry["future"].done():
        entry["future"].set_result({"approve": bool(approve)})
    return True


def resolve_shell_decision(pid: str, approve: bool) -> bool:
    """Approve/deny a pending shell_run call. On approve, executes the
    command (capturing stdout/stderr/exit_code) and stores the result on
    the pending future. On deny, marks it denied so the chat loop surfaces
    that to the model."""
    _cleanup_expired()
    entry = _PENDING.pop(pid, None)
    if not entry or entry.get("kind") != "shell":
        return False
    if not approve:
        _SHELL_CANCEL_EVENTS.pop(pid, None)
        if not entry["future"].done():
            entry["future"].set_result({"deny": True})
        return True
    command = entry.get("command", "")
    cwd = entry.get("cwd")
    if not cwd:
        roots = entry.get("roots") or []
        cwd = roots[0] if roots else "."
    # Blocks until the command ends or the turn cancel bridge sets cancel_event.
    # Callers on an event loop should run this in a worker thread (server does).
    output = ft._shell_execute(command, cwd, cancel_event=entry.get("cancel_event"))
    _SHELL_CANCEL_EVENTS.pop(pid, None)
    _set_future_result(entry["future"], {"approve": True, "output": output})
    return True


def resolve_plan_decision(pid: str, action: str, note: Optional[str] = None) -> bool:
    """Approve or revise a pending plan (kind=plan). Unknown id / wrong kind → False."""
    _cleanup_expired()
    action = (action or "").strip().lower()
    entry = _PENDING.get(pid)
    if not entry or entry.get("kind") != "plan":
        return False
    if action not in {"approve", "revise"}:
        return False
    _PENDING.pop(pid, None)
    if not entry["future"].done():
        entry["future"].set_result({"action": action, "note": (note or "").strip()})
    return True


def resolve_workspace_grant(
    pid: str,
    *,
    root: Optional[str] = None,
    deny: bool = False,
    workspace_roots: Optional[List[str]] = None,
) -> bool:
    _cleanup_expired()
    entry = _PENDING.pop(pid, None)
    if not entry:
        return False
    if not entry["future"].done():
        entry["future"].set_result(
            {
                "deny": bool(deny),
                "root": root,
                "workspace_roots": workspace_roots,
            }
        )
    return True


def _sse(obj: Dict[str, Any]) -> Dict[str, Any]:
    return obj


def _exc_message(exc: BaseException, *, fallback: str = "unknown error") -> str:
    """Human-readable exception text; httpx often uses empty ``str(e)``."""
    text = str(exc).strip()
    if text:
        return text
    name = type(exc).__name__
    return f"{fallback} ({name})"


def _is_clarifying_question(text: str) -> bool:
    """True when clarify text looks like a real question for the user."""
    s = (text or "").strip()
    if not s:
        return False
    if "?" in s:
        return True
    lower = s.lower()
    return any(
        p in lower
        for p in (
            "which ",
            "should i ",
            "do you want",
            "can you confirm",
            "please confirm",
            "pick one",
            "choose one",
            "a or b",
        )
    )


IDENTICAL_TOOL_FAIL_LIMIT = 3


def _tool_path_arg(args: Dict[str, Any]) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("path", "root", "src", "dst"):
        val = args.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _tool_fail_signature(name: str, args: Dict[str, Any], result: str) -> str:
    return f"{name}\0{_tool_path_arg(args)}\0{(result or '')[:300]}"


def _note_repeated_tool_failure(
    tracker: Dict[str, Any],
    *,
    name: str,
    args: Dict[str, Any],
    ok: bool,
    result: str,
) -> Optional[Dict[str, Any]]:
    """After N identical failures, nudge the picker off the same broken call."""
    if ok:
        tracker["sig"] = None
        tracker["count"] = 0
        return None
    sig = _tool_fail_signature(name, args, result)
    if sig == tracker.get("sig"):
        tracker["count"] = int(tracker.get("count") or 0) + 1
    else:
        tracker["sig"] = sig
        tracker["count"] = 1
    if int(tracker.get("count") or 0) < IDENTICAL_TOOL_FAIL_LIMIT:
        return None
    tracker["sig"] = None
    tracker["count"] = 0
    path = _tool_path_arg(args) or "(no path)"
    return {
        "role": "user",
        "content": (
            f"(system) `{name}` failed {IDENTICAL_TOOL_FAIL_LIMIT} times with the same "
            f"arguments (path={path!r}). Do not retry that exact call. "
            'Try list_dir path "." from the workspace root, search_files with a '
            "broader root, or reply explaining the blocker."
        ),
    }


def _maybe_collapse_explore(messages: List[Dict[str, Any]]) -> None:
    try:
        cr.collapse_explore_failures(messages)
    except Exception:
        # Fail safe: never break the tool loop on repair bugs.
        return


def _shell_output_cancelled(output: Any) -> bool:
    """True when _shell_execute reported a cancel (JSON {"cancelled": true})."""
    try:
        parsed = json.loads(output)
    except Exception:
        return False
    return isinstance(parsed, dict) and bool(parsed.get("cancelled"))


class ChatCancelled(Exception):
    """Client stopped the turn (disconnect / Stop)."""


def cancel_turn_pendings(pending_ids: List[str]) -> None:
    """Resolve this turn's pending approve futures as cancelled deny."""
    for pid in list(pending_ids):
        entry = _PENDING.pop(pid, None)
        # Bridge turn cancel -> in-flight shell (entry may already be popped by approve).
        shell_ev = _SHELL_CANCEL_EVENTS.pop(pid, None)
        if shell_ev is not None:
            shell_ev.set()
        if entry and entry.get("cancel_event") is not None:
            entry["cancel_event"].set()
        if entry and not entry["future"].done():
            entry["future"].set_result({"deny": True, "reason": "cancelled"})


async def _ollama_chat_stream(
    client: httpx.AsyncClient,
    ollama_url: str,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    cancel: Optional[asyncio.Event] = None,
    emit_tokens: bool = True,
) -> AsyncIterator[Dict[str, Any]]:
    """Stream Ollama chat; yield token SSE dicts; final ollama_message event."""
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
    }
    if tools is not None:
        payload["tools"] = tools

    role = "assistant"
    content_parts: List[str] = []
    thinking_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    try:
        async with client.stream(
            "POST",
            f"{ollama_url}/api/chat",
            json=payload,
            timeout=300,
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread())[:500]
                raise RuntimeError(
                    f"ollama error {resp.status_code}: {body!r}"
                )
            async for line in resp.aiter_lines():
                if cancel is not None and cancel.is_set():
                    await resp.aclose()
                    raise ChatCancelled()
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError as e:
                    raise RuntimeError(f"ollama stream malformed: {e}") from e
                if chunk.get("error"):
                    raise RuntimeError(f"ollama stream error: {chunk.get('error')}")
                msg = chunk.get("message") or {}
                if msg.get("role"):
                    role = msg["role"]
                thinking_delta = msg.get("thinking") or ""
                if thinking_delta:
                    thinking_parts.append(thinking_delta)
                    yield {"type": "thinking", "content": thinking_delta}
                delta = msg.get("content") or ""
                if delta:
                    content_parts.append(delta)
                    if emit_tokens:
                        yield {"type": "token", "content": delta}
                # Ollama may send tool_calls on the final chunk (full list)
                if msg.get("tool_calls"):
                    tool_calls = list(msg["tool_calls"])
                if cancel is not None and cancel.is_set():
                    await resp.aclose()
                    raise ChatCancelled()
    except ChatCancelled:
        raise
    except httpx.HTTPError as e:
        if cancel is not None and cancel.is_set():
            raise ChatCancelled() from e
        raise RuntimeError(
            f"ollama stream interrupted: {_exc_message(e, fallback='connection lost')}"
        ) from e

    if cancel is not None and cancel.is_set():
        raise ChatCancelled()

    assembled: Dict[str, Any] = {
        "role": role,
        "content": "".join(content_parts),
    }
    if thinking_parts:
        assembled["thinking"] = "".join(thinking_parts)
    if tool_calls:
        assembled["tool_calls"] = tool_calls
    yield {"type": "ollama_message", "message": assembled}

async def _ollama_chat(
    client: httpx.AsyncClient,
    ollama_url: str,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if tools is not None:
        payload["tools"] = tools
    r = await client.post(f"{ollama_url}/api/chat", json=payload, timeout=300)
    if r.status_code >= 400:
        raise RuntimeError(f"ollama error {r.status_code}: {r.text[:500]}")
    return r.json()


def _tool_call_parts(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    calls = message.get("tool_calls") or []
    out = []
    for i, tc in enumerate(calls):
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": args}
        out.append(
            {
                "id": tc.get("id") or f"call_{i}",
                "name": name,
                "args": args if isinstance(args, dict) else {},
            }
        )
    return out


async def _await_user(
    fut: asyncio.Future,
    timeout: float = PENDING_TTL_SEC,
    cancel: Optional[asyncio.Event] = None,
) -> Dict[str, Any]:
    if cancel is None:
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return {"deny": True, "reason": "timeout"}
    deadline = time.monotonic() + timeout
    while True:
        if fut.done():
            return fut.result()
        if cancel.is_set():
            return {"deny": True, "reason": "cancelled"}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"deny": True, "reason": "timeout"}
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=min(0.05, remaining))
        except asyncio.TimeoutError:
            continue

def _last_user_text(messages: List[Dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content") or "")
    return ""


def _normalize_messages_images(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep only non-empty string images on user messages; drop everything else.

    ``images`` must be a list. A bare string is ignored (iterating it would
    falsely treat each character as an image and block non-vision models).
    """
    out: List[Dict[str, Any]] = []
    for m in messages:
        mm = dict(m)
        raw = mm.get("images")
        if mm.get("role") == "user" and isinstance(raw, list):
            imgs = []
            for v in raw:
                if isinstance(v, str) and v.strip():
                    imgs.append(vm.normalize_image_b64(v))
            if imgs:
                mm["images"] = imgs
            else:
                mm.pop("images", None)
        else:
            mm.pop("images", None)
        out.append(mm)
    return out


def _user_messages_have_images(messages: List[Dict[str, Any]]) -> bool:
    """True only when at least one user message has a non-empty images list."""
    for m in messages:
        if m.get("role") != "user":
            continue
        imgs = m.get("images")
        if isinstance(imgs, list) and any(
            isinstance(v, str) and v.strip() for v in imgs
        ):
            return True
    return False


def _reject_images_if_unsupported(
    model: str, messages: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Return a single error SSE dict if illegal; else None.

    Text-only requests (no real image payloads) are always allowed, including
    for non-vision models. Reject only when ≥1 image is present and the model
    fails the vision heuristic.
    """
    if _user_messages_have_images(messages) and not vm.model_supports_vision(model):
        return {
            "type": "error",
            "message": (
                "Selected model does not support images. "
                "Pick a vision model (e.g. llava, qwen2-vl)."
            ),
        }
    return None


def _should_offer_tools(tools_mode: str, messages: List[Dict[str, Any]]) -> bool:
    if _chat_tools_env_off():
        return False
    mode = (tools_mode or "always").lower().strip()
    if mode in ("off", "none", "false", "0"):
        return False
    if mode in ("always", "on", "true", "1"):
        return True
    # when_asked (API escape hatch): intent regex
    return bool(_TOOL_INTENT_RE.search(_last_user_text(messages)))


def _schema_for_tool(name: str) -> Optional[Dict[str, Any]]:
    for schema in ft.TOOL_SCHEMAS:
        if schema["function"]["name"] == name:
            return schema
    return None


def _force_tool_system(name: str) -> str:
    return (
        f"This turn you MUST call the tool `{name}` with appropriate arguments. "
        "Do not call any other tool. Do not reply with plain text."
    )


_EMPTY_REPLY_NUDGE = (
    "(system) Your previous turn produced no user-visible reply text — internal "
    "thinking alone does not count. Write the full answer to the user now as "
    "normal reply text. Do not call tools."
)

_EMPTY_REPLY_FALLBACK = (
    "(Model returned an empty reply. Ask me to continue, or try again.)"
)


async def _stream_user_text_reply(
    client: httpx.AsyncClient,
    ollama_url: str,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    cancel: Optional[asyncio.Event] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Stream a no-tools reply; retry once if content is empty (thinking-only models).

    Yields SSE-shaped dicts (`thinking` / `token`) plus a final
    ``{"type": "final_text", "content": str}``.
    """
    attempt_messages = list(messages)
    final = ""
    for attempt in range(2):
        assembled: Optional[Dict[str, Any]] = None
        async for ev in _ollama_chat_stream(
            client,
            ollama_url,
            model=model,
            messages=attempt_messages,
            tools=None,
            cancel=cancel,
            emit_tokens=True,
        ):
            if ev.get("type") == "ollama_message":
                assembled = ev.get("message") or {}
            elif ev.get("type") in ("token", "thinking"):
                yield ev
        if assembled is None:
            raise RuntimeError("ollama returned no message")
        final = (assembled.get("content") or "").strip()
        if final:
            break
        if attempt == 0:
            attempt_messages = list(messages) + [
                {"role": "user", "content": _EMPTY_REPLY_NUDGE}
            ]
    if not final:
        final = _EMPTY_REPLY_FALLBACK
        yield {"type": "token", "content": final}
    yield {"type": "final_text", "content": final}


def _force_reply_after_tool(name: str, args: Dict[str, Any], ok: bool) -> bool:
    """After a successful disk mutation, stop picking tools and reply (or end).

    Prevents the picker from looping write/edit/verify for the rest of MAX_ROUNDS.
    Preview-only edit_file does not count.
    """
    if not ok or name not in ft.WRITE_TOOLS:
        return False
    if name == "edit_file" and not bool(args.get("apply")):
        return False
    return True


def _force_reply_after_tool_for_state(
    name: str, args: Dict[str, Any], ok: bool, state: "pr.RunState"
) -> bool:
    """Ad-hoc force-reply, disabled once an approved plan is being implemented."""
    return _force_reply_after_tool(name, args, ok) and pr.should_force_reply_after_tool(
        state
    )


def _jev_cancel_events(
    state: "pr.RunState", pending_ids: List[str]
) -> List[Dict[str, Any]]:
    """Cancel this turn's pendings; mark in-progress todos cancelled; build final SSE."""
    cancel_turn_pendings(pending_ids)
    pr.cancel_in_progress_todos(state)
    events: List[Dict[str, Any]] = []
    if state.todos:
        events.append(_sse({"type": "todos", "todos": [dict(t) for t in state.todos]}))
    events.append(_sse({"type": "cancelled", "message": "stopped by user"}))
    return events


def _batch_item_event(item: "wb.BatchItem") -> Dict[str, Any]:
    return {
        "item_id": item.item_id,
        "tool": item.tool,
        "args": item.args,
        "path": item.path,
        "preview": item.preview,
    }


def _batch_resolution_note(
    batch: "wb.WriteBatch",
    applied: List[str],
    denied: List["wb.BatchItem"],
    errors: List[Dict[str, str]],
) -> str:
    by_id = {i.item_id: i for i in batch.items}
    parts = ["(system) Queued writes were reviewed by the user."]
    parts.append(
        "Applied: " + (", ".join(by_id[i].path for i in applied) if applied else "none") + "."
    )
    if denied:
        parts.append("Denied by user (NOT written): " + ", ".join(d.path for d in denied) + ".")
    if errors:
        parts.append(
            "Failed (NOT written): "
            + "; ".join(f"{by_id[e['item_id']].path}: {e['error']}" for e in errors)
            + "."
        )
    parts.append("Do not claim denied or failed files were written.")
    return " ".join(parts)


async def _flush_write_batch(
    batch: "wb.WriteBatch",
    roots: List[Any],
    root_strs: List[str],
    turn_pending_ids: List[str],
    cancel: Optional[asyncio.Event],
    out: Dict[str, Any],
    state: Optional["pr.RunState"] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Ask the user about the queued writes, apply approved ones, emit SSE.

    Async generator, so results are reported through ``out``:
    ``cancelled`` (bool; nothing applied, caller must emit cancel events),
    ``force_reply`` (bool; an applied write should end the ad-hoc tool loop) and
    ``note`` (user-role message telling the model what was applied/denied; the
    caller appends it where message ordering allows).
    The pending id equals ``batch.batch_id`` so the UI can POST its decision.
    """
    out.clear()
    out["cancelled"] = False
    out["force_reply"] = False
    out["note"] = None
    if not batch.items:
        return
    pid, fut = _new_pending(
        "writes_batch",
        root_strs,
        pending_id=batch.batch_id,
        items=[_batch_item_event(i) for i in batch.items],
    )
    turn_pending_ids.append(pid)
    yield _sse(
        {
            "type": "writes_batch_pending",
            "id": pid,
            "items": [_batch_item_event(i) for i in batch.items],
        }
    )
    decision = await _await_user(fut, cancel=cancel)
    if decision.get("reason") == "cancelled" or (
        cancel is not None and cancel.is_set()
    ):
        out["cancelled"] = True
        return
    # timeout / deny without approved_ids → deny all
    approved_ids = [] if decision.get("deny") else list(decision.get("approved_ids") or [])
    to_apply, denied = wb.partition_decision(batch, approved_ids)
    applied, errors = wb.apply_items(to_apply, roots, ft.run_tool)
    resolved_state = state if state is not None else pr.RunState()
    applied_set = set(applied)
    out["force_reply"] = any(
        _force_reply_after_tool_for_state(i.tool, i.args, True, resolved_state)
        for i in batch.items
        if i.item_id in applied_set
    )
    out["note"] = {
        "role": "user",
        "content": _batch_resolution_note(batch, applied, denied, errors),
    }
    event: Dict[str, Any] = {
        "type": "writes_batch_resolved",
        "id": pid,
        "applied": applied,
        "denied": [d.item_id for d in denied],
    }
    if errors:
        event["errors"] = errors
    yield _sse(event)


def _mode_system_note(state: "pr.RunState") -> Optional[str]:
    if state.mode == "implementing" and state.plan_approved:
        return (
            "The user approved the plan. Execute it step by step with tools and keep "
            "the todo checklist current. Do not ask for plan approval again unless the "
            "user revised the plan."
        )
    if pr.mutating_held(state):
        return (
            "Plan is not approved yet: do not modify files or run shell commands. "
            "Clarify or propose a plan."
        )
    if state.mode == "normal":
        return (
            "For multi-step implement/refactor requests, prefer ask_clarify or "
            "propose_plan before any file write or shell command. Simple lookups and "
            "small edits can use tools directly."
        )
    return None


def _coerce_todos(raw: Any) -> List[Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    return list(raw) if isinstance(raw, list) else []


def _tool_hints_for_text(text: str) -> List[str]:
    """Short cues appended to the system prompt so the model picks tools."""
    hints: List[str] = []
    if _HINT_WEATHER.search(text):
        hints.append("weather → get_weather")
    if _HINT_SHELL.search(text):
        hints.append("commands/git/tests/install → shell_run")
    if _HINT_WEB.search(text):
        hints.append("lookup/docs/URLs → web_search then web_fetch")
    if _HINT_FILES.search(text):
        hints.append("local files/code → list_dir / read_file / search_files / edit_file")
    return hints


SYSTEM_PROMPT_PLAIN = (
    "You are Voxa's local chat assistant. Reply helpfully and directly.\n"
    "Never invent calendar dates or clock times. Use the Current date/time line below when relevant."
)

SYSTEM_PROMPT_TOOLS = (
    "You are Voxa's local chat assistant with tools. Reply helpfully and directly.\n"
    "\n"
    "When to use tools (prefer tools over guessing):\n"
    "- Local files/code you need to see or change → list_dir, read_file, search_files, "
    "edit_file (preview with apply=false, then apply=true), write_file, mkdir, delete_file, move_path.\n"
    "- Run commands, git, tests, installs, builds → shell_run (UI will ask the user to Approve; "
    "just call it — do not ask in text).\n"
    "- Unknown facts, package versions, docs, recent events, or a URL → web_search, then web_fetch.\n"
    "- Current weather / temperature / forecast → get_weather "
    "(needs OPENWEATHERMAP_API_KEY in .env; never shell-export it).\n"
    "\n"
    "When NOT to use tools: greetings, opinions, pure reasoning, or answers you already know "
    "from this conversation — except live weather/temps, which always require get_weather.\n"
    "Modes:\n"
    "- normal: prefer tools for lookups and small edits — call the tool, then answer from results. "
    "For multi-step implement/refactor requests, prefer ask_clarify or propose_plan before any "
    "file write or shell command.\n"
    "- clarifying / planning / awaiting_approval: do NOT modify files or run shell commands; "
    "clarify or propose a plan (summary + todo checklist) for the user to Approve or Revise.\n"
    "- implementing (plan approved): execute the approved plan with tools, call update_todo as "
    "steps progress, and do not ask for plan approval again unless the user revised it.\n"
    "Do not ask which files to open for simple lookups — call the tool, then answer from results.\n"
    "Never invent file contents, command output, or live web/weather data when a tool can fetch it.\n"
    "After a tool returns JSON or text, report those values faithfully. "
    "If a tool returns an error, say it failed — do not invent weather, dates, temps, or pages. "
    "For get_weather, use observed_at / temp / description from the tool only. "
    "If the user asks about current weather and you have not called get_weather yet, call it — "
    "never answer weather from memory.\n"
    "Never invent calendar dates or clock times. Use the Current date/time line below, "
    "or observed_at / timestamps from tool results. If unsure, say you do not know."
)


def _clock_line() -> str:
    """Ground-truth clock for the model (local + UTC)."""
    local = datetime.now().astimezone()
    utc = datetime.now(timezone.utc)
    tz_name = local.tzname() or local.strftime("%z")
    return (
        f"Current date/time: {local.strftime('%A, %B %d, %Y %I:%M %p')} "
        f"({tz_name}, offset {local.strftime('%z')}); "
        f"UTC {utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )


def _workspace_prompt_line(root_strs: List[str]) -> str:
    if not root_strs:
        return (
            "No workspace roots are granted. Ask the user to Add a workspace folder "
            "in the chat header before using file or shell tools."
        )
    lines = [
        "Granted workspace roots (relative tool paths use the primary root as cwd; "
        'list_dir path "." means the primary root):'
    ]
    for i, root in enumerate(root_strs):
        suffix = " (primary / working directory)" if i == 0 else ""
        lines.append(f"- {root}{suffix}")
    return "\n".join(lines)


def _system_prompt_for_turn(
    messages: List[Dict[str, Any]],
    *,
    tools: bool,
    root_strs: Optional[List[str]] = None,
) -> str:
    base = SYSTEM_PROMPT_TOOLS if tools else SYSTEM_PROMPT_PLAIN
    text = _last_user_text(messages)
    parts = [base, _clock_line()]
    if tools or root_strs is not None:
        parts.append(_workspace_prompt_line(list(root_strs or [])))
    if tools:
        hints = _tool_hints_for_text(text)
        if hints:
            parts.append("Likely for this message: " + "; ".join(hints) + ".")
    return "\n".join(parts)


def _required_tools_for_text(text: str) -> frozenset:
    """Tools the model must call before a final answer for this user text."""
    required: set[str] = set()
    if _HINT_WEATHER.search(text or ""):
        required.add("get_weather")
    return frozenset(required)


def _tools_mode_off(tools_mode: str) -> bool:
    return (tools_mode or "always").lower().strip() in ("off", "none", "false", "0")


def _chat_tools_env_off() -> bool:
    raw = (os.environ.get("VOXA_CHAT_TOOLS") or "").strip().lower()
    return raw in ("false", "0", "off", "none")


def _run_flags(body: Dict[str, Any]) -> tuple[bool, bool]:
    """Return (run_everything, writes_armed).

    run_everything auto-approves write batch, shell_run, and sensitive
    tool_permission — but never plan Approve. It implies writes_armed.
    """
    run_everything = bool(body.get("run_everything"))
    writes_armed = bool(body.get("writes_armed")) or run_everything
    return run_everything, writes_armed


async def iter_chat_events(
    body: Dict[str, Any],
    ollama_url: str,
    *,
    cancel: Optional[asyncio.Event] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Route chat to plain Ollama (tools off) or hosted Jev picker loop."""
    tools_mode = str(body.get("tools_mode") or "always")
    classic_body = body
    if _chat_tools_env_off():
        tools_mode = "off"
        classic_body = {**body, "tools_mode": "off"}
    iterator = (
        _iter_classic_chat_events(classic_body, ollama_url, cancel=cancel)
        if _tools_mode_off(tools_mode)
        else _iter_jev_chat_events(body, ollama_url, cancel=cancel)
    )
    async for event in iterator:
        yield event


async def _iter_classic_chat_events(
    body: Dict[str, Any],
    ollama_url: str,
    *,
    cancel: Optional[asyncio.Event] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Plain Ollama streaming chat when tools_mode is off (no Jev, no tools)."""
    messages: List[Dict[str, Any]] = _normalize_messages_images(
        list(body.get("messages") or [])
    )
    model = body.get("model") or "mistral-nemo"
    rej = _reject_images_if_unsupported(model, messages)
    if rej:
        yield _sse(rej)
        return
    run_everything, writes_armed = _run_flags(body)
    raw_roots = list(body.get("workspace_roots") or [])
    tools_mode = str(body.get("tools_mode") or "always")

    if not messages:
        yield _sse({"type": "error", "message": "messages required"})
        return

    if raw_roots:
        try:
            roots = ft.normalize_roots(raw_roots)
            root_strs = [str(root) for root in roots]
        except ft.FsError as exc:
            yield _sse({"type": "error", "message": exc.message})
            return
    else:
        roots = []
        root_strs = []

    tools_enabled = _should_offer_tools(tools_mode, messages)
    has_system = any(m.get("role") == "system" for m in messages)
    if not has_system:
        messages = [
            {
                "role": "system",
                "content": _system_prompt_for_turn(
                    messages, tools=tools_enabled, root_strs=root_strs
                ),
            }
        ] + messages

    required_tools = _required_tools_for_text(_last_user_text(messages)) if tools_enabled else frozenset()
    used_tools: set[str] = set()
    tool_nudge_sent = False
    turn_pending_ids: List[str] = []
    open_batch: Optional[wb.WriteBatch] = None
    batch_notes: List[Dict[str, Any]] = []
    flush_out: Dict[str, Any] = {}

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            for _round in range(MAX_ROUNDS):
                if cancel is not None and cancel.is_set():
                    cancel_turn_pendings(turn_pending_ids)
                    yield _sse({"type": "cancelled", "message": "stopped by user"})
                    return
                if batch_notes:
                    messages.extend(batch_notes)
                    batch_notes.clear()

                if messages and (messages[-1].get("role") == "user"):
                    _maybe_collapse_explore(messages)

                buffer_tokens = bool(required_tools - used_tools)
                assembled_message: Optional[Dict[str, Any]] = None
                try:
                    async for ev in _ollama_chat_stream(
                        client,
                        ollama_url,
                        model=model,
                        messages=messages,
                        tools=ft.TOOL_SCHEMAS if tools_enabled else None,
                        cancel=cancel,
                        emit_tokens=not buffer_tokens,
                    ):
                        if ev.get("type") == "ollama_message":
                            assembled_message = ev.get("message") or {}
                        elif ev.get("type") in ("token", "thinking"):
                            yield _sse(ev)
                except ChatCancelled:
                    cancel_turn_pendings(turn_pending_ids)
                    yield _sse({"type": "cancelled", "message": "stopped by user"})
                    return
                except Exception as e:
                    err = _exc_message(e, fallback="ollama stream failed")
                    if tools_enabled and (
                        "tools" in err.lower() or "does not support" in err.lower()
                    ):
                        tools_enabled = False
                        continue
                    yield _sse({"type": "error", "message": err})
                    return

                if assembled_message is None:
                    yield _sse({"type": "error", "message": "ollama returned no message"})
                    return

                message = assembled_message
                content = message.get("content") or ""
                calls = _tool_call_parts(message) if tools_enabled else []

                if not calls:
                    missing = required_tools - used_tools
                    if missing and not tool_nudge_sent:
                        # Discard buffered hallucination — do not yield token events.
                        tool_nudge_sent = True
                        need = ", ".join(sorted(missing))
                        messages.append({"role": "assistant", "content": content or ""})
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    f"(system) You must call {need} before answering. "
                                    "Do not invent live data from memory. Call the tool now."
                                ),
                            }
                        )
                        continue
                    # Final answer: if we buffered this round, emit content once now;
                    # if we streamed live, tokens already went out — only emit done.
                    if buffer_tokens and content:
                        yield _sse({"type": "token", "content": content})
                    if open_batch is not None and open_batch.items:
                        async for _fev in _flush_write_batch(
                            open_batch, roots, root_strs, turn_pending_ids,
                            cancel, flush_out,
                        ):
                            yield _fev
                        open_batch = None
                        if flush_out.get("cancelled"):
                            cancel_turn_pendings(turn_pending_ids)
                            yield _sse({"type": "cancelled", "message": "stopped by user"})
                            return
                    yield _sse({"type": "done", "message": content})
                    return

                # Keep assistant tool-call message in history for Ollama.
                messages.append(message)

                for call in calls:
                    if cancel is not None and cancel.is_set():
                        cancel_turn_pendings(turn_pending_ids)
                        yield _sse({"type": "cancelled", "message": "stopped by user"})
                        return

                    name = call["name"]
                    args = call["args"]
                    call_id = call["id"]
                    used_tools.add(name)
                    yield _sse(
                        {"type": "tool_start", "id": call_id, "name": name, "args": args}
                    )

                    # Pre-check paths; may need workspace grant.
                    while True:
                        try:
                            # Dry-run resolve for path args
                            if name in ft.WRITE_TOOLS:
                                ft.preview_write(name, args, roots)
                            else:
                                # Touch resolve via a no-op friendly path
                                hint = ft.path_from_tool_args(name, args)
                                if hint:
                                    if name == "search_files":
                                        ft.resolve_in_workspace(hint, roots)
                                    elif name == "list_dir":
                                        ft.resolve_in_workspace(hint, roots)
                                    elif name == "read_file":
                                        ft.resolve_in_workspace(hint, roots, must_exist=False)
                            break
                        except ft.FsError as e:
                            if e.message.startswith("outside_workspace:"):
                                hint = ft.path_from_tool_args(name, args) or ""
                                suggested = ft.suggested_root_for_path(hint)
                                pid, fut = _new_pending("grant", root_strs)
                                turn_pending_ids.append(pid)
                                yield _sse(
                                    {
                                        "type": "workspace_grant_needed",
                                        "id": pid,
                                        "path": hint,
                                        "suggested_root": suggested,
                                        "reason": e.message,
                                    }
                                )
                                decision = await _await_user(fut, cancel=cancel)
                                if decision.get("reason") == "cancelled" or (
                                    cancel is not None and cancel.is_set()
                                ):
                                    cancel_turn_pendings(turn_pending_ids)
                                    yield _sse({"type": "cancelled", "message": "stopped by user"})
                                    return
                                if decision.get("deny"):
                                    result = f"error: user denied workspace grant ({e.message})"
                                    yield _sse(
                                        {
                                            "type": "tool_result",
                                            "id": call_id,
                                            "name": name,
                                            "ok": False,
                                            "content": result,
                                        }
                                    )
                                    messages.append(
                                        {
                                            "role": "tool",
                                            "name": name,
                                            "content": result,
                                        }
                                    )
                                    # skip execute
                                    name = ""  # mark skipped
                                    break
                                if decision.get("workspace_roots"):
                                    raw_roots = list(decision["workspace_roots"])
                                elif decision.get("root"):
                                    raw_roots = list(dict.fromkeys(raw_roots + [decision["root"]]))
                                try:
                                    roots = ft.normalize_roots(raw_roots)
                                    root_strs = [str(r) for r in roots]
                                except ft.FsError as e2:
                                    result = f"error: {e2.message}"
                                    yield _sse(
                                        {
                                            "type": "tool_result",
                                            "id": call_id,
                                            "name": name,
                                            "ok": False,
                                            "content": result,
                                        }
                                    )
                                    messages.append(
                                        {
                                            "role": "tool",
                                            "name": name,
                                            "content": result,
                                        }
                                    )
                                    name = ""
                                    break
                                continue
                            # Non-grant errors: fall through to run_tool which will raise same
                            break

                    if not name:
                        continue

                    # Write approval gate (includes edit_file when apply=true)
                    needs_write_gate = name in ft.WRITE_TOOLS and not writes_armed
                    if name == "edit_file" and not bool(args.get("apply")):
                        needs_write_gate = False  # preview-only call
                    if needs_write_gate:
                        # Queue (no disk I/O); the user reviews the whole batch at flush.
                        try:
                            preview = ft.preview_write(name, args, roots)
                        except ft.FsError as e:
                            preview = None
                            result = f"error: {e.message}"
                        except Exception as e:
                            preview = None
                            result = f"error: {e}"
                        if preview is None:
                            yield _sse(
                                {
                                    "type": "tool_result",
                                    "id": call_id,
                                    "name": name,
                                    "ok": False,
                                    "content": result,
                                }
                            )
                            messages.append(
                                {"role": "tool", "name": name, "content": result}
                            )
                            continue
                        if (
                            open_batch is not None
                            and len(open_batch.items) >= wb.MAX_WRITE_BATCH
                        ):
                            async for _fev in _flush_write_batch(
                                open_batch, roots, root_strs, turn_pending_ids,
                                cancel, flush_out,
                            ):
                                yield _fev
                            open_batch = None
                            if flush_out.get("cancelled"):
                                cancel_turn_pendings(turn_pending_ids)
                                yield _sse({"type": "cancelled", "message": "stopped by user"})
                                return
                            if flush_out.get("note"):
                                batch_notes.append(flush_out["note"])
                        if open_batch is None:
                            open_batch = wb.new_batch()
                        try:
                            item = wb.try_enqueue(
                                open_batch, tool=name, args=args, preview=preview
                            )
                        except ValueError as e:
                            result = f"error: {e}"
                            yield _sse(
                                {
                                    "type": "tool_result",
                                    "id": call_id,
                                    "name": name,
                                    "ok": False,
                                    "content": result,
                                }
                            )
                            messages.append(
                                {"role": "tool", "name": name, "content": result}
                            )
                            continue
                        yield _sse(
                            {
                                "type": "write_queued",
                                "batch_id": open_batch.batch_id,
                                **_batch_item_event(item),
                            }
                        )
                        result = wb.queued_tool_content(item.path)
                        yield _sse(
                            {
                                "type": "tool_result",
                                "id": call_id,
                                "name": name,
                                "ok": True,
                                "content": result,
                            }
                        )
                        messages.append({"role": "tool", "name": name, "content": result})
                        continue

                    # Shell approval gate — confirm unless run_everything.
                    if name == "shell_run":
                        if open_batch is not None and open_batch.items:
                            async for _fev in _flush_write_batch(
                                open_batch, roots, root_strs, turn_pending_ids,
                                cancel, flush_out,
                            ):
                                yield _fev
                            open_batch = None
                            if flush_out.get("cancelled"):
                                cancel_turn_pendings(turn_pending_ids)
                                yield _sse({"type": "cancelled", "message": "stopped by user"})
                                return
                            if flush_out.get("note"):
                                batch_notes.append(flush_out["note"])
                        command = (args.get("command") or "").strip()
                        try:
                            cwd = ft.resolve_tool_cwd(args.get("cwd"), roots)
                        except ft.FsError as e:
                            result = f"error: {e.message}"
                            yield _sse({"type": "tool_result", "id": call_id, "name": name, "ok": False, "content": result})
                            messages.append({"role": "tool", "name": name, "content": result})
                            continue
                        if not command:
                            result = "error: missing 'command' argument"
                            yield _sse({"type": "tool_result", "id": call_id, "name": name, "ok": False, "content": result})
                            messages.append({"role": "tool", "name": name, "content": result})
                            continue
                        preview = ft._shell_preview(command, cwd)
                        pid, fut = _new_pending("shell", root_strs, command=command, cwd=cwd)
                        turn_pending_ids.append(pid)
                        if run_everything:
                            # Resolve on a worker thread; await fut so call_soon_threadsafe lands.
                            await asyncio.to_thread(resolve_shell_decision, pid, True)
                            decision = await fut
                        else:
                            yield _sse(
                                {
                                    "type": "shell_pending",
                                    "id": pid,
                                    "tool": name,
                                    "args": args,
                                    "preview": preview,
                                }
                            )
                            decision = await _await_user(fut, cancel=cancel)
                        if decision.get("reason") == "cancelled" or (
                            cancel is not None and cancel.is_set()
                        ):
                            cancel_turn_pendings(turn_pending_ids)
                            yield _sse({"type": "cancelled", "message": "stopped by user"})
                            return
                        if decision.get("deny"):
                            result = "error: user denied shell_run"
                            yield _sse({"type": "tool_result", "id": call_id, "name": name, "ok": False, "content": result})
                            messages.append({"role": "tool", "name": name, "content": result})
                            continue
                        if decision.get("approve"):
                            result = decision.get("output", "error: no output captured")
                            ok = True
                            try:
                                parsed = json.loads(result)
                                if isinstance(parsed, dict) and parsed.get("error"):
                                    ok = False
                            except Exception:
                                pass
                            if _shell_output_cancelled(result):
                                cancel_turn_pendings(turn_pending_ids)
                                yield _sse({"type": "cancelled", "message": "stopped by user"})
                                return
                        else:
                            result = "error: shell_run approval did not return output"
                            ok = False
                        yield _sse({"type": "tool_result", "id": call_id, "name": name, "ok": ok, "content": result})
                        messages.append({"role": "tool", "name": name, "content": result})
                        continue

                    try:
                        result = ft.run_tool(name, args, roots)
                        ok = True
                    except ft.FsError as e:
                        result = f"error: {e.message}"
                        ok = False
                    except Exception as e:
                        result = f"error: {e}"
                        ok = False

                    yield _sse(
                        {
                            "type": "tool_result",
                            "id": call_id,
                            "name": name,
                            "ok": ok,
                            "content": result,
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "name": name,
                            "content": result,
                        }
                    )
                    if name in cr.EXPLORE_TOOLS and not cr.is_explore_error(result):
                        _maybe_collapse_explore(messages)

            if open_batch is not None and open_batch.items:
                async for _fev in _flush_write_batch(
                    open_batch, roots, root_strs, turn_pending_ids,
                    cancel, flush_out,
                ):
                    yield _fev
                open_batch = None
                if flush_out.get("cancelled"):
                    cancel_turn_pendings(turn_pending_ids)
                    yield _sse({"type": "cancelled", "message": "stopped by user"})
                    return
            yield _sse({"type": "error", "message": f"stopped after {MAX_ROUNDS} tool rounds"})
    finally:
        cancel_turn_pendings(turn_pending_ids)

async def _iter_jev_chat_events(
    body: Dict[str, Any],
    ollama_url: str,
    *,
    cancel: Optional[asyncio.Event] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """Jev picker loop with streaming Ollama reply / single-tool arg-fill."""
    messages: List[Dict[str, Any]] = _normalize_messages_images(
        list(body.get("messages") or [])
    )
    model = body.get("model") or "mistral-nemo"
    rej = _reject_images_if_unsupported(model, messages)
    if rej:
        yield _sse(rej)
        return
    run_everything, writes_armed = _run_flags(body)
    raw_roots = list(body.get("workspace_roots") or [])
    tools_mode = str(body.get("tools_mode") or "always")

    if not messages:
        yield _sse({"type": "error", "message": "messages required"})
        return

    if raw_roots:
        try:
            roots = ft.normalize_roots(raw_roots)
            root_strs = [str(r) for r in roots]
        except ft.FsError as e:
            yield _sse({"type": "error", "message": e.message})
            return
    else:
        roots = []
        root_strs = []

    has_system = any(m.get("role") == "system" for m in messages)
    if not has_system:
        messages = [
            {
                "role": "system",
                "content": _system_prompt_for_turn(
                    messages, tools=True, root_strs=root_strs
                ),
            }
        ] + messages

    force_reply = False
    turn_pending_ids: List[str] = []
    state = pr.run_state_from_body(body)
    _round = 0
    open_batch: Optional[wb.WriteBatch] = None
    batch_notes: List[Dict[str, Any]] = []
    flush_out: Dict[str, Any] = {}
    fail_tracker: Dict[str, Any] = {"sig": None, "count": 0}
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            while _round < pr.max_rounds_for(state):
                _round += 1
                if cancel is not None and cancel.is_set():
                    for _cev in _jev_cancel_events(state, turn_pending_ids):
                        yield _cev
                    return
                if batch_notes:
                    # Flush results queued mid tool-call; append only between rounds
                    # so they never split an assistant tool_call from its tool result.
                    messages.extend(batch_notes)
                    batch_notes.clear()

                if messages and (messages[-1].get("role") == "user"):
                    _maybe_collapse_explore(messages)

                option = jp.REPLY_KEY
                confidence = 1.0
                selected_schema = None

                if force_reply:
                    # Successful write/edit/etc. already happened — follow up in chat.
                    force_reply = False
                    yield _sse(
                        {
                            "type": "decision_pick",
                            "option": jp.REPLY_KEY,
                            "confidence": 1.0,
                        }
                    )
                else:
                    try:
                        option, confidence, backend = await jp.pick_next_action(
                            client,
                            messages=messages,
                            roots=root_strs,
                            writes_armed=writes_armed,
                            mode=state.mode,
                            plan_approved=state.plan_approved,
                            todos=state.todos,
                            cancel=cancel,
                        )
                    except jp.JevPickerError as e:
                        if e.kind == jp.JEV_KIND_CANCELLED:
                            for _cev in _jev_cancel_events(state, turn_pending_ids):
                                yield _cev
                        else:
                            yield _sse({"type": "error", "message": e.message})
                        return

                    yield _sse(
                        {
                            "type": "decision_pick",
                            "option": option,
                            "confidence": confidence,
                            "backend": backend,
                        }
                    )

                    # Plan hold: block mutating tools before any permission card / Ollama fill.
                    if option in pr.HELD_MUTATING and pr.mutating_held(state):
                        hold_id = f"hold_{uuid.uuid4().hex[:8]}"
                        hold_msg = (
                            "error: Plan not approved yet — mutating tools are blocked. "
                            "Use ask_clarify or propose_plan, then wait for the user to Approve."
                        )
                        yield _sse(
                            {"type": "tool_start", "id": hold_id, "name": option, "args": {}}
                        )
                        yield _sse(
                            {
                                "type": "tool_result",
                                "id": hold_id,
                                "name": option,
                                "ok": False,
                                "content": hold_msg,
                            }
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    f"(system) {hold_msg} Choose another action "
                                    "(ask_clarify, propose_plan, or reply)."
                                ),
                            }
                        )
                        continue

                    if option == jp.ASK_CLARIFY_KEY:
                        # v1: streams like reply (no tools) and ends the turn.
                        pr.apply_clarify(state)
                    elif option in jp.SENSITIVE_PRE_OLLAMA and not run_everything:
                        pid, fut = _new_pending(
                            "tool_permission", root_strs, tool=option
                        )
                        turn_pending_ids.append(pid)
                        yield _sse(
                            {
                                "type": "tool_permission_pending",
                                "id": pid,
                                "tool": option,
                            }
                        )
                        decision = await _await_user(fut, cancel=cancel)
                        if decision.get("reason") == "cancelled" or (
                            cancel is not None and cancel.is_set()
                        ):
                            for _cev in _jev_cancel_events(state, turn_pending_ids):
                                yield _cev
                            return
                        if not decision.get("approve"):
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        f"(system) User denied permission to call {option}. "
                                        "Reply to the user now without calling tools."
                                    ),
                                }
                            )
                            force_reply = True
                            continue

                    if option in (jp.PROPOSE_PLAN_KEY, jp.UPDATE_TODO_KEY):
                        if option == jp.UPDATE_TODO_KEY and state.mode != "implementing":
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "(system) update_todo is only available while "
                                        "implementing an approved plan. Choose another action."
                                    ),
                                }
                            )
                            continue
                        selected_schema = pr.schema_for_control(option)
                        if option == jp.PROPOSE_PLAN_KEY:
                            # Hold mutating tools before the fill so a failed
                            # fill cannot leave writes unblocked.
                            state.mode = "planning"
                            state.plan_approved = False
                    elif option not in (jp.REPLY_KEY, jp.ASK_CLARIFY_KEY):
                        selected_schema = _schema_for_tool(option)
                        if selected_schema is None:
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        f"(system) Tool schema unavailable for {option}. "
                                        "Choose another action."
                                    ),
                                }
                            )
                            continue

                # Turn is about to reply / end: review queued writes first.
                if (
                    option in (jp.REPLY_KEY, jp.ASK_CLARIFY_KEY)
                    and open_batch is not None
                    and open_batch.items
                ):
                    async for _fev in _flush_write_batch(
                        open_batch, roots, root_strs, turn_pending_ids,
                        cancel, flush_out, state,
                    ):
                        yield _fev
                    open_batch = None
                    if flush_out.get("cancelled"):
                        for _cev in _jev_cancel_events(state, turn_pending_ids):
                            yield _cev
                        return
                    if flush_out.get("note"):
                        messages.append(flush_out["note"])

                is_control_fill = option in (jp.PROPOSE_PLAN_KEY, jp.UPDATE_TODO_KEY)
                ollama_messages = messages
                extra_system: List[Dict[str, Any]] = []
                note = _mode_system_note(state)
                if note:
                    extra_system.append({"role": "system", "content": note})
                if option == jp.ASK_CLARIFY_KEY:
                    extra_system.append(
                        {
                            "role": "system",
                            "content": (
                                "Ask the user one or two short clarifying questions "
                                "about scope or acceptance criteria. End with a '?'. "
                                "Do not narrate next steps or say you will start work. "
                                "Do not call tools or make changes."
                            ),
                        }
                    )
                if selected_schema is not None:
                    extra_system.append(
                        {"role": "system", "content": _force_tool_system(option)}
                    )
                if extra_system:
                    ollama_messages = messages + extra_system

                # Plain reply / clarify: stream with empty-content retry so
                # thinking-only models don't end the turn on a blank bubble.
                if selected_schema is None:
                    try:
                        final_text = ""
                        async for ev in _stream_user_text_reply(
                            client,
                            ollama_url,
                            model=model,
                            messages=ollama_messages,
                            cancel=cancel,
                        ):
                            if ev.get("type") == "final_text":
                                final_text = ev.get("content") or ""
                            else:
                                yield _sse(ev)
                    except ChatCancelled:
                        for _cev in _jev_cancel_events(state, turn_pending_ids):
                            yield _cev
                        return
                    except Exception as e:
                        yield _sse(
                            {
                                "type": "error",
                                "message": _exc_message(
                                    e, fallback="reply stream failed"
                                ),
                            }
                        )
                        return
                    # Status narration mis-tagged as ask_clarify: keep the turn open.
                    if option == jp.ASK_CLARIFY_KEY and not _is_clarifying_question(
                        final_text
                    ):
                        messages.append(
                            {"role": "assistant", "content": final_text or ""}
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "(system) That ask_clarify text was not a question "
                                    "for the user. Continue the task with the next tool "
                                    "(do not ask_clarify or reply until the work is done "
                                    "or you are blocked)."
                                ),
                            }
                        )
                        yield _sse(
                            {
                                "type": "segment_done",
                                "message": final_text or "",
                            }
                        )
                        continue
                    yield _sse({"type": "done", "message": final_text})
                    return

                assembled_message: Optional[Dict[str, Any]] = None
                try:
                    async for ev in _ollama_chat_stream(
                        client,
                        ollama_url,
                        model=model,
                        messages=ollama_messages,
                        tools=[selected_schema],
                        cancel=cancel,
                        emit_tokens=not is_control_fill,
                    ):
                        if ev.get("type") == "ollama_message":
                            assembled_message = ev.get("message") or {}
                        elif ev.get("type") in ("token", "thinking"):
                            yield _sse(ev)
                except ChatCancelled:
                    for _cev in _jev_cancel_events(state, turn_pending_ids):
                        yield _cev
                    return
                except Exception as e:
                    yield _sse(
                        {
                            "type": "error",
                            "message": _exc_message(
                                e, fallback="tool fill stream failed"
                            ),
                        }
                    )
                    return

                if assembled_message is None:
                    yield _sse({"type": "error", "message": "ollama returned no message"})
                    return

                message = assembled_message
                content = message.get("content") or ""
                calls = _tool_call_parts(message)

                if not calls:
                    messages.append({"role": "assistant", "content": content or ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"(system) Expected a {option} tool call, but none was "
                                "returned. Choose the next action."
                            ),
                        }
                    )
                    continue

                if len(calls) != 1 or calls[0]["name"] != option:
                    messages.append(message)
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"(system) Expected exactly one {option} tool call. "
                                "Choose the next action."
                            ),
                        }
                    )
                    continue

                # Keep assistant tool-call message in history for Ollama.
                messages.append(message)

                if option == jp.PROPOSE_PLAN_KEY:
                    plan_args = calls[0]["args"]
                    summary = str(plan_args.get("summary") or "").strip()
                    plan_todos = _coerce_todos(plan_args.get("todos"))
                    pr.apply_plan_proposed(state, summary, plan_todos)
                    messages.append(
                        {
                            "role": "tool",
                            "name": jp.PROPOSE_PLAN_KEY,
                            "content": "Plan proposed; awaiting user Approve or Revise.",
                        }
                    )
                    pid, fut = _new_pending(
                        "plan", root_strs, summary=state.plan_summary
                    )
                    turn_pending_ids.append(pid)
                    yield _sse(
                        {
                            "type": "plan_pending",
                            "id": pid,
                            "summary": state.plan_summary,
                            "todos": [dict(t) for t in state.todos],
                        }
                    )
                    decision = await _await_user(fut, cancel=cancel)
                    if decision.get("reason") == "cancelled" or (
                        cancel is not None and cancel.is_set()
                    ):
                        yield _sse(
                            {"type": "plan_decision", "id": pid, "action": "cancelled"}
                        )
                        for _cev in _jev_cancel_events(state, turn_pending_ids):
                            yield _cev
                        return
                    action = decision.get("action")
                    if action == "approve":
                        pr.apply_plan_approved(state)
                        yield _sse(
                            {"type": "plan_decision", "id": pid, "action": "approve"}
                        )
                        yield _sse(
                            {"type": "todos", "todos": [dict(t) for t in state.todos]}
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "(system) The user approved the plan. Implement it "
                                    "now, step by step."
                                ),
                            }
                        )
                    elif action == "revise":
                        pr.apply_plan_revise(state)
                        yield _sse(
                            {"type": "plan_decision", "id": pid, "action": "revise"}
                        )
                        note_text = str(decision.get("note") or "").strip()
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "(system) User requested plan revise"
                                    + (f": {note_text}" if note_text else ".")
                                    + " Clarify or propose a revised plan; do not "
                                    "modify files yet."
                                ),
                            }
                        )
                    else:
                        # Timeout or other deny: no implement.
                        yield _sse(
                            {"type": "plan_decision", "id": pid, "action": "cancelled"}
                        )
                        yield _sse(
                            {"type": "error", "message": "plan approval timed out"}
                        )
                        return
                    continue

                if option == jp.UPDATE_TODO_KEY:
                    pr.apply_todo_update(
                        state, _coerce_todos(calls[0]["args"].get("todos"))
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "name": jp.UPDATE_TODO_KEY,
                            "content": "Todos updated.",
                        }
                    )
                    yield _sse(
                        {"type": "todos", "todos": [dict(t) for t in state.todos]}
                    )
                    continue

                for call in calls:
                    name = call["name"]
                    args = call["args"]
                    call_id = call["id"]
                    yield _sse(
                        {"type": "tool_start", "id": call_id, "name": name, "args": args}
                    )

                    # Pre-check paths; may need workspace grant.
                    while True:
                        try:
                            # Dry-run resolve for path args
                            if name in ft.WRITE_TOOLS:
                                ft.preview_write(name, args, roots)
                            else:
                                # Touch resolve via a no-op friendly path
                                hint = ft.path_from_tool_args(name, args)
                                if hint:
                                    if name == "search_files":
                                        ft.resolve_in_workspace(hint, roots)
                                    elif name == "list_dir":
                                        ft.resolve_in_workspace(hint, roots)
                                    elif name == "read_file":
                                        ft.resolve_in_workspace(hint, roots, must_exist=False)
                            break
                        except ft.FsError as e:
                            if e.message.startswith("outside_workspace:"):
                                hint = ft.path_from_tool_args(name, args) or ""
                                suggested = ft.suggested_root_for_path(hint)
                                pid, fut = _new_pending("grant", root_strs)
                                turn_pending_ids.append(pid)
                                yield _sse(
                                    {
                                        "type": "workspace_grant_needed",
                                        "id": pid,
                                        "path": hint,
                                        "suggested_root": suggested,
                                        "reason": e.message,
                                    }
                                )
                                decision = await _await_user(fut, cancel=cancel)
                                if decision.get("reason") == "cancelled" or (
                                    cancel is not None and cancel.is_set()
                                ):
                                    for _cev in _jev_cancel_events(state, turn_pending_ids):
                                        yield _cev
                                    return
                                if decision.get("deny"):
                                    result = f"error: user denied workspace grant ({e.message})"
                                    yield _sse(
                                        {
                                            "type": "tool_result",
                                            "id": call_id,
                                            "name": name,
                                            "ok": False,
                                            "content": result,
                                        }
                                    )
                                    messages.append(
                                        {
                                            "role": "tool",
                                            "name": name,
                                            "content": result,
                                        }
                                    )
                                    # skip execute
                                    name = ""  # mark skipped
                                    break
                                if decision.get("workspace_roots"):
                                    raw_roots = list(decision["workspace_roots"])
                                elif decision.get("root"):
                                    raw_roots = list(dict.fromkeys(raw_roots + [decision["root"]]))
                                try:
                                    roots = ft.normalize_roots(raw_roots)
                                    root_strs = [str(r) for r in roots]
                                except ft.FsError as e2:
                                    result = f"error: {e2.message}"
                                    yield _sse(
                                        {
                                            "type": "tool_result",
                                            "id": call_id,
                                            "name": name,
                                            "ok": False,
                                            "content": result,
                                        }
                                    )
                                    messages.append(
                                        {
                                            "role": "tool",
                                            "name": name,
                                            "content": result,
                                        }
                                    )
                                    name = ""
                                    break
                                continue
                            # Non-grant errors: fall through to run_tool which will raise same
                            break

                    if not name:
                        continue

                    # Write approval gate (includes edit_file when apply=true)
                    needs_write_gate = name in ft.WRITE_TOOLS and not writes_armed
                    if name == "edit_file" and not bool(args.get("apply")):
                        needs_write_gate = False  # preview-only call
                    if needs_write_gate:
                        # Queue (no disk I/O); the user reviews the whole batch at flush.
                        try:
                            preview = ft.preview_write(name, args, roots)
                        except ft.FsError as e:
                            preview = None
                            result = f"error: {e.message}"
                        except Exception as e:
                            preview = None
                            result = f"error: {e}"
                        if preview is None:
                            yield _sse(
                                {
                                    "type": "tool_result",
                                    "id": call_id,
                                    "name": name,
                                    "ok": False,
                                    "content": result,
                                }
                            )
                            messages.append(
                                {"role": "tool", "name": name, "content": result}
                            )
                            continue
                        if (
                            open_batch is not None
                            and len(open_batch.items) >= wb.MAX_WRITE_BATCH
                        ):
                            flush_out: Dict[str, Any] = {}
                            async for _fev in _flush_write_batch(
                                open_batch, roots, root_strs, turn_pending_ids,
                                cancel, flush_out, state,
                            ):
                                yield _fev
                            open_batch = None
                            if flush_out.get("cancelled"):
                                for _cev in _jev_cancel_events(state, turn_pending_ids):
                                    yield _cev
                                return
                            if flush_out.get("note"):
                                batch_notes.append(flush_out["note"])
                            if flush_out.get("force_reply"):
                                force_reply = True
                        if open_batch is None:
                            open_batch = wb.new_batch()
                        try:
                            item = wb.try_enqueue(
                                open_batch, tool=name, args=args, preview=preview
                            )
                        except ValueError as e:
                            result = f"error: {e}"
                            yield _sse(
                                {
                                    "type": "tool_result",
                                    "id": call_id,
                                    "name": name,
                                    "ok": False,
                                    "content": result,
                                }
                            )
                            messages.append(
                                {"role": "tool", "name": name, "content": result}
                            )
                            continue
                        yield _sse(
                            {
                                "type": "write_queued",
                                "batch_id": open_batch.batch_id,
                                **_batch_item_event(item),
                            }
                        )
                        result = wb.queued_tool_content(item.path)
                        yield _sse(
                            {
                                "type": "tool_result",
                                "id": call_id,
                                "name": name,
                                "ok": True,
                                "content": result,
                            }
                        )
                        messages.append({"role": "tool", "name": name, "content": result})
                        # Queued ≠ mutation: no force_reply until the batch is applied.
                        continue

                    # Shell approval gate — confirm unless run_everything.
                    if name == "shell_run":
                        if open_batch is not None and open_batch.items:
                            flush_out = {}
                            async for _fev in _flush_write_batch(
                                open_batch, roots, root_strs, turn_pending_ids,
                                cancel, flush_out, state,
                            ):
                                yield _fev
                            open_batch = None
                            if flush_out.get("cancelled"):
                                for _cev in _jev_cancel_events(state, turn_pending_ids):
                                    yield _cev
                                return
                            if flush_out.get("note"):
                                batch_notes.append(flush_out["note"])
                            if flush_out.get("force_reply"):
                                force_reply = True
                        command = (args.get("command") or "").strip()
                        try:
                            cwd = ft.resolve_tool_cwd(args.get("cwd"), roots)
                        except ft.FsError as e:
                            result = f"error: {e.message}"
                            yield _sse({"type": "tool_result", "id": call_id, "name": name, "ok": False, "content": result})
                            messages.append({"role": "tool", "name": name, "content": result})
                            continue
                        if not command:
                            result = "error: missing 'command' argument"
                            yield _sse({"type": "tool_result", "id": call_id, "name": name, "ok": False, "content": result})
                            messages.append({"role": "tool", "name": name, "content": result})
                            continue
                        preview = ft._shell_preview(command, cwd)
                        pid, fut = _new_pending("shell", root_strs, command=command, cwd=cwd)
                        turn_pending_ids.append(pid)
                        if run_everything:
                            # Resolve on a worker thread; await fut so call_soon_threadsafe lands.
                            await asyncio.to_thread(resolve_shell_decision, pid, True)
                            decision = await fut
                        else:
                            yield _sse(
                                {
                                    "type": "shell_pending",
                                    "id": pid,
                                    "tool": name,
                                    "args": args,
                                    "preview": preview,
                                }
                            )
                            decision = await _await_user(fut, cancel=cancel)
                        if decision.get("reason") == "cancelled" or (
                            cancel is not None and cancel.is_set()
                        ):
                            for _cev in _jev_cancel_events(state, turn_pending_ids):
                                yield _cev
                            return
                        if decision.get("deny"):
                            result = "error: user denied shell_run"
                            yield _sse({"type": "tool_result", "id": call_id, "name": name, "ok": False, "content": result})
                            messages.append({"role": "tool", "name": name, "content": result})
                            continue
                        if decision.get("approve"):
                            result = decision.get("output", "error: no output captured")
                            ok = True
                            try:
                                parsed = json.loads(result)
                                if isinstance(parsed, dict) and parsed.get("error"):
                                    ok = False
                            except Exception:
                                pass
                            if _shell_output_cancelled(result):
                                for _cev in _jev_cancel_events(state, turn_pending_ids):
                                    yield _cev
                                return
                        else:
                            result = "error: shell_run approval did not return output"
                            ok = False
                        yield _sse({"type": "tool_result", "id": call_id, "name": name, "ok": ok, "content": result})
                        messages.append({"role": "tool", "name": name, "content": result})
                        continue

                    try:
                        result = ft.run_tool(name, args, roots)
                        ok = True
                    except ft.FsError as e:
                        result = f"error: {e.message}"
                        ok = False
                    except Exception as e:
                        result = f"error: {_exc_message(e)}"
                        ok = False

                    yield _sse(
                        {
                            "type": "tool_result",
                            "id": call_id,
                            "name": name,
                            "ok": ok,
                            "content": result,
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "name": name,
                            "content": result,
                        }
                    )
                    if name in cr.EXPLORE_TOOLS and not cr.is_explore_error(result):
                        _maybe_collapse_explore(messages)
                    note = _note_repeated_tool_failure(
                        fail_tracker,
                        name=name,
                        args=args if isinstance(args, dict) else {},
                        ok=ok,
                        result=result,
                    )
                    if note:
                        messages.append(note)
                    if _force_reply_after_tool_for_state(name, args, ok, state):
                        force_reply = True

            if open_batch is not None and open_batch.items:
                # Round budget exhausted with writes still queued: let the user decide.
                async for _fev in _flush_write_batch(
                    open_batch, roots, root_strs, turn_pending_ids,
                    cancel, flush_out, state,
                ):
                    yield _fev
                open_batch = None
                if flush_out.get("cancelled"):
                    for _cev in _jev_cancel_events(state, turn_pending_ids):
                        yield _cev
                    return
            yield _sse(
                {
                    "type": "error",
                    "message": f"stopped after {pr.max_rounds_for(state)} tool rounds",
                }
            )
    finally:
        cancel_turn_pendings(turn_pending_ids)
