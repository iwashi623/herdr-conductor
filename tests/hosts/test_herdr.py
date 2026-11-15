import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
from core.errors import HostError, UsageError  # noqa: E402
from core.protocols import LaunchSpec  # noqa: E402
from hosts.herdr import HerdrHost  # noqa: E402

FAKES = SKILL / "tests" / "fakes"
OK = {"stdout": '{"result":{"type":"ok"}}', "exit": 0}
TAB = {"stdout": '{"result":{"tab":{"tab_id":"w1:t5"},"root_pane":{"pane_id":"w1:p9"}}}', "exit": 0}
BUSY = {"stdout": "", "exit": 1, "stderr": json.dumps({"error": {"code": "agent_pane_busy"}})}


class HerdrHostTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "herdr.log"
        self.env = {"PATH": f"{FAKES}{os.pathsep}{os.environ['PATH']}", "FAKE_HERDR_LOG": str(self.log)}
        p = mock.patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def respond(self, resp):
        os.environ["FAKE_HERDR_RESPONSES"] = json.dumps(resp)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def test_create_tab_args(self):
        self.respond({"tab create": TAB})
        tab = HerdrHost().create_tab("w1", "lbl", Path("/x/y"), {"A": "1", "B": "2"})
        self.assertEqual((tab.tab_id, tab.pane_id), ("w1:t5", "w1:p9"))
        c = self.calls()[0]
        self.assertIn("--no-focus", c)
        self.assertEqual(c[c.index("--workspace") + 1], "w1")
        self.assertEqual(c[c.index("--cwd") + 1], "/x/y")
        self.assertEqual(c[c.index("--label") + 1], "lbl")
        self.assertEqual(c[c.index("A=1") - 1], "--env")
        self.assertEqual(c[c.index("B=2") - 1], "--env")

    def test_create_tab_missing_ids_closes_tab(self):
        self.respond({"tab create": {"stdout": '{"result":{"tab":{"tab_id":"w1:t5"}}}', "exit": 0}})
        with self.assertRaises(HostError):
            HerdrHost().create_tab("w1", "l", Path("/x"), {})
        self.assertEqual(self.calls()[-1], ["tab", "close", "w1:t5"])

    def test_start_agent_passes_kind_and_tail_of_command(self):
        spec = LaunchSpec("kindx", ["exe", "--a", "b c"])
        HerdrHost().start_agent("w1:p9", "nm", spec)
        c = self.calls()[0]
        self.assertEqual(c, ["agent", "start", "nm", "--kind", "kindx", "--pane", "w1:p9",
                             "--timeout", "60000", "--", "--a", "b c"])
        self.assertNotIn("exe", c)

    def test_retry_times_from_init(self):
        self.respond({"agent start": BUSY})
        with self.assertRaises(HostError):
            HerdrHost(retry_interval=0.01, retry_total=0.1).start_agent("p", "n", LaunchSpec("k", ["x"]))
        self.assertGreater(len(self.calls()), 1)
        self.log.unlink()
        with self.assertRaises(HostError):
            HerdrHost(retry_interval=0.01, retry_total=0).start_agent("p", "n", LaunchSpec("k", ["x"]))
        self.assertEqual(len(self.calls()), 1)

    def test_retry_times_from_env(self):
        os.environ["CONDUCTOR_SPAWN_RETRY_INTERVAL"] = "0.25"
        os.environ["CONDUCTOR_SPAWN_RETRY_TOTAL"] = "3"
        self.assertEqual(HerdrHost()._retry_times(), (0.25, 3.0))

    def test_bad_retry_env_does_not_break_constructor_or_other_methods(self):
        os.environ["CONDUCTOR_SPAWN_RETRY_INTERVAL"] = "abc"
        h = HerdrHost()
        self.respond({"tab get": {"stdout": '{"result":{"tab":{"label":"w"}}}', "exit": 0}})
        self.assertEqual(h.tab_label("t"), "w")

    def test_tab_label(self):
        self.respond({"tab get": {"stdout": '{"result":{"tab":{"label":"w"}}}', "exit": 0}})
        self.assertEqual(HerdrHost().tab_label("t"), "w")

    def test_tab_label_other_failure_and_unreadable(self):
        self.respond({"tab get": {"stderr": "socket error", "exit": 1}})
        with self.assertRaises(HostError) as cm:
            HerdrHost().tab_label("t")
        self.assertIn("socket error", str(cm.exception))
        self.respond({"tab get": {"stdout": "garbage", "exit": 0}})
        with self.assertRaises(HostError):
            HerdrHost().tab_label("t")

    def test_live_agent_names_skips_null(self):
        self.respond({"agent list": {"stdout": json.dumps({"result": {"agents": [
            {"name": None}, {"name": "a"}, {"name": "b"}]}}), "exit": 0}})
        self.assertEqual(HerdrHost().live_agent_names(), ["a", "b"])

    def test_current_pane(self):
        with mock.patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p2"}):
            self.assertEqual(HerdrHost().current_pane(), "w1:p2")
        with mock.patch.dict(os.environ):
            os.environ.pop("HERDR_PANE_ID", None)
            self.assertIsNone(HerdrHost().current_pane())

    def test_require_pane(self):
        with mock.patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p2"}):
            self.assertEqual(HerdrHost().require_pane(), "w1:p2")
        with mock.patch.dict(os.environ):
            os.environ.pop("HERDR_PANE_ID", None)
            with self.assertRaises(UsageError) as cm:
                HerdrHost().require_pane()
        self.assertEqual(str(cm.exception), "HERDR_PANE_ID が空です。herdr のペイン内で実行してください。")

    def test_current_workspace(self):
        with mock.patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_WORKSPACE_ID": "w3"}):
            self.assertEqual(HerdrHost().current_workspace(), "w3")
        with mock.patch.dict(os.environ, {"HERDR_ENV": "0", "HERDR_WORKSPACE_ID": "w3"}):
            with self.assertRaises(HostError):
                HerdrHost().current_workspace()
        with mock.patch.dict(os.environ, {"HERDR_ENV": "1", "HERDR_WORKSPACE_ID": ""}):
            with self.assertRaises(HostError):
                HerdrHost().current_workspace()

    def test_missing_binary_is_host_error(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            with self.assertRaises(HostError):
                HerdrHost().close_tab("t")
            with self.assertRaises(HostError):
                HerdrHost().live_agent_names()

    def test_requires_herdr_env(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("HERDR_ENV", None)
            os.environ.pop("HERDR_WORKSPACE_ID", None)
            with self.assertRaises(HostError) as cm:
                HerdrHost().current_workspace()
        self.assertIn("HERDR_ENV", str(cm.exception))
        self.assertEqual(self.calls(), [])

    def test_agent_start_retries_when_pane_busy(self):
        self.respond({"agent start": [BUSY, BUSY, OK]})
        HerdrHost(retry_interval=0.01, retry_total=0.3).start_agent("w1:p9", "n", LaunchSpec("k", ["x"]))
        self.assertEqual(len([c for c in self.calls() if c[:2] == ["agent", "start"]]), 3)

    def test_agent_start_busy_exhausts_budget(self):
        self.respond({"agent start": BUSY})
        with self.assertRaises(HostError):
            HerdrHost(retry_interval=0.01, retry_total=1.0).start_agent("w1:p9", "n", LaunchSpec("k", ["x"]))
        self.assertGreater(len(self.calls()), 1)

    def test_agent_start_other_error_not_retried(self):
        other = {"stdout": "", "exit": 1, "stderr": json.dumps({"error": {"code": "agent_not_ready", "message": "x"}})}
        self.respond({"agent start": other})
        with self.assertRaises(HostError):
            HerdrHost(retry_interval=0.01, retry_total=0.3).start_agent("w1:p9", "n", LaunchSpec("k", ["x"]))
        self.assertEqual(len(self.calls()), 1)

    def test_metadata_failure_is_ignored(self):
        self.respond({"pane report-metadata": {"stdout": "", "exit": 1, "stderr": "boom"}})
        self.assertFalse(HerdrHost().show_status("w1:p9", "n", "k", "s"))

    def test_malformed_tab_response_closes_tab(self):
        self.respond({"tab create": {"stdout": '{"result":{"tab":{"tab_id":"w1:t7"}}}', "exit": 0}})
        with self.assertRaises(HostError):
            HerdrHost().create_tab("w1", "l", Path("/x"), {})
        self.assertEqual([c for c in self.calls() if c[:2] == ["tab", "close"]][0][2], "w1:t7")

    def test_tab_not_found_real_stderr_clears(self):
        real = {"stderr": '{"error":{"code":"tab_not_found","message":"tab w1:t999 not found"},"id":"cli:tab:get"}',
                "exit": 1}
        self.respond({"tab get": real})
        self.assertIsNone(HerdrHost().tab_label("w1:t5"))


if __name__ == "__main__":
    unittest.main()
