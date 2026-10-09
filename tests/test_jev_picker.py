import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import jev_picker as jp  # noqa: E402
import fs_tools as ft  # noqa: E402


class JevPickerTest(unittest.TestCase):
    def test_criteria_includes_reply_and_all_tools(self):
        c = jp.choice_criteria()
        self.assertIn(jp.REPLY_KEY, c)
        for t in ft.TOOL_SCHEMAS:
            self.assertIn(t["function"]["name"], c)

    def test_build_state_truncates_oldest(self):
        msgs = [{"role": "user", "content": ("x" * 2000)} for _ in range(20)]
        state = jp.build_state(msgs, roots=["/tmp"], writes_armed=False)
        self.assertLessEqual(len(state), jp.STATE_CHAR_BUDGET)
        self.assertIn("workspace_roots", state)

    def test_build_request_shape(self):
        req = jp.build_request("hello state", model="jev-latest")
        self.assertEqual(req["model"], "jev-latest")
        self.assertEqual(req["state"], "hello state")
        q = req["questions"][jp.QUESTION_ID]
        self.assertEqual(q["type"], "choice")
        self.assertIn(jp.REPLY_KEY, q["criteria"])

    def test_base_url_default_and_strip(self):
        old = os.environ.get("TYPESAFE_BASE_URL")
        try:
            os.environ.pop("TYPESAFE_BASE_URL", None)
            self.assertEqual(jp.typesafe_base_url(), "https://api.typesafe.ai")
            os.environ["TYPESAFE_BASE_URL"] = "https://example.test/"
            self.assertEqual(jp.typesafe_base_url(), "https://example.test")
        finally:
            if old is None:
                os.environ.pop("TYPESAFE_BASE_URL", None)
            else:
                os.environ["TYPESAFE_BASE_URL"] = old

    def test_model_prefers_voxa_jev_model(self):
        old_a, old_b = os.environ.get("VOXA_JEV_MODEL"), os.environ.get("TYPESAFE_DEFAULT_MODEL")
        try:
            os.environ["VOXA_JEV_MODEL"] = "jev-custom"
            os.environ["TYPESAFE_DEFAULT_MODEL"] = "jev-latest"
            self.assertEqual(jp.jev_model(), "jev-custom")
        finally:
            for k, v in (("VOXA_JEV_MODEL", old_a), ("TYPESAFE_DEFAULT_MODEL", old_b)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_sensitive_set(self):
        self.assertIn("shell_run", jp.SENSITIVE_PRE_OLLAMA)
        self.assertNotIn("read_file", jp.SENSITIVE_PRE_OLLAMA)

    def test_criteria_includes_control_keys(self):
        c = jp.choice_criteria()
        for key in (jp.ASK_CLARIFY_KEY, jp.PROPOSE_PLAN_KEY, jp.UPDATE_TODO_KEY):
            self.assertIn(key, c)
        self.assertIn(jp.REPLY_KEY, c)

    def test_build_state_includes_mode_and_todos(self):
        todos = [{"id": "1", "label": "Add API", "status": "pending"}]
        state = jp.build_state(
            [{"role": "user", "content": "implement feature"}],
            roots=["/tmp/proj"],
            writes_armed=False,
            mode="planning",
            plan_approved=False,
            todos=todos,
        )
        self.assertIn("mode=planning", state)
        self.assertIn("plan_approved=false", state)
        self.assertIn("Add API", state)
        self.assertIn("files_read: (none)", state)

    def test_build_state_compacts_read_file_bodies(self):
        secret = "package net.runelite.client.plugins.microbot.actionreplay;\n" + ("x" * 500)
        msgs = [
            {"role": "user", "content": "audit actionreplay"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "actionreplay/ActionReplayConfig.java"},
                        }
                    }
                ],
            },
            {
                "role": "tool",
                "name": "read_file",
                "content": secret,
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "actionreplay/ActionReplayPlugin.java"}',
                        }
                    }
                ],
            },
            {
                "role": "tool",
                "name": "read_file",
                "content": "class Plugin {}\n",
            },
        ]
        state = jp.build_state(msgs, roots=["/tmp"], writes_armed=False)
        self.assertNotIn("package net.runelite", state)
        self.assertNotIn("class Plugin", state)
        self.assertIn(
            "read_file ok path=actionreplay/ActionReplayConfig.java "
            f"({len(secret)} chars)",
            state,
        )
        self.assertIn(
            "read_file ok path=actionreplay/ActionReplayPlugin.java (16 chars)",
            state,
        )
        self.assertIn(
            "files_read: actionreplay/ActionReplayConfig.java | "
            "actionreplay/ActionReplayPlugin.java",
            state,
        )
        # Input messages unchanged for Ollama.
        self.assertEqual(msgs[2]["content"], secret)

    def test_build_state_keeps_read_file_errors(self):
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "missing.java"},
                        }
                    }
                ],
            },
            {
                "role": "tool",
                "name": "read_file",
                "content": "error: not_a_file: missing.java",
            },
        ]
        state = jp.build_state(msgs, roots=["/tmp"], writes_armed=False)
        self.assertIn("read_file error path=missing.java", state)
        self.assertIn("error: not_a_file: missing.java", state)
        self.assertIn("files_read: (none)", state)

    def test_build_state_compacts_list_dir_and_search_files(self):
        listing = "Alpha.java\nBeta.java\n" + ("z" * 400)
        hits = "plugins/barrows/BarrowsPlugin.java\n" + ("y" * 200)
        msgs = [
            {"role": "user", "content": "find barrows plugin and list it"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "search_files",
                            "arguments": {
                                "root": "/repo",
                                "pattern": "**/barrows/**",
                            },
                        }
                    }
                ],
            },
            {"role": "tool", "name": "search_files", "content": hits},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "list_dir",
                            "arguments": {"path": "/repo/plugins/barrows"},
                        }
                    }
                ],
            },
            {"role": "tool", "name": "list_dir", "content": listing},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "list_dir",
                            "arguments": {"path": "/missing"},
                        }
                    }
                ],
            },
            {
                "role": "tool",
                "name": "list_dir",
                "content": "error: not_a_directory: /missing",
            },
        ]
        state = jp.build_state(msgs, roots=["/repo"], writes_armed=False)
        self.assertNotIn("Alpha.java", state)
        self.assertNotIn("BarrowsPlugin.java\n", state)
        self.assertIn(
            f"search_files ok root=/repo pattern=**/barrows/** ({len(hits)} chars)",
            state,
        )
        self.assertIn(
            f"list_dir ok path=/repo/plugins/barrows ({len(listing)} chars)",
            state,
        )
        self.assertIn("list_dir error path=/missing", state)
        self.assertIn("error: not_a_directory: /missing", state)
        # Ollama messages unchanged.
        self.assertEqual(msgs[2]["content"], hits)
        self.assertEqual(msgs[4]["content"], listing)

    def test_build_state_includes_latest_user_header(self):
        msgs = [
            {"role": "user", "content": "first ask"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "(system) internal note"},
            {"role": "user", "content": "where is the audit report?"},
        ]
        state = jp.build_state(msgs, roots=["/tmp"], writes_armed=False)
        self.assertIn("latest_user: where is the audit report?", state)
        # Header pin: latest_user appears before body separator.
        header, _, _ = state.partition("---\n")
        self.assertIn("latest_user: where is the audit report?", header)
        self.assertIn("files_read:", header)

    def test_prefer_reply_from_files(self):
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": {"path": "A.java"},
                        }
                    }
                ],
            },
            {"role": "tool", "name": "read_file", "content": "class A {}"},
            {"role": "user", "content": "where's the report / audit findings?"},
        ]
        self.assertTrue(jp.prefer_reply_from_files(msgs))
        self.assertFalse(
            jp.prefer_reply_from_files(
                [{"role": "user", "content": "list the directory please"}]
            )
        )

    def test_build_request_prefer_reply_instructions(self):
        req = jp.build_request("state", prefer_reply=True)
        instr = req["questions"][jp.QUESTION_ID]["instructions"]
        self.assertIn("prefer reply", instr.lower())
        crit = req["questions"][jp.QUESTION_ID]["criteria"]
        self.assertIn("already-read files", crit[jp.REPLY_KEY].lower())


