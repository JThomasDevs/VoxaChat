"""Unit tests for Ollama stream assembly + cancel (chat_agent)."""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import chat_agent as ca  # noqa: E402


def _ndjson_lines(chunks: List[Dict[str, Any]]) -> bytes:
    return b"".join((json.dumps(c) + "\n").encode() for c in chunks)


class FakeByteStream:
    """Minimal async byte stream for httpx Response.aiter_lines."""

    def __init__(self, data: bytes):
        self._lines = data.decode().splitlines()

    async def aiter_lines(self):
        for line in self._lines:
            yield line


def _mock_stream_response(chunks: List[Dict[str, Any]], status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "err" if status_code >= 400 else ""
    stream = FakeByteStream(_ndjson_lines(chunks))
    resp.aiter_lines = stream.aiter_lines

    class _CM:
        async def __aenter__(self_inner):
            return resp

        async def __aexit__(self_inner, *args):
            return False

    return _CM()


class ExcMessageTest(unittest.TestCase):
    def test_exc_message_uses_str_when_present(self):
        self.assertEqual(ca._exc_message(RuntimeError("boom")), "boom")

    def test_exc_message_fallback_for_empty_httpx(self):
        import httpx

        msg = ca._exc_message(httpx.ReadError(""), fallback="connection lost")
        self.assertIn("connection lost", msg)
        self.assertIn("ReadError", msg)

    def test_clarifying_question_detects_question_mark(self):
        self.assertTrue(ca._is_clarifying_question("Which plugin first?"))
        self.assertFalse(
            ca._is_clarifying_question(
                "I'll begin with actionreplay and read every file."
            )
        )

    def test_repeated_tool_failure_nudge(self):
        tracker: dict = {"sig": None, "count": 0}
        args = {"path": "src/main/java/net/runelite/microbot"}
        err = "error: list_dir failed: [Errno 2] No such file or directory: '…'"
        self.assertIsNone(
            ca._note_repeated_tool_failure(
                tracker, name="list_dir", args=args, ok=False, result=err
            )
        )
        self.assertIsNone(
            ca._note_repeated_tool_failure(
                tracker, name="list_dir", args=args, ok=False, result=err
            )
        )
        note = ca._note_repeated_tool_failure(
            tracker, name="list_dir", args=args, ok=False, result=err
        )
        self.assertIsNotNone(note)
        self.assertIn("failed 3 times", note["content"])
        self.assertIn("src/main/java/net/runelite/microbot", note["content"])
        # Success resets tracker.
        self.assertIsNone(
            ca._note_repeated_tool_failure(
                tracker, name="list_dir", args=args, ok=True, result="ok"
            )
        )
        self.assertEqual(tracker["count"], 0)


class OllamaStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_stream_error_chunk_raises_readable(self):
        chunks = [
            {"message": {"role": "assistant", "content": "Hi"}, "done": False},
            {"error": "model runner stopped"},
        ]
        client = AsyncMock()
        client.stream = MagicMock(return_value=_mock_stream_response(chunks))
        with self.assertRaises(RuntimeError) as ctx:
            async for _ in ca._ollama_chat_stream(
                client,
                "http://ollama.test",
                model="m",
                messages=[{"role": "user", "content": "x"}],
                tools=None,
            ):
                pass
        self.assertIn("model runner stopped", str(ctx.exception))

    async def test_empty_httpx_error_has_fallback_message(self):
        import httpx

        client = AsyncMock()

        class _CM:
            async def __aenter__(self_inner):
                raise httpx.ReadError("")

            async def __aexit__(self_inner, *args):
                return False

        client.stream = MagicMock(return_value=_CM())
        with self.assertRaises(RuntimeError) as ctx:
            async for _ in ca._ollama_chat_stream(
                client,
                "http://ollama.test",
                model="m",
                messages=[{"role": "user", "content": "x"}],
                tools=None,
            ):
                pass
        self.assertIn("ollama stream interrupted", str(ctx.exception))
        self.assertIn("connection lost", str(ctx.exception))

    async def test_stream_yields_tokens_then_assembled_message(self):
        chunks = [
            {"message": {"role": "assistant", "content": "Hel"}, "done": False},
            {"message": {"role": "assistant", "content": "lo"}, "done": False},
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "function": {
                                "name": "list_dir",
                                "arguments": {"path": "/tmp"},
                            },
                        }
                    ],
                },
                "done": True,
            },
        ]
        client = AsyncMock()
        client.stream = MagicMock(return_value=_mock_stream_response(chunks))

        events = []
        async for ev in ca._ollama_chat_stream(
            client,
            "http://ollama.test",
            model="mistral-nemo",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            emit_tokens=True,
        ):
            events.append(ev)

        tokens = [e["content"] for e in events if e.get("type") == "token"]
        self.assertEqual(tokens, ["Hel", "lo"])
        final = [e for e in events if e.get("type") == "ollama_message"]
        self.assertEqual(len(final), 1)
        msg = final[0]["message"]
        self.assertEqual(msg.get("role"), "assistant")
        self.assertEqual(msg.get("content"), "Hello")
        self.assertEqual(len(msg.get("tool_calls") or []), 1)
        self.assertEqual(msg["tool_calls"][0]["function"]["name"], "list_dir")

    async def test_emit_tokens_false_suppresses_token_events(self):
        chunks = [
            {"message": {"role": "assistant", "content": "secret"}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True},
        ]
        client = AsyncMock()
        client.stream = MagicMock(return_value=_mock_stream_response(chunks))
        events = []
        async for ev in ca._ollama_chat_stream(
            client,
            "http://ollama.test",
            model="m",
            messages=[{"role": "user", "content": "x"}],
            tools=None,
            emit_tokens=False,
        ):
            events.append(ev)
        self.assertFalse(any(e.get("type") == "token" for e in events))
        self.assertEqual(events[-1]["type"], "ollama_message")
        self.assertEqual(events[-1]["message"]["content"], "secret")

    async def test_stream_yields_thinking_then_tokens(self):
        chunks = [
            {
                "message": {
                    "role": "assistant",
                    "thinking": "Let me ",
                    "content": "",
                },
                "done": False,
            },
            {
                "message": {
                    "role": "assistant",
                    "thinking": "reason.",
                    "content": "",
                },
                "done": False,
            },
            {
                "message": {"role": "assistant", "thinking": "", "content": "Hi"},
                "done": False,
            },
            {"message": {"role": "assistant", "content": ""}, "done": True},
        ]
        client = AsyncMock()
        client.stream = MagicMock(return_value=_mock_stream_response(chunks))
        events = []
        async for ev in ca._ollama_chat_stream(
            client,
            "http://ollama.test",
            model="m",
            messages=[{"role": "user", "content": "x"}],
            tools=None,
            emit_tokens=True,
        ):
            events.append(ev)
        thinking = [e["content"] for e in events if e.get("type") == "thinking"]
        tokens = [e["content"] for e in events if e.get("type") == "token"]
        self.assertEqual(thinking, ["Let me ", "reason."])
        self.assertEqual(tokens, ["Hi"])
        msg = [e for e in events if e.get("type") == "ollama_message"][0]["message"]
        self.assertEqual(msg["content"], "Hi")
        self.assertEqual(msg.get("thinking"), "Let me reason.")
        self.assertNotIn("reason", msg["content"])

    async def test_emit_tokens_false_still_yields_thinking(self):
        chunks = [
            {
                "message": {
                    "role": "assistant",
                    "thinking": "plan",
                    "content": "secret",
                },
                "done": False,
            },
            {"message": {"role": "assistant", "content": ""}, "done": True},
        ]
        client = AsyncMock()
        client.stream = MagicMock(return_value=_mock_stream_response(chunks))
        events = []
        async for ev in ca._ollama_chat_stream(
            client,
            "http://ollama.test",
            model="m",
            messages=[{"role": "user", "content": "x"}],
            tools=None,
            emit_tokens=False,
        ):
            events.append(ev)
        self.assertTrue(
            any(e.get("type") == "thinking" and e["content"] == "plan" for e in events)
        )
        self.assertFalse(any(e.get("type") == "token" for e in events))
        self.assertEqual(events[-1]["message"]["content"], "secret")
        self.assertEqual(events[-1]["message"].get("thinking"), "plan")

    async def test_cancel_during_stream_raises_chat_cancelled(self):
        chunks = [
            {"message": {"role": "assistant", "content": "A"}, "done": False},
            {"message": {"role": "assistant", "content": "B"}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True},
        ]
        client = AsyncMock()
        client.stream = MagicMock(return_value=_mock_stream_response(chunks))
        cancel = asyncio.Event()

        async def _slow_lines():
            yield json.dumps(chunks[0])
            cancel.set()
            yield json.dumps(chunks[1])
            yield json.dumps(chunks[2])

        resp = MagicMock()
        resp.status_code = 200
        resp.aiter_lines = _slow_lines
        resp.aclose = AsyncMock()

        class _CM:
            async def __aenter__(self_inner):
                return resp

            async def __aexit__(self_inner, *args):
                return False

        client.stream = MagicMock(return_value=_CM())

        with self.assertRaises(ca.ChatCancelled):
            async for _ in ca._ollama_chat_stream(
                client,
                "http://ollama.test",
                model="m",
                messages=[{"role": "user", "content": "x"}],
                tools=None,
                cancel=cancel,
                emit_tokens=True,
            ):
                pass

    async def test_cancel_turn_pendings_resolves_future(self):
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        pid = "test-pending-1"
        ca._PENDING[pid] = {
            "future": fut,
            "kind": "write",
            "created": __import__("time").time(),
            "roots": [],
        }
        ca.cancel_turn_pendings([pid])
        self.assertTrue(fut.done())
        self.assertEqual(fut.result()["deny"], True)
        self.assertEqual(fut.result()["reason"], "cancelled")
        self.assertNotIn(pid, ca._PENDING)

    async def test_await_user_returns_cancelled_when_event_set(self):
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        cancel = asyncio.Event()
        cancel.set()
        result = await ca._await_user(fut, timeout=5, cancel=cancel)
        self.assertTrue(result.get("deny"))
        self.assertEqual(result.get("reason"), "cancelled")


