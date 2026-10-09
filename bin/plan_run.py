"""Plan/todo run-state helpers for Voxa chat (clarify → approve → implement)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

MODES = frozenset(
    {"normal", "clarifying", "planning", "awaiting_approval", "implementing"}
)
HELD_MUTATING = frozenset(
    {"write_file", "mkdir", "delete_file", "move_path", "edit_file", "shell_run"}
)
HOLD_MODES = frozenset({"clarifying", "planning", "awaiting_approval"})
# Jev picks one tool per round; multi-step turns burn through these quickly.
MAX_ROUNDS_DEFAULT = 128
MAX_ROUNDS_IMPLEMENT = 256
TODO_STATUSES = frozenset({"pending", "in_progress", "done", "cancelled"})


@dataclass
class RunState:
    mode: str = "normal"
    plan_approved: bool = False
    plan_summary: str = ""
    todos: List[Dict[str, Any]] = field(default_factory=list)


def normalize_todo(item: Dict[str, Any]) -> Dict[str, Any]:
    status = str(item.get("status") or "pending")
    if status not in TODO_STATUSES:
        status = "pending"
    tid = str(item.get("id") or "").strip() or str(uuid.uuid4())
    label = str(item.get("label") or item.get("content") or "").strip() or "Untitled"
    return {"id": tid, "label": label, "status": status}


def normalize_todos(items: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for raw in items or []:
        if isinstance(raw, dict):
            out.append(normalize_todo(raw))
        elif isinstance(raw, str) and raw.strip():
            out.append(normalize_todo({"label": raw.strip()}))
    return out


def run_state_from_body(body: Optional[Dict[str, Any]]) -> RunState:
    if not isinstance(body, dict):
        return RunState()
    mode = str(body.get("mode") or "normal").strip()
    if mode not in MODES:
        return RunState()
    approved_raw = body.get("plan_approved")
    if approved_raw is None and mode == "implementing":
        plan_approved = True
    else:
        plan_approved = bool(approved_raw)
    summary = str(body.get("plan_summary") or "")
    todos = normalize_todos(list(body.get("todos") or []))
    return RunState(
        mode=mode,
        plan_approved=plan_approved,
        plan_summary=summary,
        todos=todos,
    )


def mutating_held(state: RunState) -> bool:
    return state.mode in HOLD_MODES


def should_force_reply_after_tool(state: RunState) -> bool:
    return not (state.mode == "implementing" and state.plan_approved)


def max_rounds_for(state: RunState) -> int:
    if state.mode == "implementing" and state.plan_approved:
        return MAX_ROUNDS_IMPLEMENT
    return MAX_ROUNDS_DEFAULT


def apply_clarify(state: RunState) -> None:
    state.mode = "clarifying"


def apply_plan_proposed(state: RunState, summary: str, todos: List[Any]) -> None:
    state.plan_summary = (summary or "").strip()
    state.todos = normalize_todos(todos)
    state.mode = "awaiting_approval"
    state.plan_approved = False


def apply_plan_approved(state: RunState) -> None:
    state.mode = "implementing"
    state.plan_approved = True


def apply_plan_revise(state: RunState) -> None:
    state.mode = "planning"
    state.plan_approved = False


def apply_todo_update(state: RunState, todos: List[Any]) -> None:
    """Replace or merge by id: incoming items override matching ids; others append."""
    incoming: List[Dict[str, Any]] = []
    for raw in todos or []:
        if isinstance(raw, dict):
            t = normalize_todo(raw)
            raw_label = str(raw.get("label") or raw.get("content") or "").strip()
            raw_status = str(raw.get("status") or "").strip()
            # Remember what the caller actually supplied so partial updates
            # (e.g. {id, status}) do not wipe prior fields.
            t["_has_label"] = bool(raw_label)
            t["_has_status"] = raw_status in TODO_STATUSES
            incoming.append(t)
        elif isinstance(raw, str) and raw.strip():
            t = normalize_todo({"label": raw.strip()})
            t["_has_label"] = True
            t["_has_status"] = False
            incoming.append(t)
    if not incoming:
        return
    by_id = {t["id"]: dict(t) for t in state.todos}
    for t in incoming:
        has_label = t.pop("_has_label")
        has_status = t.pop("_has_status")
        prior = by_id.get(t["id"])
        if prior is not None:
            merged = dict(prior)
            if has_label:
                merged["label"] = t["label"]
            if has_status:
                merged["status"] = t["status"]
            t = merged
        by_id[t["id"]] = t
    ordered: List[Dict[str, Any]] = []
    seen = set()
    for t in state.todos:
        if t["id"] in by_id:
            ordered.append(by_id[t["id"]])
            seen.add(t["id"])
    for t in incoming:
        if t["id"] not in seen:
            ordered.append(t)
            seen.add(t["id"])
    state.todos = ordered


def cancel_in_progress_todos(state: RunState) -> None:
    for t in state.todos:
        if t.get("status") == "in_progress":
            t["status"] = "cancelled"


def ask_clarify_is_text() -> bool:
    """True: ask_clarify uses plain reply stream (no tool schema)."""
    return True


def control_tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "propose_plan",
                "description": "Propose an implementation plan and checklist for user approval.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Short plan the user can approve",
                        },
                        "todos": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": [
                                            "pending",
                                            "in_progress",
                                            "done",
                                            "cancelled",
                                        ],
                                    },
                                },
                                "required": ["label"],
                            },
                        },
                    },
                    "required": ["summary", "todos"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_todo",
                "description": "Update todo checklist items during implementation.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "todos": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "label": {"type": "string"},
                                    "status": {
                                        "type": "string",
                                        "enum": [
                                            "pending",
                                            "in_progress",
                                            "done",
                                            "cancelled",
                                        ],
                                    },
                                },
                                "required": ["id", "status"],
                            },
                        },
                    },
                    "required": ["todos"],
                },
            },
        },
    ]


def schema_for_control(name: str) -> Dict[str, Any]:
    for s in control_tool_schemas():
        if s["function"]["name"] == name:
            return s
    raise KeyError(name)
