"""Resume plan run-state from request body (seeded RunState)."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import chat_agent as ca  # noqa: E402
import jev_picker as jp  # noqa: E402
import plan_run as pr  # noqa: E402


def _tool_msg(name: str, args: Dict[str, Any], call_id: str = "c1") -> Dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": args}}],
    }


def _text_msg(text: str) -> Dict[str, Any]:
    return {"role": "assistant", "content": text}


def _scripted_stream(messages: List[Dict[str, Any]], calls: List[Dict[str, Any]]):
    queue = list(messages)

    async def fake_stream(*args, **kwargs):
        calls.append(kwargs)
        msg = queue.pop(0)
        content = msg.get("content") or ""
        if content and kwargs.get("emit_tokens", True):
            yield {"type": "token", "content": content}
        yield {"type": "ollama_message", "message": msg}

    return fake_stream


class _Responder:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._stop = False

    async def _run(self):
        while not self._stop:
            await asyncio.sleep(0.01)
            for pid, entry in list(ca._PENDING.items()):
                kind = entry.get("kind")
                if kind == "tool_permission":
                    ca.resolve_tool_permission(pid, True)
                elif kind == "write":
                    ca.resolve_write_decision(pid, True)
                elif kind == "writes_batch":
                    ids = [i["item_id"] for i in entry.get("items", [])]
                    ca.resolve_writes_batch_decision(pid, ids)

    def __enter__(self):
        self._task = asyncio.get_event_loop().create_task(self._run())
        return self

    def __exit__(self, *exc):
        self._stop = True
        if self._task:
            self._task.cancel()
        return False


async def _collect(body):
    return [
        ev
        async for ev in ca._iter_jev_chat_events(body, "http://ollama.test", cancel=None)
    ]


def _resume_body(**extra):
    base = {
        "messages": [{"role": "user", "content": "continue the plan"}],
        "model": "m",
        "tools_mode": "always",
        "workspace_roots": [],
        "writes_armed": True,
        "todos": [
            {"id": "t1", "label": "Write a.txt", "status": "in_progress"},
            {"id": "t2", "label": "Write b.txt", "status": "pending"},
        ],
        "plan_summary": "Add two files",
    }
    base.update(extra)
    return base


class PlanResumeFromBodyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ca._PENDING.clear()

    async def test_awaiting_approval_seeded_hold_blocks_mutating(self):
        body = _resume_body(mode="awaiting_approval", plan_approved=False)
        self.assertTrue(pr.mutating_held(pr.run_state_from_body(body)))

        picks = AsyncMock(side_effect=[("write_file", 0.9, "jev"), ("reply", 0.9, "jev")])
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream(
            [_text_msg("Switching to reply.")],
            stream_calls,
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok") as run_tool:
            evs = await _collect(body)

        run_tool.assert_not_called()
        denied = [
            e for e in evs if e["type"] == "tool_result" and e["name"] == "write_file"
        ]
        self.assertEqual(len(denied), 1)
        self.assertFalse(denied[0]["ok"])
        self.assertIn("Plan not approved", denied[0]["content"])
        self.assertEqual(picks.await_args_list[0].kwargs["mode"], "awaiting_approval")
        self.assertFalse(picks.await_args_list[0].kwargs["plan_approved"])
        self.assertEqual(evs[-1]["type"], "done")

    async def test_implementing_seeded_allows_mutating_tool(self):
        body = _resume_body(mode="implementing", plan_approved=True)
        state = pr.run_state_from_body(body)
        self.assertFalse(pr.mutating_held(state))

        picks = AsyncMock(side_effect=[("write_file", 0.9, "jev"), ("reply", 0.9, "jev")])
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream(
            [
                _tool_msg("write_file", {"path": "/tmp/a.txt", "content": "a"}, "w1"),
                _text_msg("Done."),
            ],
            stream_calls,
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok"), patch.object(
            ca.ft, "preview_write", return_value={}
        ):
            with _Responder():
                evs = await _collect(body)

        self.assertFalse(any(e.get("type") == "error" for e in evs[:3]))
        self.assertEqual(picks.await_args_list[0].kwargs["mode"], "implementing")
        self.assertTrue(picks.await_args_list[0].kwargs["plan_approved"])
        self.assertEqual(len(picks.await_args_list[0].kwargs["todos"]), 2)
        options = [e["option"] for e in evs if e["type"] == "decision_pick"]
        self.assertEqual(options[0], "write_file")
        self.assertEqual(evs[-1]["type"], "done")


if __name__ == "__main__":
    unittest.main()
