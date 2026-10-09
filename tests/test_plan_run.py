import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import plan_run as pr  # noqa: E402


class PlanRunTest(unittest.TestCase):
    def test_normalize_todos_defaults(self):
        out = pr.normalize_todos([{"label": "A"}, {"id": "2", "label": "B", "status": "done"}])
        self.assertEqual(out[0]["status"], "pending")
        self.assertTrue(out[0]["id"])
        self.assertEqual(out[1]["status"], "done")

    def test_mutating_held_by_mode(self):
        s = pr.RunState(mode="awaiting_approval")
        self.assertTrue(pr.mutating_held(s))
        s2 = pr.RunState(mode="implementing", plan_approved=True)
        self.assertFalse(pr.mutating_held(s2))
        s3 = pr.RunState(mode="normal")
        self.assertFalse(pr.mutating_held(s3))

    def test_force_reply_only_when_not_implementing_approved(self):
        s = pr.RunState(mode="implementing", plan_approved=True)
        self.assertFalse(pr.should_force_reply_after_tool(s))
        s2 = pr.RunState(mode="normal")
        self.assertTrue(pr.should_force_reply_after_tool(s2))

    def test_transitions(self):
        s = pr.RunState()
        pr.apply_clarify(s)
        self.assertEqual(s.mode, "clarifying")
        pr.apply_plan_proposed(s, "Do X", [{"id": "1", "label": "X", "status": "pending"}])
        self.assertEqual(s.mode, "awaiting_approval")
        self.assertFalse(s.plan_approved)
        pr.apply_plan_approved(s)
        self.assertEqual(s.mode, "implementing")
        self.assertTrue(s.plan_approved)
        pr.apply_plan_revise(s)
        self.assertEqual(s.mode, "planning")
        self.assertFalse(s.plan_approved)

    def test_cancel_in_progress_todos(self):
        s = pr.RunState(
            todos=[
                {"id": "1", "label": "a", "status": "done"},
                {"id": "2", "label": "b", "status": "in_progress"},
            ]
        )
        pr.cancel_in_progress_todos(s)
        self.assertEqual(s.todos[0]["status"], "done")
        self.assertEqual(s.todos[1]["status"], "cancelled")

    def test_control_schemas_have_propose_and_update(self):
        names = {t["function"]["name"] for t in pr.control_tool_schemas()}
        self.assertEqual(names, {"propose_plan", "update_todo"})

    def test_max_rounds_implement(self):
        s = pr.RunState(mode="implementing", plan_approved=True)
        self.assertEqual(pr.max_rounds_for(s), pr.MAX_ROUNDS_IMPLEMENT)
        self.assertEqual(pr.max_rounds_for(pr.RunState()), pr.MAX_ROUNDS_DEFAULT)

    def test_apply_todo_update_preserves_labels_on_status_only(self):
        s = pr.RunState()
        pr.apply_plan_proposed(
            s,
            "plan",
            [
                {"id": "t1", "label": "Write a.txt", "status": "in_progress"},
                {"id": "t2", "label": "Write b.txt", "status": "pending"},
            ],
        )
        pr.apply_todo_update(s, [{"id": "t1", "status": "done"}])
        self.assertEqual(
            [(t["id"], t["label"], t["status"]) for t in s.todos],
            [("t1", "Write a.txt", "done"), ("t2", "Write b.txt", "pending")],
        )
        # Label-only update keeps prior status; new ids append.
        pr.apply_todo_update(
            s,
            [{"id": "t2", "label": "Write c.txt"}, {"id": "t3", "label": "New"}],
        )
        self.assertEqual(
            [(t["id"], t["label"], t["status"]) for t in s.todos],
            [
                ("t1", "Write a.txt", "done"),
                ("t2", "Write c.txt", "pending"),
                ("t3", "New", "pending"),
            ],
        )

    def test_ask_clarify_is_text(self):
        self.assertTrue(pr.ask_clarify_is_text())

    def test_run_state_from_body_implementing(self):
        s = pr.run_state_from_body(
            {
                "mode": "implementing",
                "plan_approved": True,
                "plan_summary": "Do X",
                "todos": [{"id": "1", "label": "X", "status": "in_progress"}],
            }
        )
        self.assertEqual(s.mode, "implementing")
        self.assertTrue(s.plan_approved)
        self.assertEqual(s.plan_summary, "Do X")
        self.assertEqual(s.todos[0]["id"], "1")

    def test_run_state_from_body_bad_mode_defaults(self):
        s = pr.run_state_from_body({"mode": "nope", "plan_approved": True})
        self.assertEqual(s.mode, "normal")
        self.assertFalse(s.plan_approved)

    def test_run_state_from_body_implementing_implies_approved(self):
        s = pr.run_state_from_body({"mode": "implementing"})
        self.assertTrue(s.plan_approved)

    def test_run_state_from_body_awaiting(self):
        s = pr.run_state_from_body(
            {
                "mode": "awaiting_approval",
                "plan_approved": False,
                "todos": [{"label": "A"}],
            }
        )
        self.assertEqual(s.mode, "awaiting_approval")
        self.assertTrue(pr.mutating_held(s))
        self.assertEqual(len(s.todos), 1)

    def test_run_state_from_body_none(self):
        s = pr.run_state_from_body(None)
        self.assertEqual(s.mode, "normal")
