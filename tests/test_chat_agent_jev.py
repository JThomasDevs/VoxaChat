import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import chat_agent as ca  # noqa: E402
import jev_picker as jp  # noqa: E402


async def _collect(body, ollama_url="http://ollama"):
    return [ev async for ev in ca.iter_chat_events(body, ollama_url)]


def _stream_mock(message, tokens=None):
    async def fake_stream(*args, **kwargs):
        for t in tokens or []:
            yield {"type": "token", "content": t}
        yield {"type": "ollama_message", "message": message}

    return fake_stream


class ChatAgentJevTest(unittest.IsolatedAsyncioTestCase):
    async def test_tools_off_skips_jev(self):
        stream_called = False

        async def mark_stream(*args, **kwargs):
            nonlocal stream_called
            stream_called = True
            gen = _stream_mock({"role": "assistant", "content": "hi"})
            async for ev in gen(*args, **kwargs):
                yield ev

        with patch.object(
            ca, "_ollama_chat_stream", new=mark_stream
        ), patch.object(jp, "pick_next_action", new=AsyncMock()) as pick:
            evs = await _collect(
                {
                    "messages": [{"role": "user", "content": "read README.md"}],
                    "tools_mode": "off",
                    "workspace_roots": [],
                }
            )
            pick.assert_not_called()
            self.assertTrue(stream_called)
            self.assertTrue(any(e.get("type") == "done" for e in evs))

    async def test_jev_error_hard_stops(self):
        with patch.object(
            jp,
            "pick_next_action",
            new=AsyncMock(side_effect=jp.JevPickerError("Jev unreachable: boom")),
        ), patch.object(ca, "_ollama_chat_stream", new=AsyncMock()) as ollama:
            evs = await _collect(
                {
                    "messages": [{"role": "user", "content": "read README.md"}],
                    "tools_mode": "always",
                    "workspace_roots": [],
                }
            )
            ollama.assert_not_called()
            err = next(e for e in evs if e.get("type") == "error")
            self.assertIn("Jev", err["message"])

    async def test_reply_path_ollama_without_tools(self):
        stream_calls = []

        async def tracking_stream(*args, **kwargs):
            stream_calls.append(kwargs)
            gen = _stream_mock({"role": "assistant", "content": "hello"})
            async for ev in gen(*args, **kwargs):
                yield ev

        with patch.object(
            jp, "pick_next_action", new=AsyncMock(return_value=("reply", 0.9, "jev"))
        ), patch.object(ca, "_ollama_chat_stream", new=tracking_stream):
            evs = await _collect(
                {
                    "messages": [{"role": "user", "content": "hi there"}],
                    "tools_mode": "always",
                    "workspace_roots": [],
                }
            )
        self.assertEqual(stream_calls[0].get("tools"), None)
        self.assertTrue(
            any(
                e.get("type") == "decision_pick" and e.get("option") == "reply"
                for e in evs
            )
        )

    async def test_single_tool_schema_on_arg_fill(self):
        read_schema = next(
            s for s in ca.ft.TOOL_SCHEMAS if s["function"]["name"] == "read_file"
        )
        stream_calls = []
        stream_round = 0

        async def tool_stream(*args, **kwargs):
            nonlocal stream_round
            stream_calls.append(kwargs)
            stream_round += 1
            if stream_round == 1:
                yield {
                    "type": "ollama_message",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "read_file",
                                    "arguments": {"path": "/tmp/foo.txt"},
                                },
                            }
                        ],
                    },
                }
            else:
                yield {
                    "type": "ollama_message",
                    "message": {"role": "assistant", "content": "done"},
                }

        with patch.object(
            jp,
            "pick_next_action",
            new=AsyncMock(side_effect=[("read_file", 0.85, "jev"), ("reply", 0.9, "jev")]),
        ), patch.object(ca, "_ollama_chat_stream", new=tool_stream), patch.object(
            ca.ft, "run_tool", return_value="file body"
        ):
            evs = await _collect(
                {
                    "messages": [{"role": "user", "content": "read README.md"}],
                    "tools_mode": "always",
                    "workspace_roots": ["/tmp"],
                }
            )

        tools = stream_calls[0].get("tools") or []
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0], read_schema)
        self.assertTrue(any(e.get("type") == "tool_start" for e in evs))

    async def test_sensitive_deny_skips_ollama(self):
        stream_calls = []

        async def reply_stream(*args, **kwargs):
            stream_calls.append(kwargs)
            yield {
                "type": "ollama_message",
                "message": {"role": "assistant", "content": "ok"},
            }

        async def auto_deny():
            for _ in range(50):
                await asyncio.sleep(0.01)
                for pid, entry in list(ca._PENDING.items()):
                    if entry.get("kind") == "tool_permission":
                        ca.resolve_tool_permission(pid, False)
                        return
            raise AssertionError("no tool_permission pending")

        with patch.object(
            jp, "pick_next_action", new=AsyncMock(return_value=("delete_file", 0.7, "jev"))
        ), patch.object(ca, "_ollama_chat_stream", new=reply_stream):
            task = asyncio.create_task(auto_deny())
            evs = await _collect(
                {
                    "messages": [{"role": "user", "content": "delete bad.txt"}],
                    "tools_mode": "always",
                    "workspace_roots": [],
                }
            )
            await task

        self.assertTrue(any(e.get("type") == "tool_permission_pending" for e in evs))
        for kwargs in stream_calls:
            tools = kwargs.get("tools")
            self.assertTrue(
                tools is None
                or all(t["function"]["name"] != "delete_file" for t in (tools or []))
            )

    async def test_decision_pick_includes_backend(self):
        with patch.object(
            jp, "pick_next_action", new=AsyncMock(return_value=("reply", 0.9, "tev1"))
        ), patch.object(
            ca,
            "_ollama_chat_stream",
            new=_stream_mock({"role": "assistant", "content": "hi"}),
        ):
            evs = await _collect(
                {
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools_mode": "always",
                    "workspace_roots": [],
                }
            )
        pick = next(e for e in evs if e.get("type") == "decision_pick")
        self.assertEqual(pick.get("backend"), "tev1")

    def test_chat_tools_env_off_values(self):
        old = os.environ.get("VOXA_CHAT_TOOLS")
        try:
            for val in ("false", "0", "off", "none"):
                with self.subTest(val=val):
                    os.environ["VOXA_CHAT_TOOLS"] = val
                    self.assertTrue(ca._chat_tools_env_off())
            os.environ["VOXA_CHAT_TOOLS"] = "true"
            self.assertFalse(ca._chat_tools_env_off())
        finally:
            if old is None:
                os.environ.pop("VOXA_CHAT_TOOLS", None)
            else:
                os.environ["VOXA_CHAT_TOOLS"] = old

    async def test_voxa_chat_tools_env_off_skips_picker_and_tools(self):
        old = os.environ.get("VOXA_CHAT_TOOLS")
        env_values = ("false", "0", "off", "none")
        try:
            for env_val in env_values:
                with self.subTest(env_val=env_val):
                    os.environ["VOXA_CHAT_TOOLS"] = env_val
                    picker = AsyncMock()
                    stream_calls = []

                    async def tracking_stream(*args, **kwargs):
                        stream_calls.append(kwargs)
                        gen = _stream_mock({"role": "assistant", "content": "plain"})
                        async for ev in gen(*args, **kwargs):
                            yield ev

                    with patch.object(jp, "pick_next_action", new=picker), patch.object(
                        ca,
                        "_ollama_chat_stream",
                        new=tracking_stream,
                    ):
                        evs = await _collect(
                            {
                                "messages": [
                                    {"role": "user", "content": "read README.md"}
                                ],
                                "tools_mode": "always",
                                "workspace_roots": [],
                            }
                        )
                    picker.assert_not_called()
                    self.assertFalse(
                        any(e.get("type") == "decision_pick" for e in evs)
                    )
                    self.assertEqual(len(stream_calls), 1)
                    self.assertIsNone(stream_calls[0].get("tools"))
        finally:
            if old is None:
                os.environ.pop("VOXA_CHAT_TOOLS", None)
            else:
                os.environ["VOXA_CHAT_TOOLS"] = old


if __name__ == "__main__":
    unittest.main()
