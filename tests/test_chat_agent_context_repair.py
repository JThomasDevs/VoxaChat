import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import context_repair as cr  # noqa: E402


def _asst_explore(name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": name, "arguments": args}}],
    }


def _tool(name: str, content: str) -> dict:
    return {"role": "tool", "name": name, "content": content}


class ChatAgentHookContractTest(unittest.TestCase):
    def test_chat_agent_uses_shared_context_repair_module(self):
        import chat_agent as ca

        self.assertIs(ca.cr, cr)

    def test_maybe_collapse_explore_collapses_failed_streak(self):
        import chat_agent as ca

        msgs = [
            {"role": "user", "content": "find barrows"},
            _asst_explore("list_dir", {"path": "/bad"}),
            _tool("list_dir", "error: outside_workspace: /bad"),
            _asst_explore("list_dir", {"path": "/good"}),
            _tool("list_dir", "ok\n"),
        ]
        before_len = len(msgs)
        ca._maybe_collapse_explore(msgs)
        self.assertLess(len(msgs), before_len)
        self.assertTrue(
            any(
                str(m.get("content") or "").startswith(cr.PATH_REPAIR_PREFIX)
                for m in msgs
            )
        )
        self.assertEqual(len([m for m in msgs if m.get("role") == "tool"]), 1)

    def test_maybe_collapse_explore_swallows_repair_exceptions(self):
        import chat_agent as ca

        msgs = [{"role": "user", "content": "x"}]
        original = cr.collapse_explore_failures

        def boom(_msgs):
            raise RuntimeError("repair bug")

        cr.collapse_explore_failures = boom
        try:
            ca._maybe_collapse_explore(msgs)
        finally:
            cr.collapse_explore_failures = original
        self.assertEqual(msgs, [{"role": "user", "content": "x"}])

    def test_chat_agent_source_wires_collapse_at_call_sites(self):
        import chat_agent as ca

        src = Path(ca.__file__).read_text(encoding="utf-8")
        self.assertIn("collapse_explore_failures", src)
        self.assertIn("import context_repair", src)


if __name__ == "__main__":
    unittest.main()
