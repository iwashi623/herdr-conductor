import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
from agents.claude import ClaudeAgent  # noqa: E402


NOWHERE = Path("/nonexistent-conductor-test/settings.json")


def sl(data, settings_path=NOWHERE):
    return ClaudeAgent(settings_path).statusline(data if isinstance(data, str) else json.dumps(data))


def payload(total, size=1_000_000, model="claude-opus-5-5", used=38):
    cw = {"context_window_size": size, "used_percentage": used}
    if total is not None:
        cw["total_input_tokens"] = total
    return {"model": {"id": model}, "context_window": cw, "cwd": "/x/p"}


class AutoCompactWindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "settings.json"

    def settings(self, ms):
        self.path.write_text(json.dumps({"modelSettings": ms}), encoding="utf-8")

    def run_sl(self, data):
        return sl(data, self.path)

    def test_matching_model(self):
        self.settings({"claude-opus-5-5": {"autoCompactWindow": 500000}})
        self.assertEqual(self.run_sl(payload(375500)), "ctx 75% · p")

    def test_no_matching_model(self):
        self.settings({"other": {"autoCompactWindow": 500000}})
        self.assertEqual(self.run_sl(payload(375500)), "ctx 38% · p")

    def test_larger_than_window(self):
        self.settings({"claude-opus-5-5": {"autoCompactWindow": 2000000}})
        self.assertEqual(self.run_sl(payload(375500)), "ctx 38% · p")

    def test_broken_settings(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.run_sl(payload(375500)), "ctx 38% · p")
        self.assertEqual(sl(payload(375500), NOWHERE), "ctx 38% · p")
        self.path.write_text("[1]", encoding="utf-8")
        self.assertEqual(self.run_sl(payload(375500)), "ctx 38% · p")
        self.settings(["x"])
        self.assertEqual(self.run_sl(payload(375500)), "ctx 38% · p")

    def test_invalid_values_ignored(self):
        for v in (True, 0, -5, "500000", 5e5, None):
            self.settings({"claude-opus-5-5": {"autoCompactWindow": v}})
            self.assertEqual(self.run_sl(payload(375500)), "ctx 38% · p", v)

    def test_missing_total_uses_used_percentage(self):
        self.settings({"claude-opus-5-5": {"autoCompactWindow": 500000}})
        self.assertEqual(self.run_sl(payload(None, used=10)), "ctx 10% · p")
        self.assertEqual(self.run_sl(payload(0, used=10)), "ctx 10% · p")
        self.assertEqual(self.run_sl(payload(5, size=0, used=10)), "ctx 10% · p")

    def test_missing_everything(self):
        d = payload(None)
        del d["context_window"]["used_percentage"]
        self.assertEqual(self.run_sl(d), "ctx --% · p")

    def test_bracket_suffix_matches_base(self):
        self.settings({"claude-opus-5-5": {"autoCompactWindow": 500000}})
        self.assertEqual(self.run_sl(payload(375500, model="claude-opus-5-5[1m]")), "ctx 75% · p")

    def test_exact_bracket_key_preferred(self):
        self.settings({"claude-opus-5-5[1m]": {"autoCompactWindow": 250000},
                       "claude-opus-5-5": {"autoCompactWindow": 500000}})
        self.assertEqual(self.run_sl(payload(125000, model="claude-opus-5-5[1m]")), "ctx 50% · p")


class StatuslineTest(unittest.TestCase):
    def test_normal(self):
        out = sl({"context_window": {"used_percentage": 42.4}, "cwd": "/a/b/proj"})
        self.assertEqual(out, "ctx 42% · proj")

    def test_rounds(self):
        self.assertEqual(sl({"context_window": {"used_percentage": 42.6}, "cwd": "/x/p"}), "ctx 43% · p")

    def test_no_percentage(self):
        self.assertEqual(sl({"context_window": {}, "cwd": "/x/p"}), "ctx --% · p")
        self.assertEqual(sl({"context_window": {"used_percentage": None}}), "ctx --%")

    def test_non_numeric(self):
        for v in ("abc", [1], {"a": 1}, True, float("nan"), float("inf")):
            self.assertTrue(sl({"context_window": {"used_percentage": v}}).startswith("ctx --%"), v)

    def test_broken_input(self):
        for t in ("not json", "", "[1]", "null", '{"context_window": 3}'):
            self.assertEqual(sl(t), "ctx --%", t)

    def test_workspace_current_dir(self):
        out = sl({"context_window": {"used_percentage": 10}, "workspace": {"current_dir": "/q/wdir"}})
        self.assertEqual(out, "ctx 10% · wdir")

    def test_cwd_preferred_and_trailing_slash(self):
        out = sl({"cwd": "/a/b/", "workspace": {"current_dir": "/z/other"}, "context_window": {"used_percentage": 1}})
        self.assertEqual(out, "ctx 1% · b")

    def test_no_dir(self):
        self.assertEqual(sl({"context_window": {"used_percentage": 5}}), "ctx 5%")
