import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import apply_snippet as asp  # noqa: E402
import fs_tools as ft  # noqa: E402


class ApplySnippetTest(unittest.TestCase):
    def test_writes_under_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td).resolve())
            target = str(Path(root) / "hello.txt")
            out = asp.apply_snippet(target, "hi\n", [root])
            self.assertEqual(Path(out), Path(target))
            self.assertEqual(Path(target).read_text(encoding="utf-8"), "hi\n")

    def test_rejects_outside_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = str(Path(td).resolve())
            outside = str(Path(td).resolve().parent / "nope-outside-voxa-apply.txt")
            with self.assertRaises((ft.FsError, ValueError, OSError)):
                asp.apply_snippet(outside, "x", [root])

    def test_empty_path(self):
        with self.assertRaises(ValueError):
            asp.apply_snippet("", "x", ["/tmp"])

    def test_empty_roots(self):
        with self.assertRaises(ValueError):
            asp.apply_snippet("/tmp/a", "x", [])


if __name__ == "__main__":
    unittest.main()
