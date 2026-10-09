import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import chat_sessions as cs

class ChatSessionsTest(unittest.TestCase):
    def setUp(self):
        self._td = __import__("tempfile").TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_create_list_get_delete(self):
        s = cs.create_chat({"model": "m", "workspace_roots": ["/tmp"]}, root=self.root)
        self.assertRegex(s["id"], cs.ID_RE.pattern)
        self.assertEqual(s["model"], "m")
        listed = cs.list_chats(root=self.root)
        self.assertEqual(len(listed), 1)
        self.assertEqual(cs.get_chat(s["id"], root=self.root)["id"], s["id"])
        cs.delete_chat(s["id"], root=self.root)
        self.assertEqual(cs.list_chats(root=self.root), [])

    def test_put_updates_index_title_from_first_user(self):
        s = cs.create_chat(root=self.root)
        s["messages"] = [{"role": "user", "content": "hello world from audit"}]
        s["model"] = "qwen"
        out = cs.put_chat(s["id"], s, root=self.root)
        self.assertIn("hello world", out["title"])
        self.assertEqual(cs.list_chats(root=self.root)[0]["title"], out["title"])

    def test_normalize_strips_pendings(self):
        msgs = [
            {"role": "user", "content": "x"},
            {"kind": "write_pending", "id": "1"},
            {"kind": "thinking", "content": "t", "streaming": True, "collapsed": False},
            {"kind": "writes_batch_pending", "awaitingDecision": True, "items": []},
            {"role": "assistant", "content": "y"},
        ]
        out = cs.normalize_messages(msgs)
        kinds = [m.get("kind") for m in out]
        self.assertNotIn("write_pending", kinds)
        self.assertNotIn("writes_batch_pending", kinds)
        th = next(m for m in out if m.get("kind") == "thinking")
        self.assertFalse(th["streaming"])
        self.assertTrue(th["collapsed"])

    def test_bad_id_rejected(self):
        with self.assertRaises(ValueError):
            cs.get_chat("../etc/passwd", root=self.root)

    def test_patch_model(self):
        s = cs.create_chat(root=self.root)
        out = cs.patch_chat(s["id"], {"model": "nemo"}, root=self.root)
        self.assertEqual(out["model"], "nemo")

    def test_put_preserves_created_at_when_omitted(self):
        s = cs.create_chat(root=self.root)
        created = s["created_at"]
        payload = {
            "id": s["id"],
            "title": "",
            "model": "m",
            "workspace_roots": [],
            "resume": s["resume"],
            "messages": [{"role": "user", "content": "hi"}],
        }
        out = cs.put_chat(s["id"], payload, root=self.root)
        self.assertEqual(out["created_at"], created)
        self.assertEqual(cs.get_chat(s["id"], root=self.root)["created_at"], created)

    def test_create_defaults_run_everything_true(self):
        s = cs.create_chat(root=self.root)
        self.assertTrue(s.get("run_everything"))

    def test_create_respects_run_everything_false(self):
        s = cs.create_chat({"run_everything": False}, root=self.root)
        self.assertFalse(s["run_everything"])

    def test_get_coerces_missing_run_everything_true(self):
        s = cs.create_chat(root=self.root)
        path = self.root / f"{s['id']}.json"
        data = __import__("json").loads(path.read_text())
        data.pop("run_everything", None)
        path.write_text(__import__("json").dumps(data))
        got = cs.get_chat(s["id"], root=self.root)
        self.assertTrue(got["run_everything"])

    def test_patch_run_everything(self):
        s = cs.create_chat(root=self.root)
        out = cs.patch_chat(s["id"], {"run_everything": False}, root=self.root)
        self.assertFalse(out["run_everything"])


if __name__ == "__main__":
    unittest.main()
