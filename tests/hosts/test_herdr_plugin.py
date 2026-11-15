import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "hosts" / "herdr-plugin" / "notify_blocked.py"
FAKE = ROOT / "tests" / "fakes" / "herdr"


class NotifyBlockedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = Path(self.tmp.name) / "calls.jsonl"
        self.log = Path(self.tmp.name) / "logs" / "blocked.log"

    def run_script(self, event, now=None):
        env = {
            "PATH": os.environ["PATH"],
            "HERDR_BIN_PATH": str(FAKE),
            "FAKE_HERDR_LOG": str(self.calls),
            "CONDUCTOR_BLOCKED_LOG": str(self.log),
        }
        if now is not None:
            env["CONDUCTOR_NOTIFY_NOW"] = str(now)
        if event is not None:
            env["HERDR_PLUGIN_EVENT_JSON"] = event if isinstance(event, str) else json.dumps(event)
        return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)

    def herdr_calls(self):
        if not self.calls.exists():
            return []
        return [json.loads(l) for l in self.calls.read_text().splitlines()]

    def test_blocked_notifies_with_display_agent(self):
        r = self.run_script({"data": {"agent_status": "blocked", "pane_id": "p_1", "display_agent": "Claude", "agent": "claude"}})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self.herdr_calls(), [["notification", "show", "[承認待ち] Claude", "--sound", "request"]])

    def test_falls_back_to_agent_then_pane_id(self):
        self.run_script({"data": {"agent_status": "blocked", "pane_id": "p_1", "agent": "claude"}})
        self.run_script({"data": {"agent_status": "blocked", "pane_id": "p_2"}})
        self.assertEqual([c[2] for c in self.herdr_calls()], ["[承認待ち] claude", "[承認待ち] p_2"])

    def test_log_line_appended(self):
        self.run_script({"data": {"agent_status": "blocked", "pane_id": "p_1", "display_agent": "Claude"}})
        self.run_script({"data": {"agent_status": "blocked", "pane_id": "p_2", "display_agent": "Codex"}})
        lines = self.log.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        t, pane, label = lines[0].split("\t")
        self.assertRegex(t, r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
        self.assertEqual((pane, label), ("p_1", "Claude"))
        self.assertTrue(lines[1].endswith("p_2\tCodex"))

    def test_other_statuses_do_nothing(self):
        for s in ["idle", "working", "done", "unknown"]:
            r = self.run_script({"data": {"agent_status": s, "pane_id": "p_1"}})
            self.assertEqual(r.returncode, 0)
        self.assertEqual(self.herdr_calls(), [])
        self.assertFalse(self.log.exists())

    def test_malformed_or_missing_event_exits_zero_silently(self):
        for ev in [None, "", "not json", "[]", "null", {"data": None}, {}, {"data": {}}]:
            r = self.run_script(ev)
            self.assertEqual(r.returncode, 0, ev)
            self.assertEqual(r.stdout + r.stderr, "", ev)
        self.assertEqual(self.herdr_calls(), [])
        self.assertFalse(self.log.exists())

    def test_herdr_failure_still_exits_zero(self):
        env_bin = Path(self.tmp.name) / "nonexistent"
        env = {"PATH": os.environ["PATH"], "HERDR_BIN_PATH": str(env_bin),
               "CONDUCTOR_BLOCKED_LOG": str(self.log),
               "HERDR_PLUGIN_EVENT_JSON": json.dumps({"data": {"agent_status": "blocked", "pane_id": "p_1"}})}
        r = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(self.log.exists())

    def blocked(self, pane="p_1", label="Claude"):
        return {"data": {"agent_status": "blocked", "pane_id": pane, "display_agent": label}}

    def test_control_chars_replaced_and_truncated(self):
        self.run_script(self.blocked(label="a\nb\x1b[31m\tc\x7fd" + "x" * 200))
        title = self.herdr_calls()[0][2]
        self.assertTrue(title.startswith("[承認待ち] a b"))
        self.assertFalse(any(ord(c) < 32 or ord(c) == 127 for c in title))
        self.assertEqual(len(title), len("[承認待ち] ") + 80)
        line = self.log.read_text().splitlines()
        self.assertEqual(len(line), 1)
        self.assertEqual(len(line[0].split("\t")), 3)

    def test_blank_label_falls_back_to_pane_id(self):
        self.run_script(self.blocked(label=" \n\t "))
        self.assertEqual(self.herdr_calls()[0][2], "[承認待ち] p_1")

    def test_leading_dash_label_never_starts_title_with_dash(self):
        self.run_script(self.blocked(label="--help"))
        self.assertEqual(self.herdr_calls()[0][2], "[承認待ち] --help")

    def test_pane_id_sanitized_in_log(self):
        self.run_script(self.blocked(pane="p\n1"))
        lines = self.log.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0].split("\t")[1], "p 1")

    def test_duplicate_within_30s_suppressed(self):
        self.run_script(self.blocked(), now=1000)
        self.run_script(self.blocked(), now=1029)
        self.assertEqual(len(self.herdr_calls()), 1)
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_notifies_again_after_30s(self):
        self.run_script(self.blocked(), now=1000)
        self.run_script(self.blocked(), now=1030)
        self.assertEqual(len(self.herdr_calls()), 2)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)

    def test_other_pane_not_suppressed(self):
        self.run_script(self.blocked("p_1"), now=1000)
        self.run_script(self.blocked("p_2"), now=1001)
        self.assertEqual(len(self.herdr_calls()), 2)

    def test_state_file_in_logs_dir_and_corrupt_ignored(self):
        self.log.parent.mkdir(parents=True)
        state = self.log.parent / ".last_notify.json"
        state.write_text("{not json")
        self.run_script(self.blocked(), now=1000)
        self.assertEqual(len(self.herdr_calls()), 1)
        self.assertEqual(json.loads(state.read_text()), {"p_1": 1000})
        state.write_text("[1]")
        self.run_script(self.blocked(), now=2000)
        self.assertEqual(len(self.herdr_calls()), 2)

    def test_default_log_points_to_skill_root_logs(self):
        spec = importlib.util.spec_from_file_location("notify_blocked_under_test", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.DEFAULT_LOG, ROOT / "logs" / "blocked.log")


if __name__ == "__main__":
    unittest.main()
