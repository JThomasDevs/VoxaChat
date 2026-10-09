from __future__ import annotations

import copy
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHATS_DIR = REPO_ROOT / "data" / "chats"

ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

PENDING_KINDS = frozenset(
    {
        "write_pending",
        "shell_pending",
        "plan_pending",
        "tool_permission_pending",
        "grant_needed",
    }
)

DEFAULT_TITLE = "New chat"
TITLE_MAX = 48


def new_session_id() -> str:
    return str(uuid.uuid4())


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _chats_root(root: Path | None) -> Path:
    return root if root is not None else CHATS_DIR


def _validate_id(chat_id: str) -> None:
    if not ID_RE.match(chat_id):
        raise ValueError(f"invalid chat id: {chat_id!r}")


def _default_resume() -> dict:
    return {
        "mode": "normal",
        "plan_approved": False,
        "plan_summary": "",
        "todos": [],
    }


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def ensure_dir(root: Path | None = None) -> Path:
    d = _chats_root(root)
    d.mkdir(parents=True, exist_ok=True)
    index_path = d / "index.json"
    if not index_path.exists():
        _atomic_write_json(index_path, {"chats": []})
    return d


def _read_index(d: Path) -> dict:
    with open(d / "index.json", encoding="utf-8") as f:
        return json.load(f)


def _write_index(d: Path, index: dict) -> None:
    _atomic_write_json(d / "index.json", index)


def _session_path(d: Path, chat_id: str) -> Path:
    _validate_id(chat_id)
    return d / f"{chat_id}.json"


def _is_default_title(title: str) -> bool:
    return not title or title == DEFAULT_TITLE


def _index_entry(session: dict) -> dict:
    entry = {
        "id": session["id"],
        "title": session.get("title") or DEFAULT_TITLE,
        "updated_at": session["updated_at"],
    }
    model = session.get("model")
    if model:
        entry["model"] = model
    return entry


def title_from_messages(messages: list) -> str:
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content") or ""
        content = " ".join(str(content).split())
        if not content:
            continue
        if len(content) > TITLE_MAX:
            return content[:TITLE_MAX].rstrip() + "…"
        return content
    return DEFAULT_TITLE


def normalize_messages(messages: list) -> list:
    out: list = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        kind = m.get("kind")
        if kind in PENDING_KINDS:
            continue
        if kind == "writes_batch_pending" and m.get("awaitingDecision"):
            continue
        msg = copy.deepcopy(m)
        if kind == "thinking":
            msg["streaming"] = False
            msg["collapsed"] = True
        out.append(msg)
    return out


def list_chats(root: Path | None = None) -> list[dict]:
    d = ensure_dir(root)
    index = _read_index(d)
    chats = list(index.get("chats") or [])
    chats.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    return chats


def create_chat(payload: dict | None = None, *, root: Path | None = None) -> dict:
    d = ensure_dir(root)
    payload = dict(payload or {})
    now = _utc_now()
    chat_id = new_session_id()
    messages = list(payload.get("messages") or [])
    if messages:
        messages = normalize_messages(messages)
    resume = payload.get("resume")
    if resume is None:
        resume = _default_resume()
    session = {
        "id": chat_id,
        "title": payload.get("title") or DEFAULT_TITLE,
        "created_at": now,
        "updated_at": now,
        "model": payload.get("model") or "",
        "workspace_roots": list(payload.get("workspace_roots") or []),
        "resume": resume,
        "messages": messages,
    }
    if "run_everything" in payload:
        session["run_everything"] = bool(payload.get("run_everything"))
    else:
        session["run_everything"] = True
    _atomic_write_json(_session_path(d, chat_id), session)
    index = _read_index(d)
    index.setdefault("chats", []).append(_index_entry(session))
    _write_index(d, index)
    return session


def get_chat(chat_id: str, *, root: Path | None = None) -> dict:
    d = ensure_dir(root)
    _validate_id(chat_id)
    path = d / f"{chat_id}.json"
    if not path.exists():
        raise KeyError(chat_id)
    with open(path, encoding="utf-8") as f:
        session = json.load(f)
    session["messages"] = normalize_messages(session.get("messages") or [])
    if "run_everything" not in session:
        session["run_everything"] = True
    else:
        session["run_everything"] = bool(session.get("run_everything"))
    return session


def put_chat(chat_id: str, session: dict, *, root: Path | None = None) -> dict:
    _validate_id(chat_id)
    d = ensure_dir(root)
    path = _session_path(d, chat_id)
    if not path.exists():
        raise KeyError(chat_id)
    with open(path, encoding="utf-8") as f:
        existing = json.load(f)
    session = dict(session)
    session["id"] = chat_id
    if "run_everything" not in session:
        session["run_everything"] = bool(existing.get("run_everything", True))
    else:
        session["run_everything"] = bool(session.get("run_everything"))
    session["messages"] = normalize_messages(session.get("messages") or [])
    if _is_default_title(session.get("title") or ""):
        derived = title_from_messages(session["messages"])
        if derived != DEFAULT_TITLE:
            session["title"] = derived
    # Clients often omit created_at on autosave; never drop it from disk.
    created = session.get("created_at") or existing.get("created_at")
    if created:
        session["created_at"] = created
    session["updated_at"] = _utc_now()
    _atomic_write_json(path, session)
    index = _read_index(d)
    chats = index.setdefault("chats", [])
    entry = _index_entry(session)
    for i, c in enumerate(chats):
        if c.get("id") == chat_id:
            chats[i] = entry
            break
    else:
        chats.append(entry)
    _write_index(d, index)
    return session


def patch_chat(chat_id: str, patch: dict, *, root: Path | None = None) -> dict:
    session = get_chat(chat_id, root=root)
    patch = dict(patch or {})
    for key in ("title", "model", "workspace_roots", "resume", "messages", "run_everything"):
        if key in patch:
            session[key] = patch[key]
    return put_chat(chat_id, session, root=root)


def delete_chat(chat_id: str, *, root: Path | None = None) -> None:
    _validate_id(chat_id)
    d = ensure_dir(root)
    path = d / f"{chat_id}.json"
    if not path.exists():
        raise KeyError(chat_id)
    path.unlink()
    index = _read_index(d)
    index["chats"] = [
        c for c in (index.get("chats") or []) if c.get("id") != chat_id
    ]
    _write_index(d, index)
