import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
import core.ledger as lib  # noqa: E402
from core import init_main  # noqa: E402
from core.errors import PaneClash, UsageError  # noqa: E402
from tests.fakes.layers import FakeHost  # noqa: E402


def run(cwd: Path, pane="w1:p2", main="boss", retire=False, force=False):
    return init_main.run(FakeHost(pane=pane), main, cwd, retire, force)


class InitMainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_main_json(self):
        out, warnings = run(self.tmp, pane="w1:p2")
        self.assertEqual(warnings, [])
        ldir = lib.ledger_dir(lib.ledger_root(self.tmp), "boss")
        self.assertEqual(out, str(ldir))
        data = json.loads((ldir / "main.json").read_text())
        self.assertEqual(data["main"], "boss")
        self.assertEqual(data["pane_id"], "w1:p2")
        self.assertEqual(data["cwd"], str(self.tmp))
        self.assertTrue(data["updated"])
        self.assertTrue((ldir / "tasks.md").exists())
        self.assertEqual(lib.read_rows(ldir), [])

    def test_rerun_keeps_tasks(self):
        out, _ = run(self.tmp, pane="w1:p2")
        ldir = Path(out)
        lib.upsert_row(ldir, lib.Row("a", "s", "作業中", "b", "", "t", "p", "now"))
        run(self.tmp, pane="w9:p9")
        self.assertEqual(len(lib.read_rows(ldir)), 1)
        data = json.loads((ldir / "main.json").read_text())
        self.assertEqual(data["pane_id"], "w9:p9")

    def test_retire_removes_only_main_json(self):
        out, _ = run(self.tmp, pane="w1:p2")
        ldir = Path(out)
        out, warnings = run(self.tmp, pane=None, retire=True)
        self.assertEqual((out, warnings), (str(ldir), []))
        self.assertFalse((ldir / "main.json").exists())
        self.assertTrue((ldir / "tasks.md").exists())
        run(self.tmp, pane=None, retire=True)

    def test_rejects_bad_name(self):
        with self.assertRaises(UsageError) as cm:
            run(self.tmp, pane="w1:p2", main="Bad/Name")
        self.assertEqual(cm.exception.exit_code, 2)

    def test_cwd_must_be_directory(self):
        with self.assertRaises(UsageError) as cm:
            run(self.tmp / "nope", pane="w1:p2")
        self.assertEqual(cm.exception.exit_code, 2)
        self.assertTrue(str(cm.exception).strip())
        f = self.tmp / "file.txt"
        f.write_text("x")
        with self.assertRaises(UsageError):
            run(f, pane="w1:p2")

    def test_conflict_same_pane_other_name(self):
        run(self.tmp, pane="w1:p2", main="alpha")
        run(self.tmp, "w1:p2", "beta", force=True)
        with self.assertRaises(PaneClash) as cm:
            run(self.tmp, pane="w1:p2", main="gamma")
        self.assertEqual(cm.exception.exit_code, 3)
        self.assertEqual(sorted(str(cm.exception).split()), ["alpha", "beta"])
        ldir = lib.ledger_dir(lib.ledger_root(self.tmp), "gamma")
        self.assertFalse(ldir.exists())

    def test_conflict_force_registers(self):
        run(self.tmp, pane="w1:p2", main="alpha")
        run(self.tmp, "w1:p2", "gamma", force=True)
        ldir = lib.ledger_dir(lib.ledger_root(self.tmp), "gamma")
        self.assertTrue((ldir / "main.json").exists())

    def test_no_conflict_with_other_pane_or_bad_json(self):
        run(self.tmp, pane="w1:p9", main="alpha")
        bad = lib.ledger_dir(lib.ledger_root(self.tmp), "broken")
        bad.mkdir(parents=True)
        (bad / "main.json").write_text("{not json")
        run(self.tmp, pane="w1:p2", main="gamma")

    def test_same_name_other_pane_warns_and_overwrites(self):
        run(self.tmp, pane="w1:p2")
        _, warnings = run(self.tmp, pane="w5:p5")
        self.assertEqual(len(warnings), 1)
        self.assertIn("w1:p2", warnings[0])
        ldir = lib.ledger_dir(lib.ledger_root(self.tmp), "boss")
        self.assertEqual(json.loads((ldir / "main.json").read_text())["pane_id"], "w5:p5")

    def test_retire_skips_conflict_check(self):
        run(self.tmp, pane="w1:p2", main="alpha")
        run(self.tmp, "w1:p2", "beta", force=True)
        run(self.tmp, pane="w1:p2", main="beta", retire=True)

    def test_probe_only_requires_pane(self):
        host = FakeHost(pane="w1:p2")
        init_main.probe(host)
        self.assertEqual(host.calls, [("require_pane", ())])
        with self.assertRaises(UsageError):
            init_main.probe(FakeHost(pane=None))


if __name__ == "__main__":
    unittest.main()
