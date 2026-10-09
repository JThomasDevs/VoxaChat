"""Turn cancel -> shell_run kill wiring tests for chat_agent."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List
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


def _scripted_stream(messages: List[Dict[str, Any]]):
    queue = list(messages)

    async def fake_stream(*args, **kwargs):
        msg = queue.pop(0)
        yield {"type": "ollama_message", "message": msg}

    return fake_stream


class ShellPendingCancelEventTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ca._PENDING.clear()

    async def test_shell_pending_has_threading_cancel_event(self):
        pid, _ = ca._new_pending("shell", ["."], command="echo hi", cwd=".")
        ev = ca._PENDING[pid].get("cancel_event")
        self.assertIsInstance(ev, threading.Event)
        self.assertFalse(ev.is_set())

    async def test_non_shell_pending_has_no_cancel_event(self):
        pid, _ = ca._new_pending("write", ["."])
        self.assertNotIn("cancel_event", ca._PENDING[pid])


class ResolveShellCancelTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ca._PENDING.clear()

    async def test_resolve_shell_passes_entry_cancel_event(self):
        pid, fut = ca._new_pending("shell", ["."], command="echo hi", cwd=".")
        ev = ca._PENDING[pid]["cancel_event"]
        with patch.object(ca.ft, "_shell_execute", return_value="{}") as ex:
            self.assertTrue(ca.resolve_shell_decision(pid, True))
        self.assertIs(ex.call_args.kwargs.get("cancel_event"), ev)

    async def test_resolve_shell_honors_cancel_event(self):
        pid, fut = ca._new_pending("shell", ["."], command="sleep 30", cwd=".")
        ev = ca._PENDING[pid]["cancel_event"]
        threading.Timer(0.3, ev.set).start()
        t0 = time.monotonic()
        ok = await asyncio.wait_for(
            asyncio.to_thread(ca.resolve_shell_decision, pid, True), timeout=5
        )
        self.assertTrue(ok)
        self.assertLess(time.monotonic() - t0, 5)
        out = json.loads(fut.result()["output"])
        self.assertTrue(out.get("cancelled"))

    async def test_cancel_turn_pendings_kills_already_popped_shell(self):
        """Approve pops the entry; Stop must still reach the running process."""
        pid, fut = ca._new_pending("shell", ["."], command="sleep 30", cwd=".")
        worker = asyncio.create_task(
            asyncio.to_thread(ca.resolve_shell_decision, pid, True)
        )
        await asyncio.sleep(0.3)  # let execute start; entry already popped
        self.assertNotIn(pid, ca._PENDING)
        ca.cancel_turn_pendings([pid])
        t0 = time.monotonic()
        await asyncio.wait_for(worker, timeout=5)
        self.assertLess(time.monotonic() - t0, 5)
        # Future set thread-safely from the worker thread.
        await asyncio.sleep(0.05)
        self.assertTrue(fut.done())
        out = json.loads(fut.result()["output"])
        self.assertTrue(out.get("cancelled"))


class ShellCancelTurnTest(unittest.IsolatedAsyncioTestCase):
    """Full Jev loop: approve a long shell, Stop mid-execute."""

    def setUp(self):
        ca._PENDING.clear()

    async def test_stop_mid_shell_ends_turn_cancelled(self):
        cancel = asyncio.Event()
        handled: List[str] = []

        async def responder():
            while True:
                await asyncio.sleep(0.01)
                for pid, entry in list(ca._PENDING.items()):
                    if entry.get("kind") == "tool_permission":
                        ca.resolve_tool_permission(pid, True)
                    elif entry.get("kind") == "shell" and pid not in handled:
                        handled.append(pid)
                        asyncio.get_event_loop().call_later(0.4, cancel.set)
                        asyncio.create_task(
                            asyncio.to_thread(ca.resolve_shell_decision, pid, True)
                        )

        picks = AsyncMock(side_effect=[("shell_run", 0.9, "jev"), ("reply", 0.9, "jev")])
        stream = _scripted_stream(
            [
                _tool_msg("shell_run", {"command": "sleep 29.417"}, "s1"),
                {"role": "assistant", "content": "never"},
            ]
        )
        body = {
            "messages": [{"role": "user", "content": "run sleep"}],
            "model": "m",
            "tools_mode": "always",
            "workspace_roots": ["/tmp"],
            "writes_armed": False,
        }
        task = asyncio.create_task(responder())
        t0 = time.monotonic()
        try:
            with patch.object(jp, "pick_next_action", new=picks), patch.object(
                ca, "_ollama_chat_stream", new=stream
            ):
                evs = await asyncio.wait_for(
                    _collect(body, cancel), timeout=8
                )
        finally:
            task.cancel()
        self.assertLess(time.monotonic() - t0, 8)
        self.assertEqual(evs[-1]["type"], "cancelled")
        self.assertEqual(len(handled), 1)
        # The shell process must actually be gone (not just the turn).
        await asyncio.sleep(0.5)
        left = subprocess.run(
            ["pgrep", "-f", "sleep 29.417"], capture_output=True, text=True
        )
        self.assertEqual(left.stdout.strip(), "")


async def _collect(body, cancel=None):
    return [
        ev
        async for ev in ca._iter_jev_chat_events(body, "http://ollama.test", cancel=cancel)
    ]


class ShellCancelledOutputTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ca._PENDING.clear()

    async def test_cancelled_output_ends_turn_not_ok(self):
        """Approved shell returning cancelled JSON must not look like success."""
        cancelled_json = json.dumps({"error": "shell_run cancelled", "cancelled": True})

        async def responder():
            while True:
                await asyncio.sleep(0.01)
                for pid, entry in list(ca._PENDING.items()):
                    if entry.get("kind") == "tool_permission":
                        ca.resolve_tool_permission(pid, True)
                    elif entry.get("kind") == "shell":
                        ca._PENDING.pop(pid, None)
                        entry["future"].set_result(
                            {"approve": True, "output": cancelled_json}
                        )

        picks = AsyncMock(side_effect=[("shell_run", 0.9, "jev"), ("reply", 0.9, "jev")])
        stream = _scripted_stream(
            [
                _tool_msg("shell_run", {"command": "sleep 29.417"}, "s1"),
                {"role": "assistant", "content": "should not run"},
            ]
        )
        body = {
            "messages": [{"role": "user", "content": "run sleep"}],
            "model": "m",
            "tools_mode": "always",
            "workspace_roots": ["/tmp"],
            "writes_armed": False,
        }
        task = asyncio.create_task(responder())
        try:
            with patch.object(jp, "pick_next_action", new=picks), patch.object(
                ca, "_ollama_chat_stream", new=stream
            ):
                evs = await asyncio.wait_for(_collect(body), timeout=5)
        finally:
            task.cancel()
        self.assertEqual(evs[-1]["type"], "cancelled")
        self.assertFalse(
            any(e["type"] == "tool_result" and e.get("ok") for e in evs)
        )


if __name__ == "__main__":
    unittest.main()
