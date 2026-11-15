import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
SCRIPT = SKILL / "statusline.py"


def run(stdin, extra=None, home=None):
    env = {k: v for k, v in os.environ.items() if k != "CONDUCTOR_AGENT"}
    if home:
        env["HOME"] = home
    env.update(extra or {})
    t = time.monotonic()
    r = subprocess.run([sys.executable, str(SCRIPT)], input=stdin, capture_output=True,
                       text=True, env=env)
    return r, time.monotonic() - t


class StatuslineCliTest(unittest.TestCase):
    def test_normal_and_fast(self):
        r, dt = run(json.dumps({"context_window": {"used_percentage": 33}, "cwd": "/a/proj"}))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "ctx 33% · proj\n")
        self.assertLess(dt, 0.5)

    def test_broken_input(self):
        r, _ = run("garbage")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "ctx --%\n")

    def test_unknown_agent(self):
        r, _ = run(json.dumps({"context_window": {"used_percentage": 33}}), {"CONDUCTOR_AGENT": "unknown"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "ctx --%\n")

    def test_no_files_written(self):
        with tempfile.TemporaryDirectory() as h:
            run(json.dumps({"context_window": {"used_percentage": 1}, "cwd": "/a"}), home=h)
            run("bad", home=h)
            self.assertEqual(list(Path(h).rglob("*")), [])


if __name__ == "__main__":
    unittest.main()
