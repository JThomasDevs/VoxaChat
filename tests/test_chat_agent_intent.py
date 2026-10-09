import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import chat_agent as ca  # noqa: E402


class ChatAgentIntentTest(unittest.TestCase):
    def test_offers_tools_for_natural_asks(self):
        should = [
            "what's the weather in Denver",
            "run pytest",
            "search the web for fastapi sse",
            "look up python pathlib",
            "git status please",
            "fix the bug in server.py",
            "install the dependencies",
            "what's in the repo",
            "read README.md",
            "how do I configure ollama tools",
            "https://example.com/docs",
        ]
        for text in should:
            with self.subTest(text=text):
                self.assertTrue(
                    ca._TOOL_INTENT_RE.search(text),
                    f"expected tool intent for: {text!r}",
                )
                self.assertTrue(
                    ca._should_offer_tools("when_asked", [{"role": "user", "content": text}])
                )

    def test_skips_chit_chat(self):
        for text in ("hello how are you", "thanks!", "what do you think about jazz"):
            with self.subTest(text=text):
                self.assertFalse(ca._TOOL_INTENT_RE.search(text))
                self.assertFalse(
                    ca._should_offer_tools("when_asked", [{"role": "user", "content": text}])
                )

    def test_system_prompt_plain_includes_clock(self):
        prompt = ca._system_prompt_for_turn(
            [{"role": "user", "content": "hi"}],
            tools=False,
        )
        self.assertIn("Current date/time:", prompt)
        self.assertIn("Never invent calendar dates", prompt)

    def test_explicit_mode_aliases_control_tools_gate(self):
        messages = [{"role": "user", "content": "hello"}]
        for mode in ("always", "on", "true", "1"):
            with self.subTest(mode=mode):
                self.assertTrue(ca._should_offer_tools(mode, messages))
        for mode in ("off", "none", "false", "0"):
            with self.subTest(mode=mode):
                self.assertFalse(ca._should_offer_tools(mode, messages))


if __name__ == "__main__":
    unittest.main()
