from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_WRITE_BATCH = 20


@dataclass
class BatchItem:
    item_id: str
    tool: str
    args: Dict[str, Any]
    path: str
    preview: str


@dataclass
class WriteBatch:
    batch_id: str
    items: List[BatchItem] = field(default_factory=list)


def new_batch() -> WriteBatch:
    return WriteBatch(batch_id=str(uuid.uuid4()))


def display_path(tool: str, args: Dict[str, Any]) -> str:
    if tool == "move_path":
        return f"{args.get('src', '')} -> {args.get('dst', '')}"
    return str(args.get("path") or args.get("dst") or tool)


def queued_tool_content(path: str) -> str:
    return json.dumps(
        {
            "status": "queued",
            "path": path,
            "hint": "not applied; awaiting batch approval",
        }
    )


def try_enqueue(
    batch: WriteBatch,
    *,
    tool: str,
    args: Dict[str, Any],
    preview: str,
    item_id: Optional[str] = None,
) -> BatchItem:
    if len(batch.items) >= MAX_WRITE_BATCH:
        raise ValueError("batch full")
    path = display_path(tool, args or {})
    for it in batch.items:
        if it.path == path:
            raise ValueError("previous edit for path still pending in batch")
    item = BatchItem(
        item_id=item_id or str(uuid.uuid4()),
        tool=tool,
        args=dict(args or {}),
        path=path,
        preview=str(preview or ""),
    )
    batch.items.append(item)
    return item


def item_ids(batch: WriteBatch) -> List[str]:
    return [i.item_id for i in batch.items]


def partition_decision(
    batch: WriteBatch, approved_ids: List[str]
) -> Tuple[List[BatchItem], List[BatchItem]]:
    approved = set(approved_ids or [])
    to_apply = [i for i in batch.items if i.item_id in approved]
    denied = [i for i in batch.items if i.item_id not in approved]
    return to_apply, denied


def apply_items(
    items: List[BatchItem],
    roots: List[Any],
    run_tool: Callable[[str, Dict[str, Any], List[Any]], str],
) -> Tuple[List[str], List[Dict[str, str]]]:
    applied: List[str] = []
    errors: List[Dict[str, str]] = []
    for it in items:
        try:
            run_tool(it.tool, it.args, roots)
            applied.append(it.item_id)
        except Exception as e:
            errors.append({"item_id": it.item_id, "error": str(e)})
    return applied, errors
