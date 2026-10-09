"""Plan / todos run-state loop tests for chat_agent (Jev loop, mocked Ollama + Jev)."""
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


PLAN_ARGS = {
    "summary": "Add two files",
    "todos": [
        {"id": "t1", "label": "Write a.txt", "status": "in_progress"},
        {"id": "t2", "label": "Write b.txt", "status": "pending"},
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
    """Resolves pending futures by kind while a turn is running."""

    def __init__(self, plan_action: Optional[str] = "approve", on_plan=None):
        self.plan_action = plan_action
        self.on_plan = on_plan
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
                    if self.on_plan:
                        self.on_plan()
                    if self.plan_action:
                        ca.resolve_plan_decision(pid, self.plan_action, "tighten scope")
                    else:
                        # leave unresolved; cancel path will resolve it
                        continue
                elif kind == "tool_permission":
                    self.seen.append("tool_permission")
                    ca.resolve_tool_permission(pid, True)
                elif kind == "write":
                    self.seen.append("write")
                    ca.resolve_write_decision(pid, True)
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
        "messages": [{"role": "user", "content": "implement feature X"}],
        "model": "m",
        "tools_mode": "always",
        "workspace_roots": [],
        "writes_armed": False,
    }
    base.update(extra)
    return base


class ForceReplyGateTest(unittest.TestCase):
    def test_force_reply_respects_run_state(self):
        self.assertFalse(
            ca._force_reply_after_tool_for_state(
                "write_file",
                {"apply": True},
                True,
                pr.RunState(mode="implementing", plan_approved=True),
            )
        )
        self.assertTrue(
            ca._force_reply_after_tool_for_state(
                "write_file", {"apply": True}, True, pr.RunState(mode="normal")
            )
        )

    def test_force_reply_still_false_for_reads_and_failures(self):
        normal = pr.RunState(mode="normal")
        self.assertFalse(ca._force_reply_after_tool_for_state("read_file", {}, True, normal))
        self.assertFalse(ca._force_reply_after_tool_for_state("write_file", {}, False, normal))
        self.assertFalse(
            ca._force_reply_after_tool_for_state("edit_file", {"apply": False}, True, normal)
        )

    def test_cancel_events_mark_in_progress_cancelled(self):
        state = pr.RunState(
            mode="implementing",
            plan_approved=True,
            todos=[
                {"id": "a", "label": "A", "status": "in_progress"},
                {"id": "b", "label": "B", "status": "done"},
            ],
        )
        events = ca._jev_cancel_events(state, [])
        self.assertEqual(events[-1]["type"], "cancelled")
        todos_ev = next(e for e in events if e["type"] == "todos")
        self.assertEqual([t["status"] for t in todos_ev["todos"]], ["cancelled", "done"])

    def test_cancel_events_without_todos_only_cancelled(self):
        events = ca._jev_cancel_events(pr.RunState(), [])
        self.assertEqual([e["type"] for e in events], ["cancelled"])

    def test_system_prompt_is_mode_aware(self):
        self.assertNotIn("Do not narrate tool plans", ca.SYSTEM_PROMPT_TOOLS)
        for word in ("propose_plan", "update_todo", "implementing"):
            self.assertIn(word, ca.SYSTEM_PROMPT_TOOLS)

    def test_mode_system_note_carries_normal_mode_guidance(self):
        note = ca._mode_system_note(pr.RunState(mode="normal"))
        self.assertIsNotNone(note)
        self.assertIn("ask_clarify", note)
        self.assertIn("propose_plan", note)
        # Held / implementing modes keep their own notes.
        self.assertIn("not approved", ca._mode_system_note(pr.RunState(mode="planning")))
        self.assertIn(
            "approved",
            ca._mode_system_note(pr.RunState(mode="implementing", plan_approved=True)),
        )


class ResolvePlanDecisionTest(unittest.IsolatedAsyncioTestCase):
    async def test_approve_resolves_future(self):
        pid, fut = ca._new_pending("plan", [])
        self.assertTrue(ca.resolve_plan_decision(pid, "Approve", " ok "))
        self.assertEqual(fut.result(), {"action": "approve", "note": "ok"})
        self.assertNotIn(pid, ca._PENDING)

    async def test_wrong_kind_and_bad_action_rejected(self):
        wpid, _ = ca._new_pending("write", [])
        self.assertFalse(ca.resolve_plan_decision(wpid, "approve"))
        ca._PENDING.pop(wpid, None)
        ppid, fut = ca._new_pending("plan", [])
        self.assertFalse(ca.resolve_plan_decision(ppid, "nope"))
        self.assertFalse(fut.done())
        self.assertIn(ppid, ca._PENDING)
        ca._PENDING.pop(ppid, None)
        self.assertFalse(ca.resolve_plan_decision("missing", "approve"))


class PlanTodosLoopTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ca._PENDING.clear()

    async def test_approve_flow_emits_plan_pending_then_todos_implementing(self):
        """Minimum bar: plan_pending → Approve → todos, with implementing mode in the next pick."""
        picks = AsyncMock(side_effect=[("propose_plan", 0.9, "jev"), ("reply", 0.9, "jev")])
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream(
            [_tool_msg("propose_plan", PLAN_ARGS), _text_msg("All set.")], stream_calls
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ):
            with _Responder("approve"):
                evs = await _collect(_body())

        types = [e["type"] for e in evs]
        self.assertIn("plan_pending", types)
        plan = next(e for e in evs if e["type"] == "plan_pending")
        self.assertEqual(plan["summary"], "Add two files")
        self.assertEqual([t["id"] for t in plan["todos"]], ["t1", "t2"])
        self.assertLess(types.index("plan_pending"), types.index("plan_decision"))
        decision = next(e for e in evs if e["type"] == "plan_decision")
        self.assertEqual(decision["action"], "approve")
        self.assertEqual(decision["id"], plan["id"])
        todos_ev = next(e for e in evs if e["type"] == "todos")
        self.assertEqual(len(todos_ev["todos"]), 2)
        self.assertLess(types.index("plan_decision"), types.index("todos"))
        self.assertEqual(types[-1], "done")
        # First pick normal; second pick sees implementing + approved + todos.
        first, second = picks.await_args_list
        self.assertEqual(first.kwargs["mode"], "normal")
        self.assertFalse(first.kwargs["plan_approved"])
        self.assertEqual(second.kwargs["mode"], "implementing")
        self.assertTrue(second.kwargs["plan_approved"])
        self.assertEqual(len(second.kwargs["todos"]), 2)
        # propose_plan fill uses control schema, no token leak.
        self.assertEqual(
            stream_calls[0]["tools"][0]["function"]["name"], "propose_plan"
        )
        self.assertFalse(stream_calls[0]["emit_tokens"])

    async def test_after_approve_two_writes_no_force_reply(self):
        picks = AsyncMock(
            side_effect=[
                ("propose_plan", 0.9, "jev"),
                ("write_file", 0.9, "jev"),
                ("write_file", 0.9, "jev"),
                ("reply", 0.9, "jev"),
            ]
        )
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream(
            [
                _tool_msg("propose_plan", PLAN_ARGS),
                _tool_msg("write_file", {"path": "/tmp/a.txt", "content": "a"}, "w1"),
                _tool_msg("write_file", {"path": "/tmp/b.txt", "content": "b"}, "w2"),
                _text_msg("Done."),
            ],
            stream_calls,
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok") as run_tool, patch.object(
            ca.ft, "preview_write", return_value={}
        ):
            with _Responder("approve") as r:
                evs = await _collect(_body())

        self.assertEqual(run_tool.call_count, 2)
        # 4 Jev picks → decision after first write was NOT force-replied.
        self.assertEqual(picks.await_count, 4)
        options = [e["option"] for e in evs if e["type"] == "decision_pick"]
        self.assertEqual(options, ["propose_plan", "write_file", "write_file", "reply"])
        # Safety cards still fire after plan Approve.
        self.assertEqual(r.seen.count("tool_permission"), 2)
        # Gated writes are queued and reviewed once as a batch (no per-write cards).
        self.assertEqual(r.seen.count("writes_batch"), 1)
        self.assertNotIn("write", r.seen)
        self.assertFalse(any(e["type"] == "write_pending" for e in evs))
        self.assertEqual([e["type"] for e in evs].count("write_queued"), 2)
        self.assertEqual(evs[-1]["type"], "done")

    async def test_adhoc_write_still_force_reply(self):
        """Ad-hoc: queued write is not a mutation; once the batch is applied
        (flushed by the reply pick) the turn ends without more tool rounds."""
        picks = AsyncMock(side_effect=[("write_file", 0.9, "jev"), ("reply", 0.9, "jev")])
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream(
            [
                _tool_msg("write_file", {"path": "/tmp/a.txt", "content": "a"}, "w1"),
                _text_msg("Wrote it."),
            ],
            stream_calls,
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok"), patch.object(
            ca.ft, "preview_write", return_value={}
        ):
            with _Responder():
                evs = await _collect(_body())

        self.assertEqual(picks.await_count, 2)
        options = [e["option"] for e in evs if e["type"] == "decision_pick"]
        self.assertEqual(options, ["write_file", "reply"])
        self.assertIsNone(stream_calls[1].get("tools"))
        types = [e["type"] for e in evs]
        self.assertLess(types.index("writes_batch_resolved"), types.index("done"))
        self.assertEqual(evs[-1]["type"], "done")
        self.assertFalse(any(e["type"] in ("plan_pending", "todos") for e in evs))

    async def test_revise_keeps_hold_blocks_mutating(self):
        """Revise → planning; a write_file pick is denied without cards or execution."""
        picks = AsyncMock(
            side_effect=[("propose_plan", 0.9, "jev"), ("write_file", 0.9, "jev"), ("reply", 0.9, "jev")]
        )
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream(
            [_tool_msg("propose_plan", PLAN_ARGS), _text_msg("Need a revised plan.")],
            stream_calls,
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok") as run_tool:
            with _Responder("revise") as r:
                evs = await _collect(_body())

        decision = next(e for e in evs if e["type"] == "plan_decision")
        self.assertEqual(decision["action"], "revise")
        run_tool.assert_not_called()
        self.assertFalse(any(e["type"] == "tool_permission_pending" for e in evs))
        self.assertFalse(any(e["type"] == "write_pending" for e in evs))
        self.assertNotIn("tool_permission", r.seen)
        denied = [
            e for e in evs if e["type"] == "tool_result" and e["name"] == "write_file"
        ]
        self.assertEqual(len(denied), 1)
        self.assertFalse(denied[0]["ok"])
        self.assertIn("Plan not approved", denied[0]["content"])
        # Picker saw planning mode (hold) on the picks after revise.
        self.assertEqual(picks.await_args_list[1].kwargs["mode"], "planning")
        self.assertFalse(picks.await_args_list[1].kwargs["plan_approved"])
        # Ollama was not asked to fill the held write (only plan fill + reply).
        self.assertEqual(len(stream_calls), 2)
        self.assertEqual(evs[-1]["type"], "done")

    async def test_hold_message_is_user_role_not_orphan_tool(self):
        seen_messages: List[List[Dict[str, Any]]] = []
        picks = AsyncMock(
            side_effect=[("propose_plan", 0.9, "jev"), ("write_file", 0.9, "jev"), ("reply", 0.9, "jev")]
        )
        queue = [_tool_msg("propose_plan", PLAN_ARGS), _text_msg("ok")]

        async def stream(*args, **kwargs):
            seen_messages.append(list(kwargs["messages"]))
            yield {"type": "ollama_message", "message": queue.pop(0)}

        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok"):
            with _Responder("revise"):
                await _collect(_body())

        last = seen_messages[-1]
        self.assertFalse(
            any(
                m.get("role") == "tool" and m.get("name") == "write_file" for m in last
            )
        )
        self.assertTrue(
            any(
                m.get("role") == "user"
                and m.get("content", "").startswith("(system)")
                and "Plan not approved" in m.get("content", "")
                for m in last
            )
        )

    async def test_failed_propose_plan_fill_still_holds_writes(self):
        picks = AsyncMock(
            side_effect=[("propose_plan", 0.9, "jev"), ("write_file", 0.9, "jev"), ("reply", 0.9, "jev")]
        )
        stream_calls: List[Dict[str, Any]] = []
        # propose_plan fill returns no tool call; then reply text.
        stream = _scripted_stream(
            [_text_msg("no tool call"), _text_msg("Sorry.")], stream_calls
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok") as run_tool:
            with _Responder("approve") as r:
                evs = await _collect(_body())

        run_tool.assert_not_called()
        self.assertNotIn("tool_permission", r.seen)
        self.assertFalse(any(e["type"] == "plan_pending" for e in evs))
        self.assertEqual(picks.await_args_list[0].kwargs["mode"], "normal")
        self.assertEqual(picks.await_args_list[1].kwargs["mode"], "planning")
        denied = [
            e for e in evs if e["type"] == "tool_result" and e["name"] == "write_file"
        ]
        self.assertEqual(len(denied), 1)
        self.assertFalse(denied[0]["ok"])
        self.assertEqual(evs[-1]["type"], "done")

    async def test_revise_note_reaches_messages(self):
        seen_messages: List[List[Dict[str, Any]]] = []
        picks = AsyncMock(side_effect=[("propose_plan", 0.9, "jev"), ("reply", 0.9, "jev")])
        queue = [_tool_msg("propose_plan", PLAN_ARGS), _text_msg("ok")]

        async def stream(*args, **kwargs):
            seen_messages.append(list(kwargs["messages"]))
            yield {"type": "ollama_message", "message": queue.pop(0)}

        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ):
            with _Responder("revise"):
                await _collect(_body())

        last = seen_messages[-1]
        self.assertTrue(
            any(
                m.get("role") == "user"
                and "revise" in m.get("content", "")
                and "tighten scope" in m.get("content", "")
                for m in last
            )
        )

    async def test_cancel_resolves_plan_pending(self):
        cancel = asyncio.Event()
        picks = AsyncMock(side_effect=[("propose_plan", 0.9, "jev")])
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream([_tool_msg("propose_plan", PLAN_ARGS)], stream_calls)
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ), patch.object(ca.ft, "run_tool", return_value="ok") as run_tool:
            with _Responder(plan_action=None, on_plan=cancel.set):
                evs = await _collect(_body(), cancel=cancel)

        types = [e["type"] for e in evs]
        self.assertIn("plan_pending", types)
        decision = next(e for e in evs if e["type"] == "plan_decision")
        self.assertEqual(decision["action"], "cancelled")
        self.assertEqual(types[-1], "cancelled")
        # in_progress todo from the plan is cancelled in the final snapshot.
        final_todos = [e for e in evs if e["type"] == "todos"][-1]
        self.assertEqual(
            {t["id"]: t["status"] for t in final_todos["todos"]},
            {"t1": "cancelled", "t2": "pending"},
        )
        run_tool.assert_not_called()
        self.assertFalse(any(v.get("kind") == "plan" for v in ca._PENDING.values()))

    async def test_update_todo_emits_snapshot_only_when_implementing(self):
        picks = AsyncMock(
            side_effect=[
                ("update_todo", 0.9, "jev"),  # normal mode → rejected
                ("propose_plan", 0.9, "jev"),
                ("update_todo", 0.9, "jev"),
                ("reply", 0.9, "jev"),
            ]
        )
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream(
            [
                _tool_msg("propose_plan", PLAN_ARGS),
                _tool_msg("update_todo", {"todos": [{"id": "t1", "status": "done"}]}),
                _text_msg("Finished."),
            ],
            stream_calls,
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ):
            with _Responder("approve"):
                evs = await _collect(_body())

        todo_events = [e for e in evs if e["type"] == "todos"]
        self.assertEqual(len(todo_events), 2)  # approve snapshot + update
        self.assertEqual(todo_events[-1]["todos"][0]["status"], "done")
        self.assertEqual(todo_events[-1]["todos"][1]["status"], "pending")
        self.assertEqual(evs[-1]["type"], "done")

    async def test_ask_clarify_streams_text_and_ends_turn(self):
        picks = AsyncMock(side_effect=[("ask_clarify", 0.9, "jev")])
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream([_text_msg("Which directory?")], stream_calls)
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ):
            evs = await _collect(_body())

        self.assertIsNone(stream_calls[0].get("tools"))
        self.assertEqual(
            [e["content"] for e in evs if e["type"] == "token"], ["Which directory?"]
        )
        self.assertEqual(evs[-1]["type"], "done")
        self.assertEqual(picks.await_count, 1)

    async def test_ask_clarify_narration_continues_turn(self):
        """Status updates mis-tagged as ask_clarify must not end the turn."""
        picks = AsyncMock(
            side_effect=[("ask_clarify", 0.9, "jev"), ("reply", 0.9, "jev")]
        )
        stream_calls: List[Dict[str, Any]] = []
        stream = _scripted_stream(
            [
                _text_msg("I'll begin with actionreplay and read every file."),
                _text_msg("Audit complete."),
            ],
            stream_calls,
        )
        with patch.object(jp, "pick_next_action", new=picks), patch.object(
            ca, "_ollama_chat_stream", new=stream
        ):
            evs = await _collect(_body())

        self.assertTrue(any(e.get("type") == "segment_done" for e in evs))
        self.assertEqual(picks.await_count, 2)
        self.assertEqual(evs[-1]["type"], "done")
        self.assertEqual(evs[-1].get("message"), "Audit complete.")

    async def test_jev_error_still_hard_stops(self):
        with patch.object(
            jp,
            "pick_next_action",
            new=AsyncMock(side_effect=jp.JevPickerError("Jev unreachable: boom")),
        ), patch.object(ca, "_ollama_chat_stream", new=AsyncMock()) as ollama:
            evs = await _collect(_body())
        ollama.assert_not_called()
        self.assertEqual(evs[-1]["type"], "error")


if __name__ == "__main__":
    unittest.main()
