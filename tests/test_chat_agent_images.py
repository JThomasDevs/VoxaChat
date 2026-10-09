"""Image pass-through and reject tests for chat_agent."""
from __future__ import annotations

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


RAW_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
DATA_URL = f"data:image/png;base64,{RAW_B64}"


class NormalizeImagesHelpersTest(unittest.TestCase):
    def test_normalize_strips_non_user_and_data_url_prefix(self):
        msgs = [
            {"role": "system", "content": "s", "images": ["x"]},
            {"role": "user", "content": "u", "images": [DATA_URL, "  "]},
            {"role": "assistant", "content": "a", "images": ["y"]},
        ]
        out = ca._normalize_messages_images(msgs)
        self.assertNotIn("images", out[0])
        self.assertEqual(out[1]["images"], [RAW_B64])
        self.assertNotIn("images", out[2])

    def test_reject_non_vision_model(self):
        msgs = [{"role": "user", "content": "x", "images": [RAW_B64]}]
        err = ca._reject_images_if_unsupported("mistral-nemo", msgs)
        self.assertEqual(err["type"], "error")
        self.assertIn("vision", err["message"].lower())

    def test_reject_allows_vision_model(self):
        msgs = [{"role": "user", "content": "x", "images": [RAW_B64]}]
        self.assertIsNone(ca._reject_images_if_unsupported("llava:latest", msgs))

    def test_text_only_never_rejects_non_vision(self):
        """Regression: non-vision + text-only must not error on the image path."""
        cases = [
            [{"role": "user", "content": "hello"}],
            [{"role": "user", "content": "hello", "images": []}],
            [{"role": "user", "content": "hello", "images": ["", "  "]}],
            [{"role": "user", "content": "hello", "images": None}],
            # bare string must not be treated as image payloads
            [{"role": "user", "content": "hello", "images": RAW_B64}],
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "yo"},
                {"role": "user", "content": "follow up"},
            ],
        ]
        for msgs in cases:
            with self.subTest(msgs=msgs):
                norm = ca._normalize_messages_images(msgs)
                self.assertFalse(ca._user_messages_have_images(norm))
                self.assertIsNone(
                    ca._reject_images_if_unsupported("mistral-nemo", norm)
                )


class ChatAgentImagesIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_images_non_vision_yields_error_no_ollama(self):
        with patch.object(ca, "_ollama_chat_stream", new=AsyncMock()) as ollama, patch.object(
            jp, "pick_next_action", new=AsyncMock()
        ) as pick:
            evs = await _collect(
                {
                    "messages": [
                        {"role": "user", "content": "what is this?", "images": [DATA_URL]}
                    ],
                    "model": "mistral-nemo",
                    "tools_mode": "off",
                    "workspace_roots": [],
                }
            )
            ollama.assert_not_called()
            pick.assert_not_called()
            self.assertEqual(len(evs), 1)
            self.assertEqual(evs[0]["type"], "error")

    async def test_images_vision_model_forwards_normalized_b64(self):
        stream_calls = []

        async def tracking_stream(*args, **kwargs):
            stream_calls.append(kwargs)
            gen = _stream_mock({"role": "assistant", "content": "a cat"})
            async for ev in gen(*args, **kwargs):
                yield ev

        with patch.object(ca, "_ollama_chat_stream", new=tracking_stream):
            evs = await _collect(
                {
                    "messages": [
                        {"role": "user", "content": "what is this?", "images": [DATA_URL]}
                    ],
                    "model": "llava",
                    "tools_mode": "off",
                    "workspace_roots": [],
                }
            )
        self.assertTrue(any(e.get("type") == "done" for e in evs))
        self.assertEqual(len(stream_calls), 1)
        posted = stream_calls[0]["messages"]
        user_msgs = [m for m in posted if m.get("role") == "user"]
        self.assertTrue(user_msgs)
        self.assertEqual(user_msgs[-1].get("images"), [RAW_B64])

    async def test_jev_path_rejects_images_on_non_vision(self):
        with patch.object(ca, "_ollama_chat_stream", new=AsyncMock()) as ollama, patch.object(
            jp, "pick_next_action", new=AsyncMock()
        ) as pick:
            evs = await _collect(
                {
                    "messages": [
                        {"role": "user", "content": "pic", "images": [RAW_B64]}
                    ],
                    "model": "mistral-nemo",
                    "tools_mode": "always",
                    "workspace_roots": [],
                }
            )
            ollama.assert_not_called()
            pick.assert_not_called()
            self.assertEqual(evs[0]["type"], "error")

    async def test_text_only_non_vision_no_image_path_error(self):
        """Regression: text-only + non-vision must not yield the images error."""
        stream_calls = []

        async def tracking_stream(*args, **kwargs):
            stream_calls.append(kwargs)
            gen = _stream_mock({"role": "assistant", "content": "ok"})
            async for ev in gen(*args, **kwargs):
                yield ev

        with patch.object(ca, "_ollama_chat_stream", new=tracking_stream):
            evs = await _collect(
                {
                    "messages": [{"role": "user", "content": "hello there"}],
                    "model": "mistral-nemo",
                    "tools_mode": "off",
                    "workspace_roots": [],
                }
            )
        self.assertFalse(
            any(
                e.get("type") == "error"
                and "image" in str(e.get("message", "")).lower()
                for e in evs
            )
        )
        self.assertTrue(any(e.get("type") == "done" for e in evs))
        self.assertEqual(len(stream_calls), 1)
        posted = stream_calls[0]["messages"]
        for m in posted:
            if m.get("role") == "user":
                self.assertNotIn("images", m)

    async def test_text_only_non_vision_jev_no_image_path_error(self):
        """Regression: Jev path also allows text-only on non-vision models."""
        stream_calls = []

        async def tracking_stream(*args, **kwargs):
            stream_calls.append(kwargs)
            gen = _stream_mock({"role": "assistant", "content": "ok"})
            async for ev in gen(*args, **kwargs):
                yield ev

        with patch.object(ca, "_ollama_chat_stream", new=tracking_stream), patch.object(
            jp, "pick_next_action", new=AsyncMock(return_value=("reply", 0.9, "jev"))
        ):
            evs = await _collect(
                {
                    "messages": [{"role": "user", "content": "just text"}],
                    "model": "mistral-nemo",
                    "tools_mode": "always",
                    "workspace_roots": [],
                }
            )
        self.assertFalse(
            any(
                e.get("type") == "error"
                and "image" in str(e.get("message", "")).lower()
                for e in evs
            )
        )
        self.assertTrue(any(e.get("type") == "done" for e in evs))
        self.assertGreaterEqual(len(stream_calls), 1)


if __name__ == "__main__":
    unittest.main()
