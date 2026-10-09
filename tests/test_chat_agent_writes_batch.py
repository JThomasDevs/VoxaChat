"""Writes-batch loop tests for chat_agent (Jev loop, mocked Ollama + Jev)."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import chat_agent as ca  # noqa: E402
import jev_picker as jp  # noqa: E402
import write_batch as wb  # noqa: E402


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
    """Resolves pending futures by kind while a turn is running."""

    def __init__(
        self,
        batch_approved: Optional[Callable[[List[str]], List[str]]] = None,
        leave_batch_open: bool = False,
        on_batch: Optional[Callable[[], None]] = None,
        order: Optional[List[str]] = None,
    ):
        self.batch_approved = batch_approved
        self.leave_batch_open = leave_batch_open
        self.on_batch = on_batch
        self.order = order if order is not None else []
        self.seen: List[str] = []
        self.batch_entries: List[Dict[str, Any]] = []
        self._handled: set = set()
        self._task: Optional[asyncio.Task] = None
        self._stop = False

    async def _run(self):
        while not self._stop:
            await asyncio.sleep(0.01)
            for pid, entry in list(ca._PENDING.items()):
                kind = entry.get("kind")
                if kind == "tool_permission":
                    self.seen.append("tool_permission")
                    ca.resolve_tool_permission(pid, True)
                elif kind == "write":
                    self.seen.append("write")
                    ca.resolve_write_decision(pid, True)
                elif kind == "shell":
                    self.seen.append("shell")
                    ca.resolve_shell_decision(pid, False)
                elif kind == "writes_batch":
                    if pid in self._handled:
                        continue
                    self._handled.add(pid)
                    self.seen.append("writes_batch")
                    self.batch_entries.append(entry)
                    if self.on_batch:
                        self.on_batch()
                    if self.leave_batch_open:
                        continue
                    ids = [i["item_id"] for i in entry.get("items", [])]
                    approved = self.batch_approved(ids) if self.batch_approved else ids
                    self.order.append("decide")
                    self.assert_ok(ca.resolve_writes_batch_decision(pid, approved))

    @staticmethod
    def assert_ok(value: bool) -> None:
        assert value, "resolve_writes_batch_decision returned False"

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
        "messages": [{"role": "user", "content": "write two files"}],
        "model": "m",
        "tools_mode": "always",
        "workspace_roots": ["/tmp"],
        "writes_armed": False,
    }
    base.update(extra)
    return base


def _types(evs):
    return [e["type"] for e in evs]


class ResolveWritesBatchDecisionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ca._PENDING.clear()

    async def test_resolves_future_with_approved_ids(self):
        pid, fut = ca._new_pending("writes_batch", [])
        self.assertTrue(ca.resolve_writes_batch_decision(pid, ["a", "b"]))
        self.assertEqual(fut.result(), {"approved_ids": ["a", "b"]})
        self.assertNotIn(pid, ca._PENDING)

    async def test_wrong_kind_or_unknown_rejected(self):
        wpid, _ = ca._new_pending("write", [])
        self.assertFalse(ca.resolve_writes_batch_decision(wpid, []))
        self.assertIn(wpid, ca._PENDING)
        ca._PENDING.pop(wpid, None)
        self.assertFalse(ca.resolve_writes_batch_decision("missing", []))


class WritesBatchLoopTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ca._PENDING.clear()

    async def _run_turn(
        self,
        picks_seq,
        stream_msgs,
        responder,
        body=None,
        cancel=None,
        run_tool=None,
    ):
        picks = AsyncMock(side_effect=picks_seq)
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream(stream_msgs, stream_calls)
        rt = run_tool if run_tool is not None else (lambda *a, **k: "ok")
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", side_effect=rt) as run_tool_mock, patch.object(
            ca.ft, "preview_write", return_value="PREVIEW"
        ):
            with responder:
                evs = await _collect(body or _body(), cancel=cancel)
        return evs, run_tool_mock, picks, stream_calls

    async def test_two_writes_one_batch_before_reply(self):
        order: List[str] = []

        def rt(*a, **k):
            order.append("run")
            return "ok"

        r = _Responder(order=order)
        evs, run_tool, picks, _ = await self._run_turn(
            [("write_file", 0.9, "jev"), ("write_file", 0.9, "jev"), ("reply", 0.9, "jev")],
            [_write("/tmp/a.txt", "w1"), _write("/tmp/b.txt", "w2"), _text_msg("Done.")],
            r,
            run_tool=rt,
        )
        types = _types(evs)
        self.assertEqual(types.count("write_queued"), 2)
        self.assertEqual(types.count("writes_batch_pending"), 1)
        self.assertEqual(types.count("writes_batch_resolved"), 1)
        self.assertNotIn("write_pending", types)
        self.assertNotIn("write", r.seen)
        self.assertEqual(run_tool.call_count, 2)
        # Nothing hit disk until the batch was decided.
        self.assertEqual(order, ["decide", "run", "run"])
        # Queue → pending → resolved → done, in order.
        self.assertLess(types.index("write_queued"), types.index("writes_batch_pending"))
        self.assertLess(
            types.index("writes_batch_pending"), types.index("writes_batch_resolved")
        )
        self.assertLess(types.index("writes_batch_resolved"), types.index("done"))
        queued = [e for e in evs if e["type"] == "write_queued"]
        pending = next(e for e in evs if e["type"] == "writes_batch_pending")
        resolved = next(e for e in evs if e["type"] == "writes_batch_resolved")
        self.assertEqual({q["batch_id"] for q in queued}, {pending["id"]})
        self.assertEqual(resolved["id"], pending["id"])
        self.assertEqual(
            [i["item_id"] for i in pending["items"]], [q["item_id"] for q in queued]
        )
        self.assertEqual(resolved["applied"], [q["item_id"] for q in queued])
        self.assertEqual(resolved["denied"], [])
        self.assertEqual(evs[-1]["type"], "done")
        # Queued tool_result is ok but never claims "applied".
        tool_results = [
            e for e in evs if e["type"] == "tool_result" and e["name"] == "write_file"
        ]
        self.assertEqual(len(tool_results), 2)
        for tr in tool_results:
            self.assertTrue(tr["ok"])
            self.assertIn("queued", tr["content"])
        # Queued-only writes must not force a reply (Jev picked reply itself).
        self.assertEqual(picks.await_count, 3)
        self.assertEqual(len(ca._PENDING), 0)

    async def test_approve_subset(self):
        r = _Responder(batch_approved=lambda ids: ids[:1])
        evs, run_tool, _, _ = await self._run_turn(
            [("write_file", 0.9, "jev"), ("write_file", 0.9, "jev"), ("reply", 0.9, "jev")],
            [_write("/tmp/a.txt", "w1"), _write("/tmp/b.txt", "w2"), _text_msg("ok")],
            r,
        )
        queued = [e for e in evs if e["type"] == "write_queued"]
        resolved = next(e for e in evs if e["type"] == "writes_batch_resolved")
        self.assertEqual(run_tool.call_count, 1)
        self.assertEqual(run_tool.call_args.args[1]["path"], "/tmp/a.txt")
        self.assertEqual(resolved["applied"], [queued[0]["item_id"]])
        self.assertEqual(resolved["denied"], [queued[1]["item_id"]])

    async def test_deny_all(self):
        r = _Responder(batch_approved=lambda ids: [])
        evs, run_tool, _, _ = await self._run_turn(
            [("write_file", 0.9, "jev"), ("reply", 0.9, "jev")],
            [_write("/tmp/a.txt", "w1"), _text_msg("ok")],
            r,
        )
        resolved = next(e for e in evs if e["type"] == "writes_batch_resolved")
        run_tool.assert_not_called()
        self.assertEqual(resolved["applied"], [])
        self.assertEqual(len(resolved["denied"]), 1)
        self.assertEqual(evs[-1]["type"], "done")

    async def test_apply_error_recorded_and_others_applied(self):
        def rt(name, args, roots):
            if args["path"].endswith("a.txt"):
                raise ca.ft.FsError("boom")
            return "ok"

        r = _Responder()
        evs, run_tool, _, _ = await self._run_turn(
            [("write_file", 0.9, "jev"), ("write_file", 0.9, "jev"), ("reply", 0.9, "jev")],
            [_write("/tmp/a.txt", "w1"), _write("/tmp/b.txt", "w2"), _text_msg("ok")],
            r,
            run_tool=rt,
        )
        queued = [e for e in evs if e["type"] == "write_queued"]
        resolved = next(e for e in evs if e["type"] == "writes_batch_resolved")
        self.assertEqual(run_tool.call_count, 2)
        self.assertEqual(resolved["applied"], [queued[1]["item_id"]])
        self.assertEqual(resolved["errors"][0]["item_id"], queued[0]["item_id"])
        self.assertIn("boom", resolved["errors"][0]["error"])

    async def test_shell_flushes_batch_first(self):
        r = _Responder()
        evs, run_tool, _, _ = await self._run_turn(
            [("write_file", 0.9, "jev"), ("shell_run", 0.9, "jev")],
            [
                _write("/tmp/a.txt", "w1"),
                _tool_msg("shell_run", {"command": "echo hi"}, "s1"),
                _text_msg("done"),
            ],
            r,
        )
        types = _types(evs)
        self.assertIn("shell_pending", types)
        self.assertLess(types.index("writes_batch_pending"), types.index("shell_pending"))
        self.assertLess(
            types.index("writes_batch_resolved"), types.index("shell_pending")
        )
        self.assertEqual(run_tool.call_count, 1)
        self.assertEqual(r.seen.count("writes_batch"), 1)
        self.assertEqual(evs[-1]["type"], "done")

    async def test_same_path_rejected(self):
        r = _Responder()
        evs, run_tool, _, _ = await self._run_turn(
            [("write_file", 0.9, "jev"), ("write_file", 0.9, "jev"), ("reply", 0.9, "jev")],
            [_write("/tmp/a.txt", "w1"), _write("/tmp/a.txt", "w2"), _text_msg("ok")],
            r,
        )
        results = [
            e for e in evs if e["type"] == "tool_result" and e["name"] == "write_file"
        ]
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertIn("still pending", results[1]["content"])
        self.assertEqual(_types(evs).count("write_queued"), 1)
        pending = next(e for e in evs if e["type"] == "writes_batch_pending")
        self.assertEqual(len(pending["items"]), 1)
        self.assertEqual(run_tool.call_count, 1)

    async def test_cancel_open_batch(self):
        cancel = asyncio.Event()
        r = _Responder(leave_batch_open=True, on_batch=cancel.set)
        evs, run_tool, _, _ = await self._run_turn(
            [("write_file", 0.9, "jev"), ("reply", 0.9, "jev")],
            [_write("/tmp/a.txt", "w1"), _text_msg("ok")],
            r,
            cancel=cancel,
        )
        types = _types(evs)
        self.assertIn("writes_batch_pending", types)
        self.assertEqual(types[-1], "cancelled")
        self.assertNotIn("writes_batch_resolved", types)
        run_tool.assert_not_called()
        self.assertFalse(any(v.get("kind") == "writes_batch" for v in ca._PENDING.values()))

    async def test_cancel_before_flush_discards_queue(self):
        cancel = asyncio.Event()

        picks_calls = {"n": 0}

        async def pick(*a, **k):
            picks_calls["n"] += 1
            if picks_calls["n"] == 1:
                return ("write_file", 0.9, "jev")
            cancel.set()
            return ("write_file", 0.9, "jev")

        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream([_write("/tmp/a.txt", "w1")], stream_calls)
        with patch.object(jp, "pick_next_action", new=pick), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok") as run_tool, patch.object(
            ca.ft, "preview_write", return_value="P"
        ):
            with _Responder():
                evs = await _collect(_body(), cancel=cancel)
        self.assertEqual(evs[-1]["type"], "cancelled")
        run_tool.assert_not_called()

    async def test_writes_armed_skips_batch(self):
        r = _Responder()
        evs, run_tool, _, _ = await self._run_turn(
            [("write_file", 0.9, "jev")],
            [_write("/tmp/a.txt", "w1"), _text_msg("Wrote it.")],
            r,
            body=_body(writes_armed=True),
        )
        types = _types(evs)
        for t in ("write_queued", "writes_batch_pending", "writes_batch_resolved", "write_pending"):
            self.assertNotIn(t, types)
        self.assertEqual(run_tool.call_count, 1)
        self.assertNotIn("writes_batch", r.seen)
        self.assertEqual(evs[-1]["type"], "done")

    async def test_max_batch_flushes_then_opens_new_batch(self):
        r = _Responder()
        with patch.object(wb, "MAX_WRITE_BATCH", 2):
            evs, run_tool, _, _ = await self._run_turn(
                [("write_file", 0.9, "jev"), ("write_file", 0.9, "jev"), ("write_file", 0.9, "jev")],
                [
                    _write("/tmp/a.txt", "w1"),
                    _write("/tmp/b.txt", "w2"),
                    _write("/tmp/c.txt", "w3"),
                    _text_msg("done"),
                ],
                r,
            )
        pendings = [e for e in evs if e["type"] == "writes_batch_pending"]
        self.assertEqual(len(pendings), 2)
        self.assertEqual([len(p["items"]) for p in pendings], [2, 1])
        self.assertNotEqual(pendings[0]["id"], pendings[1]["id"])
        self.assertEqual(run_tool.call_count, 3)
        # First flush happens before the third write is queued.
        types = _types(evs)
        third_queue = [i for i, t in enumerate(types) if t == "write_queued"][2]
        self.assertLess(types.index("writes_batch_resolved"), third_queue)
        self.assertEqual(evs[-1]["type"], "done")

    async def test_batch_result_note_reaches_reply_messages(self):
        seen: List[List[Dict[str, Any]]] = []
        queue = [_write("/tmp/a.txt", "w1"), _text_msg("ok")]

        async def stream(*args, **kwargs):
            seen.append(list(kwargs["messages"]))
            yield {"type": "ollama_message", "message": queue.pop(0)}

        picks = AsyncMock(side_effect=[("write_file", 0.9, "jev"), ("reply", 0.9, "jev")])
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok"), patch.object(
            ca.ft, "preview_write", return_value="P"
        ):
            with _Responder():
                await _collect(_body())
        last = seen[-1]
        self.assertTrue(
            any(
                m.get("role") == "user"
                and m.get("content", "").startswith("(system)")
                and "Applied: /tmp/a.txt" in m.get("content", "")
                for m in last
            )
        )


if __name__ == "__main__":
    unittest.main()
