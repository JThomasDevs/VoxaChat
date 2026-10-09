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


class ContextRepairTest(unittest.TestCase):
    def test_collapse_removes_failed_streak_keeps_success(self):
        msgs = [
            {"role": "user", "content": "audit barrows"},
            _asst_explore("list_dir", {"path": "/wrong/barrows"}),
            _tool("list_dir", "error: outside_workspace: /wrong/barrows"),
            _asst_explore("list_dir", {"path": "/also/wrong"}),
            _tool("list_dir", "error: not_a_directory: /also/wrong"),
            _asst_explore("list_dir", {"path": "/real/plugins/barrows"}),
            _tool("list_dir", "BarrowsPlugin.java\n"),
        ]
        changed = cr.collapse_explore_failures(msgs)
        self.assertTrue(changed)
        names = [(m.get("role"), m.get("name"), (m.get("content") or "")[:40]) for m in msgs]
        # Failed pairs gone; success kept; one repair note inserted.
        tool_results = [m for m in msgs if m.get("role") == "tool"]
        self.assertEqual(len(tool_results), 1)
        self.assertEqual(tool_results[0]["content"], "BarrowsPlugin.java\n")
        repair = [
            m
            for m in msgs
            if m.get("role") == "user"
            and str(m.get("content") or "").startswith(cr.PATH_REPAIR_PREFIX)
        ]
        self.assertEqual(len(repair), 1)
        body = repair[0]["content"]
        self.assertIn("collapsed 2 failed explore steps", body)
        self.assertIn("Working: /real/plugins/barrows", body)
        self.assertIn("Do not retry:", body)
        self.assertIn("/wrong/barrows", body)
        self.assertIn("/also/wrong", body)
        # Original user ask preserved.
        self.assertEqual(msgs[0]["content"], "audit barrows")

    def test_no_collapse_when_only_successes(self):
        msgs = [
            {"role": "user", "content": "list src"},
            _asst_explore("list_dir", {"path": "src"}),
            _tool("list_dir", "a.py\n"),
        ]
        before = list(msgs)
        self.assertFalse(cr.collapse_explore_failures(msgs))
        self.assertEqual(msgs, before)

    def test_idempotent_second_pass(self):
        msgs = [
            {"role": "user", "content": "find x"},
            _asst_explore("list_dir", {"path": "/bad"}),
            _tool("list_dir", "error: outside_workspace: /bad"),
            _asst_explore("list_dir", {"path": "/good"}),
            _tool("list_dir", "ok\n"),
        ]
        self.assertTrue(cr.collapse_explore_failures(msgs))
        snapshot = [dict(m) for m in msgs]
        self.assertFalse(cr.collapse_explore_failures(msgs))
        self.assertEqual(msgs, snapshot)

    def test_skip_ambiguous_multi_tool_assistant(self):
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "list_dir", "arguments": {"path": "/bad"}}},
                    {"function": {"name": "list_dir", "arguments": {"path": "/also"}}},
                ],
            },
            _tool("list_dir", "error: outside_workspace: /bad"),
            _asst_explore("list_dir", {"path": "/good"}),
            _tool("list_dir", "ok\n"),
        ]
        before = [dict(m) for m in msgs]
        # Ambiguous prior pairing → fail safe, no mutation.
        self.assertFalse(cr.collapse_explore_failures(msgs))
        self.assertEqual(msgs, before)

    def test_never_prunes_non_explore(self):
        msgs = [
            _asst_explore("list_dir", {"path": "/bad"}),
            _tool("list_dir", "error: outside_workspace: /bad"),
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "shell_run", "arguments": {"command": "ls"}}}
                ],
            },
            {"role": "tool", "name": "shell_run", "content": "error: user denied shell_run"},
            _asst_explore("list_dir", {"path": "/good"}),
            _tool("list_dir", "ok\n"),
        ]
        cr.collapse_explore_failures(msgs)
        shell = [m for m in msgs if m.get("name") == "shell_run"]
        self.assertEqual(len(shell), 1)
        self.assertIn("user denied", shell[0]["content"])

    def test_collapse_skips_system_nudge_between_failures_and_success(self):
        nudge = {
            "role": "user",
            "content": (
                "(system) `list_dir` failed 3 times with the same arguments "
                '(path=\'/wrong/barrows\'). Do not retry that exact call.'
            ),
        }
        msgs = [
            {"role": "user", "content": "audit barrows"},
            _asst_explore("list_dir", {"path": "/wrong/barrows"}),
            _tool("list_dir", "error: outside_workspace: /wrong/barrows"),
            _asst_explore("list_dir", {"path": "/wrong/barrows"}),
            _tool("list_dir", "error: outside_workspace: /wrong/barrows"),
            _asst_explore("list_dir", {"path": "/wrong/barrows"}),
            _tool("list_dir", "error: outside_workspace: /wrong/barrows"),
            nudge,
            _asst_explore("list_dir", {"path": "/real/plugins/barrows"}),
            _tool("list_dir", "BarrowsPlugin.java\n"),
        ]
        self.assertTrue(cr.collapse_explore_failures(msgs))
        tool_results = [m for m in msgs if m.get("role") == "tool"]
        self.assertEqual(len(tool_results), 1)
        self.assertIn("BarrowsPlugin", tool_results[0]["content"])
        failed_tools = [
            m
            for m in msgs
            if m.get("role") == "tool" and cr.is_explore_error(str(m.get("content") or ""))
        ]
        self.assertEqual(failed_tools, [])
        self.assertTrue(any(m == nudge for m in msgs))
        repair = [
            m
            for m in msgs
            if str(m.get("content") or "").startswith(cr.PATH_REPAIR_PREFIX)
        ]
        self.assertEqual(len(repair), 1)
        self.assertIn("collapsed 3 failed explore steps", repair[0]["content"])

    def test_user_path_correction_trigger(self):
        msgs = [
            {"role": "user", "content": "audit barrows"},
            _asst_explore("list_dir", {"path": "/wrong/barrows"}),
            _tool("list_dir", "error: outside_workspace: /wrong/barrows"),
            {
                "role": "user",
                "content": "that's not where barrows is — use /real/plugins/barrows",
            },
        ]
        self.assertTrue(cr.looks_like_path_correction(msgs[-1]["content"]))
        changed = cr.collapse_explore_failures(msgs)
        self.assertTrue(changed)
        tools = [m for m in msgs if m.get("role") == "tool"]
        self.assertEqual(tools, [])
        repair = [
            m
            for m in msgs
            if str(m.get("content") or "").startswith(cr.PATH_REPAIR_PREFIX)
        ]
        self.assertEqual(len(repair), 1)
        self.assertIn("/real/plugins/barrows", repair[0]["content"])
        # Correction message kept.
        self.assertTrue(
            any("that's not where barrows is" in str(m.get("content") or "") for m in msgs)
        )


if __name__ == "__main__":
    unittest.main()
