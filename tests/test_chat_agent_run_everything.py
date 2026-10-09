"""run_everything auto-approve gates (writes, shell, tool_permission; not plan)."""
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


def _tool_msg(name: str, args: Dict[str, Any], call_id: str = "c1") -> Dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": args}}],
    }


def _text_msg(text: str) -> Dict[str, Any]:
    return {"role": "assistant", "content": text}


def _write(path: str, call_id: str) -> Dict[str, Any]:
    return _tool_msg("write_file", {"path": path, "content": "x"}, call_id)


PLAN_ARGS = {
    "summary": "Add a file",
    "todos": [
        {"id": "t1", "label": "Write a.txt", "status": "pending"},
    ],
}


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
    """Resolves plan / leftover gates while a turn runs."""

    def __init__(self, plan_action: Optional[str] = "approve"):
        self.plan_action = plan_action
        self.seen: List[str] = []
        self._task: Optional[asyncio.Task] = None
        self._stop = False

    async def _run(self):
        while not self._stop:
            await asyncio.sleep(0.01)
            for pid, entry in list(ca._PENDING.items()):
                kind = entry.get("kind")
                if kind == "plan":
                    self.seen.append("plan")
                    if self.plan_action:
                        ca.resolve_plan_decision(pid, self.plan_action)
                elif kind == "tool_permission":
                    self.seen.append("tool_permission")
                    ca.resolve_tool_permission(pid, True)
                elif kind == "writes_batch":
                    self.seen.append("writes_batch")
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


async def _collect(body, cancel=None):
    return [
        ev
        async for ev in ca._iter_jev_chat_events(body, "http://ollama.test", cancel=cancel)
    ]


def _body(**extra):
    base = {
        "messages": [{"role": "user", "content": "do the thing"}],
        "model": "m",
        "tools_mode": "always",
        "workspace_roots": [],
        "writes_armed": False,
        "run_everything": True,
    }
    base.update(extra)
    return base


def _types(evs):
    return [e["type"] for e in evs]


class RunFlagsTest(unittest.TestCase):
    def test_run_everything_implies_writes_armed(self):
        re, wa = ca._run_flags({"run_everything": True, "writes_armed": False})
        self.assertTrue(re)
        self.assertTrue(wa)

    def test_writes_armed_alone_does_not_set_run_everything(self):
        re, wa = ca._run_flags({"run_everything": False, "writes_armed": True})
        self.assertFalse(re)
        self.assertTrue(wa)

    def test_defaults_off(self):
        re, wa = ca._run_flags({})
        self.assertFalse(re)
        self.assertFalse(wa)


class RunEverythingLoopTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ca._PENDING.clear()

    async def test_skips_write_batch(self):
        picks = AsyncMock(side_effect=[("write_file", 0.9, "jev")])
        stream = _scripted_stream(
            [_write("/tmp/a.txt", "w1"), _text_msg("Wrote.")], []
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok") as run_tool, patch.object(
            ca.ft, "preview_write", return_value="P"
        ):
            evs = await _collect(_body())
        types = _types(evs)
        for t in (
            "write_queued",
            "writes_batch_pending",
            "writes_batch_resolved",
            "write_pending",
            "tool_permission_pending",
        ):
            self.assertNotIn(t, types)
        self.assertEqual(run_tool.call_count, 1)
        self.assertEqual(evs[-1]["type"], "done")

    async def test_skips_shell_pending(self):
        picks = AsyncMock(side_effect=[("shell_run", 0.9, "jev"), ("reply", 0.9, "jev")])
        stream = _scripted_stream(
            [
                _tool_msg("shell_run", {"command": "echo hi"}, "s1"),
                _text_msg("done"),
            ],
            [],
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(
            ca.ft, "_shell_execute", return_value='{"stdout":"hi\\n","exit_code":0}'
        ) as shell_exec:
            # UI shape: both flags true when "Run everything" is checked.
            evs = await _collect(
                _body(
                    run_everything=True,
                    writes_armed=True,
                    workspace_roots=["/tmp"],
                )
            )
        types = _types(evs)
        self.assertNotIn("shell_pending", types)
        self.assertNotIn("tool_permission_pending", types)
        self.assertEqual(shell_exec.call_count, 1)
        results = [e for e in evs if e["type"] == "tool_result" and e["name"] == "shell_run"]
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"])
        self.assertIn("hi", results[0]["content"])
        self.assertEqual(evs[-1]["type"], "done")

    async def test_shell_run_skips_tool_permission_pending(self):
        """Regression: Run everything must not emit pre-Ollama Allow tool? for shell_run."""
        picks = AsyncMock(side_effect=[("shell_run", 0.9, "jev"), ("reply", 0.9, "jev")])
        stream = _scripted_stream(
            [
                _tool_msg("shell_run", {"command": "echo hi"}, "s1"),
                _text_msg("done"),
            ],
            [],
        )
        ollama_calls: List[Dict[str, Any]] = []

        async def tracking_stream(*args, **kwargs):
            ollama_calls.append(kwargs)
            async for ev in stream(*args, **kwargs):
                yield ev

        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=tracking_stream
        ), patch.object(
            ca.ft, "_shell_execute", return_value='{"stdout":"hi\\n","exit_code":0}'
        ):
            evs = await _collect(_body(run_everything=True, writes_armed=True))

        # Pre-Ollama yellow card must never appear; arg-fill still runs.
        self.assertFalse(any(e["type"] == "tool_permission_pending" for e in evs))
        self.assertGreaterEqual(len(ollama_calls), 1)
        pick = next(e for e in evs if e["type"] == "decision_pick")
        self.assertEqual(pick["option"], "shell_run")
        # decision_pick must not be followed by tool_permission_pending.
        pick_i = next(i for i, e in enumerate(evs) if e["type"] == "decision_pick")
        after = [e["type"] for e in evs[pick_i + 1 : pick_i + 4]]
        self.assertNotIn("tool_permission_pending", after)

    async def test_shell_run_emits_tool_permission_without_run_everything(self):
        """Control: without run_everything, shell_run still hits the pre-Ollama gate."""
        picks = AsyncMock(side_effect=[("shell_run", 0.9, "jev")])
        # Deny → force_reply → Ollama text turn (no tool arg-fill).
        stream = _scripted_stream([_text_msg("ok, skipped.")], [])

        async def deny_then_collect():
            async def resolver():
                for _ in range(200):
                    await asyncio.sleep(0.01)
                    for pid, entry in list(ca._PENDING.items()):
                        if entry.get("kind") == "tool_permission":
                            ca.resolve_tool_permission(pid, False)
                            return
                raise AssertionError("expected tool_permission pending")

            task = asyncio.create_task(resolver())
            try:
                return await _collect(
                    _body(run_everything=False, writes_armed=False)
                )
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ):
            evs = await deny_then_collect()
        self.assertTrue(any(e["type"] == "tool_permission_pending" for e in evs))
        perm = next(e for e in evs if e["type"] == "tool_permission_pending")
        self.assertEqual(perm["tool"], "shell_run")

    async def test_skips_tool_permission(self):
        picks = AsyncMock(side_effect=[("delete_file", 0.9, "jev"), ("reply", 0.9, "jev")])
        stream = _scripted_stream(
            [
                _tool_msg("delete_file", {"path": "/tmp/a.txt"}, "d1"),
                _text_msg("gone"),
            ],
            [],
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="deleted") as run_tool, patch.object(
            ca.ft, "preview_write", return_value="P"
        ):
            evs = await _collect(_body())
        self.assertFalse(any(e["type"] == "tool_permission_pending" for e in evs))
        self.assertEqual(run_tool.call_count, 1)
        self.assertEqual(evs[-1]["type"], "done")

    async def test_keeps_plan_pending(self):
        picks = AsyncMock(side_effect=[("propose_plan", 0.9, "jev"), ("reply", 0.9, "jev")])
        stream = _scripted_stream(
            [_tool_msg("propose_plan", PLAN_ARGS), _text_msg("All set.")], []
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ):
            with _Responder("approve") as r:
                evs = await _collect(_body())
        types = _types(evs)
        self.assertIn("plan_pending", types)
        self.assertIn("plan", r.seen)
        self.assertLess(types.index("plan_pending"), types.index("plan_decision"))
        self.assertEqual(evs[-1]["type"], "done")


if __name__ == "__main__":
    unittest.main()
