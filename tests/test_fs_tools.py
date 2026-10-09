import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import fs_tools as ft  # noqa: E402


class FsToolsTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.roots = ft.normalize_roots([str(self.root)])

    def tearDown(self):
        self._td.cleanup()

    def test_resolve_allows_under_root(self):
        f = self.root / "a.txt"
        f.write_text("hi")
        self.assertEqual(ft.resolve_in_workspace(str(f), self.roots), f.resolve())

    def test_resolve_denies_outside(self):
        with self.assertRaises(ft.FsError):
            ft.resolve_in_workspace("/etc/passwd", self.roots)

    def test_symlink_escape_denied(self):
        outside = self.root.parent / "escape_target.txt"
        outside.write_text("secret")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        link = self.root / "link.txt"
        link.symlink_to(outside)
        with self.assertRaises(ft.FsError):
            ft.resolve_in_workspace(str(link), self.roots)

    def test_denylist_ssh(self):
        home = self.root / "home"
        ssh = home / ".ssh"
        ssh.mkdir(parents=True)
        (ssh / "id_rsa").write_text("key")
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(home)
        self.addCleanup(
            lambda: os.environ.__setitem__("HOME", old_home)
            if old_home is not None
            else os.environ.pop("HOME", None)
        )
        roots = ft.normalize_roots([str(home)])
        with self.assertRaises(ft.FsError):
            ft.resolve_in_workspace(str(ssh / "id_rsa"), roots)

    def test_read_write_roundtrip(self):
        out = ft.run_tool(
            "write_file",
            {"path": str(self.root / "x.txt"), "content": "abc"},
            self.roots,
        )
        self.assertIn("wrote", out.lower())
        self.assertEqual(
            ft.run_tool("read_file", {"path": str(self.root / "x.txt")}, self.roots),
            "abc",
        )

    def test_move_path(self):
        src = self.root / "a.txt"
        src.write_text("z")
        dst = self.root / "b.txt"
        ft.run_tool("move_path", {"src": str(src), "dst": str(dst)}, self.roots)
        self.assertFalse(src.exists())
        self.assertEqual(dst.read_text(), "z")

    def test_delete_file_rejects_dir(self):
        d = self.root / "d"
        d.mkdir()
        with self.assertRaises(ft.FsError):
            ft.run_tool("delete_file", {"path": str(d)}, self.roots)

    def test_pem_write_denied(self):
        with self.assertRaises(ft.FsError):
            ft.run_tool(
                "write_file",
                {"path": str(self.root / "x.pem"), "content": "x"},
                self.roots,
            )

    def test_list_and_search(self):
        (self.root / "hello.py").write_text("print(1)\n")
        listed = ft.run_tool("list_dir", {"path": str(self.root)}, self.roots)
        self.assertIn("hello.py", listed)
        found = ft.run_tool(
            "search_files",
            {"root": str(self.root), "pattern": "*.py"},
            self.roots,
        )
        self.assertIn("hello.py", found)

    def test_relative_paths_use_primary_workspace_not_process_cwd(self):
        (self.root / "hello.py").write_text("print(1)\n")
        listed = ft.run_tool("list_dir", {"path": "."}, self.roots)
        self.assertIn("hello.py", listed)
        listed_empty = ft.run_tool("list_dir", {"path": ""}, self.roots)
        self.assertIn("hello.py", listed_empty)
        rel = ft.resolve_in_workspace("hello.py", self.roots)
        self.assertEqual(rel, (self.root / "hello.py").resolve())
        self.assertEqual(
            ft.resolve_tool_cwd(None, self.roots),
            str(self.root.resolve()),
        )
        self.assertEqual(
            ft.resolve_tool_cwd(".", self.roots),
            str(self.root.resolve()),
        )

    def test_edit_file_preview_and_apply(self):
        target = self.root / "edit_me.txt"
        target.write_text("hello world\n")
        preview = ft.run_tool(
            "edit_file",
            {"path": str(target), "old": "world", "new": "voxa", "apply": False},
            self.roots,
        )
        data = json.loads(preview)
        self.assertEqual(data["status"], "preview")
        self.assertIn("-hello world", data["diff"])
        self.assertIn("+hello voxa", data["diff"])
        self.assertEqual(target.read_text(), "hello world\n")

        applied = ft.run_tool(
            "edit_file",
            {"path": str(target), "old": "world", "new": "voxa", "apply": True},
            self.roots,
        )
        data = json.loads(applied)
        self.assertEqual(data["status"], "applied")
        self.assertEqual(target.read_text(), "hello voxa\n")

    def test_edit_file_missing_old(self):
        target = self.root / "edit_me.txt"
        target.write_text("abc\n")
        out = ft.run_tool(
            "edit_file",
            {"path": str(target), "old": "zzz", "new": "yyy"},
            self.roots,
        )
        self.assertIn("old_text not found", out)

    def test_shell_execute_echo(self):
        out = json.loads(ft._shell_execute("echo hello-voxa", str(self.root)))
        self.assertEqual(out["exit_code"], 0)
        self.assertIn("hello-voxa", out["stdout"])

    def test_shell_execute_cancel_kills_sleep(self):
        import threading
        import time

        ev = threading.Event()

        def cancel_soon():
            time.sleep(0.3)
            ev.set()

        t = threading.Thread(target=cancel_soon)
        t.start()
        out = ft._shell_execute("sleep 30", cwd=".", timeout=60, cancel_event=ev)
        t.join(timeout=5)
        data = json.loads(out)
        self.assertTrue(
            data.get("cancelled")
            or "cancel" in (data.get("error") or "").lower()
        )

    def test_shell_execute_success_unchanged(self):
        out = ft._shell_execute("echo hi", cwd=".", timeout=30)
        data = json.loads(out)
        self.assertIn("hi", data.get("stdout", ""))
        self.assertEqual(data.get("exit_code"), 0)

    def test_shell_preview(self):
        text = ft._shell_preview("ls -la", str(self.root))
        self.assertIn("ls -la", text)
        self.assertIn(str(self.root), text)

    def test_tool_schemas_include_core_four(self):
        names = {t["function"]["name"] for t in ft.TOOL_SCHEMAS}
        for required in ("shell_run", "edit_file", "web_search", "web_fetch", "get_weather"):
            self.assertIn(required, names)

    def test_web_search_parses_instant_answer(self):
        import urllib.request
        from unittest.mock import patch

        sample = {
            "Heading": "Charlie Kirk",
            "AbstractText": "American political activist.",
            "AbstractURL": "https://en.wikipedia.org/wiki/Charlie_Kirk",
            "Answer": "",
            "AnswerType": "",
            "Definition": "",
            "DefinitionURL": "",
            "Results": [
                {
                    "FirstURL": "https://example.com/official",
                    "Text": "Official site",
                }
            ],
            "RelatedTopics": [
                {
                    "FirstURL": "https://en.wikipedia.org/wiki/Turning_Point_USA",
                    "Text": "Turning Point USA - Organization he co-founded",
                },
                {
                    "Name": "See also",
                    "Topics": [
                        {
                            "FirstURL": "https://en.wikipedia.org/wiki/Conservatism",
                            "Text": "Conservatism - Related topic",
                        }
                    ],
                },
            ],
        }

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return json.dumps(sample).encode()

        with patch.object(urllib.request, "urlopen", lambda *a, **k: FakeResp()):
            out = json.loads(ft._tool_web_search({"query": "Charlie Kirk", "limit": 5}))
        self.assertEqual(out["provider"], "duckduckgo_instant_answer")
        self.assertGreaterEqual(len(out["results"]), 3)
        self.assertEqual(out["results"][0]["url"], "https://en.wikipedia.org/wiki/Charlie_Kirk")
        self.assertEqual(out["results"][0]["snippet"], "American political activist.")
        urls = [r["url"] for r in out["results"]]
        self.assertIn("https://example.com/official", urls)
        self.assertIn("https://en.wikipedia.org/wiki/Turning_Point_USA", urls)

    def test_web_search_empty_instant_answer(self):
        import urllib.request
        from unittest.mock import patch

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return json.dumps({
                    "Heading": "",
                    "AbstractText": "",
                    "AbstractURL": "",
                    "Results": [],
                    "RelatedTopics": [],
                }).encode()

        with patch.object(urllib.request, "urlopen", lambda *a, **k: FakeResp()):
            out = json.loads(ft._tool_web_search({"query": "obscure xyzzy", "limit": 3}))
        self.assertEqual(out["results"], [])
        self.assertIn("error", out)

    def test_load_dotenv_and_weather_key(self):
        env_file = self.root / ".env"
        env_file.write_text("OPENWEATHERMAP_API_KEY=test-key-123\nOTHER=1\n")
        os.environ.pop("OPENWEATHERMAP_API_KEY", None)
        os.environ.pop("OTHER", None)
        n = ft.load_dotenv(env_file)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(os.environ.get("OPENWEATHERMAP_API_KEY"), "test-key-123")
        # does not override existing
        os.environ["OPENWEATHERMAP_API_KEY"] = "keep-me"
        ft.load_dotenv(env_file, override=False)
        self.assertEqual(os.environ.get("OPENWEATHERMAP_API_KEY"), "keep-me")

    def test_web_fetch_rejects_non_http(self):
        out = ft.run_tool("web_fetch", {"url": "file:///etc/passwd"}, self.roots)
        self.assertIn("http://", out)

    def test_preview_write_edit_file(self):
        target = self.root / "p.txt"
        target.write_text("one two three\n")
        preview = ft.preview_write(
            "edit_file",
            {"path": str(target), "old": "two", "new": "2", "apply": True},
            self.roots,
        )
        # preview_write forces apply=False
        data = json.loads(preview)
        self.assertEqual(data["status"], "preview")
        self.assertEqual(target.read_text(), "one two three\n")


if __name__ == "__main__":
    unittest.main()