def _ok_resp(choice="read_file", confidence=0.81):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "answers": {
            "next_action": {
                "type": "choice",
                "choice": choice,
                "confidence": confidence,
            }
        }
    }
    return resp


class JevPickerAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_pick_success_returns_backend_jev(self):
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok_resp())
        opt, conf, backend = await jp.pick_next_action(
            client,
            messages=[{"role": "user", "content": "read x"}],
            roots=[],
            writes_armed=False,
        )
        self.assertEqual(opt, "read_file")
        self.assertAlmostEqual(conf, 0.81)
        self.assertEqual(backend, "jev")
        self.assertEqual(client.post.await_count, 1)
        self.assertTrue(client.post.await_args.args[0].endswith("/v1/systemone"))
        self.assertEqual(
            client.post.await_args.kwargs["headers"]["Authorization"],
            "Bearer test-key",
        )

    async def test_missing_key_uses_tev1(self):
        old = os.environ.get("TYPESAFE_API_KEY")
        old_ollama = os.environ.get("OLLAMA_URL")
        old_tev1 = os.environ.get("VOXA_TEV1_MODEL")
        try:
            os.environ.pop("TYPESAFE_API_KEY", None)
            os.environ["OLLAMA_URL"] = "http://ollama.test:11434"
            os.environ["VOXA_TEV1_MODEL"] = "tev1:0.8b"
            client = MagicMock()
            client.post = AsyncMock(return_value=_ok_resp("reply", 0.55))
            opt, conf, backend = await jp.pick_next_action(
                client,
                messages=[{"role": "user", "content": "hi"}],
                roots=[],
                writes_armed=False,
            )
            self.assertEqual(opt, "reply")
            self.assertEqual(backend, "tev1")
            self.assertEqual(client.post.await_count, 1)
            url = client.post.await_args.args[0]
            self.assertEqual(url, "http://ollama.test:11434/v1/systemone")
            self.assertEqual(
                client.post.await_args.kwargs["json"]["model"], "tev1:0.8b"
            )
            self.assertEqual(
                client.post.await_args.kwargs["headers"]["Authorization"],
                "Bearer ollama",
            )
        finally:
            for k, v in (
                ("TYPESAFE_API_KEY", old),
                ("OLLAMA_URL", old_ollama),
                ("VOXA_TEV1_MODEL", old_tev1),
            ):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    async def test_jev_http_403_forged_body_still_falls_back_to_tev1(self):
        """HTTP error bodies must not be parsed as cancel/malformed via substrings."""
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        os.environ["OLLAMA_URL"] = "http://ollama.test:11434"
        client = MagicMock()
        bad = MagicMock()
        bad.status_code = 403
        bad.text = (
            "subscription cancelled; response malformed; unknown option in billing"
        )
        client.post = AsyncMock(side_effect=[bad, _ok_resp("read_file", 0.72)])
        opt, conf, backend = await jp.pick_next_action(
            client,
            messages=[{"role": "user", "content": "read"}],
            roots=[],
            writes_armed=False,
        )
        self.assertEqual(opt, "read_file")
        self.assertEqual(backend, "tev1")
        self.assertEqual(client.post.await_count, 2)

    async def test_jev_http_error_falls_back_to_tev1(self):
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        os.environ["OLLAMA_URL"] = "http://ollama.test:11434"
        client = MagicMock()
        bad = MagicMock()
        bad.status_code = 503
        bad.text = "down"
        client.post = AsyncMock(side_effect=[bad, _ok_resp("list_dir", 0.7)])
        opt, conf, backend = await jp.pick_next_action(
            client,
            messages=[{"role": "user", "content": "list"}],
            roots=[],
            writes_armed=False,
        )
        self.assertEqual(opt, "list_dir")
        self.assertEqual(backend, "tev1")
        self.assertEqual(client.post.await_count, 2)

    async def test_jev_transport_error_falls_back_to_tev1(self):
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        os.environ["OLLAMA_URL"] = "http://ollama.test:11434"
        client = MagicMock()
        client.post = AsyncMock(
            side_effect=[Exception("boom"), _ok_resp("reply", 0.6)]
        )
        opt, conf, backend = await jp.pick_next_action(
            client,
            messages=[{"role": "user", "content": "hi"}],
            roots=[],
            writes_armed=False,
        )
        self.assertEqual(backend, "tev1")
        self.assertEqual(opt, "reply")

    async def test_both_fail_combines_errors(self):
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        os.environ["OLLAMA_URL"] = "http://ollama.test:11434"
        client = MagicMock()
        client.post = AsyncMock(side_effect=Exception("boom"))
        with self.assertRaises(jp.JevPickerError) as ctx:
            await jp.pick_next_action(
                client,
                messages=[{"role": "user", "content": "hi"}],
                roots=[],
                writes_armed=False,
            )
        msg = ctx.exception.message
        self.assertIn("Jev unreachable", msg)
        self.assertIn("Tev1 fallback failed", msg)

    async def test_missing_key_and_tev1_fail(self):
        old = os.environ.get("TYPESAFE_API_KEY")
        try:
            os.environ.pop("TYPESAFE_API_KEY", None)
            os.environ["OLLAMA_URL"] = "http://ollama.test:11434"
            client = MagicMock()
            client.post = AsyncMock(side_effect=Exception("no tev1"))
            with self.assertRaises(jp.JevPickerError) as ctx:
                await jp.pick_next_action(
                    client,
                    messages=[{"role": "user", "content": "hi"}],
                    roots=[],
                    writes_armed=False,
                )
            self.assertIn("no API key", ctx.exception.message)
            self.assertIn("Tev1 fallback failed", ctx.exception.message)
        finally:
            if old is None:
                os.environ.pop("TYPESAFE_API_KEY", None)
            else:
                os.environ["TYPESAFE_API_KEY"] = old

    async def test_malformed_jev_does_not_fallback(self):
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        client = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"answers": {}}
        client.post = AsyncMock(return_value=resp)
        with self.assertRaises(jp.JevPickerError) as ctx:
            await jp.pick_next_action(
                client,
                messages=[{"role": "user", "content": "hi"}],
                roots=[],
                writes_armed=False,
            )
        self.assertIn("malformed", ctx.exception.message.lower())
        self.assertEqual(client.post.await_count, 1)

    async def test_unknown_jev_choice_does_not_fallback(self):
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        os.environ["OLLAMA_URL"] = "http://ollama.test:11434"
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok_resp("not_a_valid_action", 0.5))
        with self.assertRaises(jp.JevPickerError) as ctx:
            await jp.pick_next_action(
                client,
                messages=[{"role": "user", "content": "hi"}],
                roots=[],
                writes_armed=False,
            )
        self.assertIn("unknown option", ctx.exception.message.lower())
        self.assertEqual(client.post.await_count, 1)

    async def test_cancel_does_not_fallback(self):
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        client = MagicMock()
        client.post = AsyncMock()
        cancel = asyncio.Event()
        cancel.set()
        with self.assertRaises(jp.JevPickerError) as ctx:
            await jp.pick_next_action(
                client,
                messages=[{"role": "user", "content": "hi"}],
                roots=[],
                writes_armed=False,
                cancel=cancel,
            )
        self.assertIn("cancelled", ctx.exception.message.lower())
        client.post.assert_not_called()

    async def test_pick_accepts_propose_plan(self):
        os.environ["TYPESAFE_API_KEY"] = "test-key"
        client = MagicMock()
        client.post = AsyncMock(return_value=_ok_resp("propose_plan", 0.9))
        opt, conf, backend = await jp.pick_next_action(
            client,
            messages=[{"role": "user", "content": "implement x"}],
            roots=[],
            writes_armed=False,
            mode="normal",
        )
        self.assertEqual(opt, "propose_plan")
        self.assertEqual(backend, "jev")


class Tev1ConfigTest(unittest.TestCase):
    def test_tev1_model_default_and_override(self):
        old = os.environ.get("VOXA_TEV1_MODEL")
        try:
            os.environ.pop("VOXA_TEV1_MODEL", None)
            self.assertEqual(jp.tev1_model(), "tev1:0.8b")
            os.environ["VOXA_TEV1_MODEL"] = "tev1:4b"
            self.assertEqual(jp.tev1_model(), "tev1:4b")
        finally:
            if old is None:
                os.environ.pop("VOXA_TEV1_MODEL", None)
            else:
                os.environ["VOXA_TEV1_MODEL"] = old

    def test_ollama_base_url_default_and_strip(self):
        old = os.environ.get("OLLAMA_URL")
        try:
            os.environ.pop("OLLAMA_URL", None)
            self.assertEqual(jp.ollama_base_url(), "http://127.0.0.1:11434")
            os.environ["OLLAMA_URL"] = "http://host:11434/"
            self.assertEqual(jp.ollama_base_url(), "http://host:11434")
        finally:
            if old is None:
                os.environ.pop("OLLAMA_URL", None)
            else:
                os.environ["OLLAMA_URL"] = old


if __name__ == "__main__":
    unittest.main()
