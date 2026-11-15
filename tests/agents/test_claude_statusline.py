import json
import sys
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
from agents.claude import ClaudeAgent  # noqa: E402


def sl(data):
    return ClaudeAgent().statusline(data if isinstance(data, str) else json.dumps(data))


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