class IterChatCancelBufferTest(unittest.IsolatedAsyncioTestCase):
    async def test_jev_reply_path_streams_tokens_via_iter_chat(self):
        """Tool-enabled chat uses Jev + streaming Ollama for reply rounds."""
        from unittest.mock import patch

        import jev_picker as jp

        async def fake_stream(*args, **kwargs):
            yield {"type": "token", "content": "Hel"}
            yield {"type": "token", "content": "lo"}
            yield {
                "type": "ollama_message",
                "message": {"role": "assistant", "content": "Hello"},
            }

        with patch.object(
            jp, "pick_next_action", new=AsyncMock(return_value=("reply", 0.9, "jev"))
        ), patch.object(ca, "_ollama_chat_stream", new=fake_stream):
            events = []
            body = {
                "messages": [{"role": "user", "content": "what's the weather in Denver"}],
                "model": "mistral-nemo",
                "workspace_roots": ["/tmp"],
                "tools_mode": "always",
            }
            async for ev in ca.iter_chat_events(body, "http://ollama.test"):
                events.append(ev)

        self.assertTrue(any(e.get("type") == "decision_pick" for e in events))
        token_texts = [e.get("content") for e in events if e.get("type") == "token"]
        self.assertEqual(token_texts, ["Hel", "lo"])
        self.assertTrue(any(e.get("type") == "done" for e in events))

    async def test_jev_empty_reply_retries_then_emits_text(self):
        """Thinking-only first reply should nudge once and stream real content."""
        from unittest.mock import patch

        import jev_picker as jp

        n = {"calls": 0}

        async def fake_stream(*args, **kwargs):
            n["calls"] += 1
            if n["calls"] == 1:
                yield {"type": "thinking", "content": "planning only"}
                yield {
                    "type": "ollama_message",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "thinking": "planning only",
                    },
                }
            else:
                yield {"type": "token", "content": "Audit findings here"}
                yield {
                    "type": "ollama_message",
                    "message": {
                        "role": "assistant",
                        "content": "Audit findings here",
                    },
                }

        with patch.object(
            jp, "pick_next_action", new=AsyncMock(return_value=("reply", 0.9, "jev"))
        ), patch.object(ca, "_ollama_chat_stream", new=fake_stream):
            events = []
            body = {
                "messages": [{"role": "user", "content": "audit please"}],
                "model": "qwen",
                "workspace_roots": ["/tmp"],
                "tools_mode": "always",
            }
            async for ev in ca.iter_chat_events(body, "http://ollama.test"):
                events.append(ev)

        self.assertEqual(n["calls"], 2)
        token_texts = [e.get("content") for e in events if e.get("type") == "token"]
        self.assertEqual(token_texts, ["Audit findings here"])
        done = [e for e in events if e.get("type") == "done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0].get("message"), "Audit findings here")


if __name__ == "__main__":
    unittest.main()
