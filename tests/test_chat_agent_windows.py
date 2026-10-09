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


class ChatAgentWindowsModeTest(unittest.IsolatedAsyncioTestCase):
    async def test_when_asked_read_still_uses_jev_not_classic_multi_tool(self):
        stream_kwargs = {}

        async def fake_stream(*args, **kwargs):
            stream_kwargs.update(kwargs)
            yield {
                "type": "ollama_message",
                "message": {"role": "assistant", "content": "README contents"},
            }

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VOXA_WINDOWS", None)
            with patch.object(
                jp, "pick_next_action", new=AsyncMock(return_value=("reply", 0.9, "jev"))
            ) as pick:
                with patch.object(ca, "_ollama_chat_stream", new=fake_stream):
                    events = await _collect(
                        {
                            "messages": [
                                {"role": "user", "content": "read README.md"}
                            ],
                            "tools_mode": "when_asked",
                            "workspace_roots": [],
                        }
                    )

        pick.assert_awaited_once()
        self.assertIsNone(stream_kwargs.get("tools"))
        self.assertTrue(any(event.get("type") == "decision_pick" for event in events))
        self.assertTrue(any(event.get("type") == "done" for event in events))

    async def test_windows_tools_always_calls_jev(self):
        with patch.object(
            jp, "pick_next_action", new=AsyncMock(return_value=("reply", 0.9, "jev"))
        ) as pick:
            with patch.object(
                ca,
                "_ollama_chat_stream",
                new=_stream_mock({"role": "assistant", "content": "hello"}),
            ):
                await _collect(
                    {
                        "messages": [{"role": "user", "content": "hello"}],
                        "tools_mode": "always",
                        "workspace_roots": [],
                        "windows": True,
                    }
                )

        pick.assert_awaited_once()

    async def test_windows_tools_off_skips_jev_and_uses_plain_ollama(self):
        stream_kwargs = {}

        async def fake_stream(*args, **kwargs):
            stream_kwargs.update(kwargs)
            yield {
                "type": "ollama_message",
                "message": {"role": "assistant", "content": "hello"},
            }

        with patch.object(jp, "pick_next_action", new=AsyncMock()) as pick:
            with patch.object(ca, "_ollama_chat_stream", new=fake_stream):
                await _collect(
                    {
                        "messages": [
                            {"role": "user", "content": "read README.md"}
                        ],
                        "tools_mode": "off",
                        "workspace_roots": [],
                        "windows": True,
                    }
                )

        pick.assert_not_called()
        self.assertIsNone(stream_kwargs.get("tools"))

    async def test_windows_when_asked_still_shows_decision_pick_for_chitchat(self):
        """Casual chat must still call Jev and emit decision_pick=reply."""
        with patch.object(
            jp, "pick_next_action", new=AsyncMock(return_value=("reply", 0.88, "jev"))
        ) as pick, patch.object(
            ca,
            "_ollama_chat_stream",
            new=_stream_mock({"role": "assistant", "content": "hi!"}),
        ):
            evs = await _collect(
                {
                    "messages": [{"role": "user", "content": "hello how are you"}],
                    "tools_mode": "when_asked",
                    "workspace_roots": [],
                    "windows": True,
                }
            )
        pick.assert_awaited_once()
        self.assertTrue(
            any(
                e.get("type") == "decision_pick"
                and e.get("option") == "reply"
                and e.get("confidence") == 0.88
                for e in evs
            )
        )
        self.assertTrue(any(e.get("type") == "done" for e in evs))


if __name__ == "__main__":
    unittest.main()
