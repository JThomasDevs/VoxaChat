import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import write_batch as wb  # noqa: E402


class WriteBatchTest(unittest.TestCase):
    def test_enqueue_and_same_path(self):
        b = wb.new_batch()
        a = wb.try_enqueue(
            b,
            tool="write_file",
            args={"path": "/tmp/a.txt", "content": "x"},
            preview="diff-a",
        )
        self.assertEqual(a.path, "/tmp/a.txt")
        self.assertEqual(len(b.items), 1)
        with self.assertRaises(ValueError) as ctx:
            wb.try_enqueue(
                b,
                tool="write_file",
                args={"path": "/tmp/a.txt", "content": "y"},
                preview="diff-a2",
            )
        self.assertIn("still pending", str(ctx.exception))

    def test_partition_preserves_queue_order(self):
        b = wb.new_batch()
        i1 = wb.try_enqueue(
            b, tool="write_file", args={"path": "/tmp/a.txt", "content": "a"}, preview="a"
        )
        i2 = wb.try_enqueue(
            b, tool="write_file", args={"path": "/tmp/b.txt", "content": "b"}, preview="b"
        )
        i3 = wb.try_enqueue(
            b, tool="write_file", args={"path": "/tmp/c.txt", "content": "c"}, preview="c"
        )
        to_apply, denied = wb.partition_decision(b, [i3.item_id, i1.item_id])
        self.assertEqual([x.item_id for x in to_apply], [i1.item_id, i3.item_id])
        self.assertEqual([x.item_id for x in denied], [i2.item_id])

    def test_apply_items_continues_after_error(self):
        b = wb.new_batch()
        i1 = wb.try_enqueue(
            b, tool="write_file", args={"path": "/tmp/a.txt", "content": "a"}, preview="a"
        )
        i2 = wb.try_enqueue(
            b, tool="write_file", args={"path": "/tmp/b.txt", "content": "b"}, preview="b"
        )

        def run_tool(name, args, roots):
            if args["path"].endswith("a.txt"):
                raise RuntimeError("boom")
            return "ok"

        applied, errors = wb.apply_items([i1, i2], [], run_tool)
        self.assertEqual(applied, [i2.item_id])
        self.assertEqual(errors[0]["item_id"], i1.item_id)
        self.assertIn("boom", errors[0]["error"])

    def test_queued_tool_content(self):
        raw = wb.queued_tool_content("/tmp/a.txt")
        data = json.loads(raw)
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["path"], "/tmp/a.txt")

    def test_batch_full(self):
        b = wb.new_batch()
        for i in range(wb.MAX_WRITE_BATCH):
            wb.try_enqueue(
                b,
                tool="write_file",
                args={"path": f"/tmp/f{i}.txt", "content": "x"},
                preview="p",
            )
        with self.assertRaises(ValueError) as ctx:
            wb.try_enqueue(
                b,
                tool="write_file",
                args={"path": "/tmp/overflow.txt", "content": "x"},
                preview="p",
            )
        self.assertIn("full", str(ctx.exception).lower())
