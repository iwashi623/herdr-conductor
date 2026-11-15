import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
import core.ledger as lib  # noqa: E402

SCRIPT = SKILL / "init_main.py"


def run(*args, pane=None, **env_extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("HERDR_PANE_ID", "CONDUCTOR_HOST", "CONDUCTOR_AGENT")}
    if pane is not None:
        env["HERDR_PANE_ID"] = pane
    env.update(env_extra)
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


class InitMainCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def test_requires_pane(self):
        r = run("--main", "boss", "--cwd", str(self.tmp), pane=None)
        self.assertEqual(r.returncode, 2)
        self.assertIn("HERDR_PANE_ID", r.stderr)
        r = run("--main", "boss", "--cwd", str(self.tmp), pane="")
        self.assertEqual(r.returncode, 2)

    def test_success_prints_ledger_dir(self):
        r = run("--main", "boss", "--cwd", str(self.tmp), pane="w1:p2")
        self.assertEqual(r.returncode, 0, r.stderr)
        ldir = lib.ledger_dir(lib.ledger_root(self.tmp), "boss")
        self.assertEqual(r.stdout, f"{ldir}\n")

    def test_probe_with_pane_writes_nothing(self):
        r = run("--probe", "--cwd", str(self.tmp), pane="w1:p2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_probe_without_pane_exits_2(self):
        r = run("--probe", pane=None)
        self.assertEqual(r.returncode, 2)
        self.assertIn("HERDR_PANE_ID", r.stderr)

    def test_unknown_host_exits_2(self):
        r = run("--main", "boss", "--cwd", str(self.tmp), pane="w1:p2", CONDUCTOR_HOST="unknown")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_unknown_host_exits_2_with_retire(self):
        r = run("--main", "boss", "--cwd", str(self.tmp), "--retire", CONDUCTOR_HOST="unknown")
        self.assertEqual(r.returncode, 2)

    def test_unknown_host_exits_2_with_probe(self):
        r = run("--probe", pane="w1:p2", CONDUCTOR_HOST="unknown")
        self.assertEqual(r.returncode, 2)

    def test_neither_probe_nor_main_exits_2(self):
        r = run("--cwd", str(self.tmp), pane="w1:p2")
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
