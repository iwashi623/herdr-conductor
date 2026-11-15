import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
import core.ledger as lib  # noqa: E402
from core.errors import ConductorError, LockBusy  # noqa: E402


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    return path


class LibTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def test_name_re(self):
        for ok in ["auth-fix", "a", "a" + "b" * 31]:
            self.assertTrue(lib.NAME_RE.match(ok), ok)
        for ng in ["Auth", "1abc", "a" + "b" * 32, "auth fix", "認証"]:
            self.assertIsNone(lib.NAME_RE.match(ng), ng)

    def test_statuses(self):
        self.assertEqual(
            lib.STATUSES,
            ("作業中", "承認待ち", "人と対話中", "完了", "PR作成済み", "中止"),
        )

    def test_ledger_root_in_subdir(self):
        repo = git_init(self.tmp / "repo")
        deep = repo / "src" / "deep"
        deep.mkdir(parents=True)
        self.assertEqual(lib.ledger_root(deep).resolve(), repo)

    def test_ledger_root_non_git(self):
        d = self.tmp / "plain"
        d.mkdir()
        self.assertEqual(lib.ledger_root(d), d)

    def test_ledger_dir_rejects_slash(self):
        with self.assertRaises(ValueError):
            lib.ledger_dir(self.tmp, "a/b")
        self.assertEqual(lib.ledger_dir(self.tmp, "m"), self.tmp / ".orchestra" / "m")

    def test_rows_roundtrip(self):
        ldir = self.tmp / "l"
        ldir.mkdir()
        rows = [
            lib.Row("auth-fix", "認証の修正", "作業中", "feat/auth", "", "t1", "p1", "2026-11-06 10:00"),
            lib.Row("docs", "文書整備", "完了", "docs", "#12", "t2", "p2", "2026-11-06 11:00"),
        ]
        lib.write_rows(ldir, rows)
        self.assertEqual(lib.read_rows(ldir), rows)

    def test_pipe_in_summary_replaced(self):
        ldir = self.tmp / "l"
        ldir.mkdir()
        lib.write_rows(ldir, [lib.Row("a", "x|y", "作業中", "", "", "", "", "")])
        self.assertEqual(lib.read_rows(ldir)[0].summary, "x／y")

    def test_read_missing(self):
        self.assertEqual(lib.read_rows(self.tmp / "none"), [])

    def test_upsert_replaces(self):
        ldir = self.tmp / "l"
        ldir.mkdir()
        lib.upsert_row(ldir, lib.Row("a", "s", "作業中", "", "", "", "", ""))
        lib.upsert_row(ldir, lib.Row("b", "s", "作業中", "", "", "", "", ""))
        lib.upsert_row(ldir, lib.Row("a", "s", "完了", "", "", "", "", ""))
        rows = lib.read_rows(ldir)
        self.assertEqual([r.name for r in rows], ["a", "b"])
        self.assertEqual(rows[0].status, "完了")

    def test_ledger_dir_rejects_bad_names(self):
        for bad in ["", ".", "..", "a/b", "a\nb", "a\x00b", "a\tb", "a\x7fb"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                lib.ledger_dir(self.tmp, bad)
        self.assertEqual(lib.ledger_dir(self.tmp, "boss"), self.tmp / ".orchestra" / "boss")

    def test_ledger_root_inside_linked_worktree(self):
        repo = git_init(self.tmp / "repo")
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(repo)]
        subprocess.run(g + ["commit", "-q", "--allow-empty", "-m", "i"], check=True)
        wt = repo / ".worktrees" / "w"
        subprocess.run(g + ["worktree", "add", "-q", str(wt), "-b", "x"], check=True)
        self.assertEqual(lib.ledger_root(wt), repo)
        self.assertEqual(lib.ledger_root(repo), repo)


class LibExclusiveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def test_concurrent_upsert_two_processes(self):
        ldir = self.tmp / "ledger"
        ldir.mkdir()
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); import core.ledger as lib\n"
            "from pathlib import Path\n"
            "for i in range(100):\n"
            "    lib.upsert_row(Path(sys.argv[2]), lib.Row(f'{sys.argv[3]}-{i}', 's', '作業中', '', '', '', '', ''))\n"
        )
        libdir = str(SKILL)
        procs = [subprocess.Popen([sys.executable, "-c", code, libdir, str(ldir), tag]) for tag in ("a", "b")]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        names = {r.name for r in lib.read_rows(ldir)}
        self.assertEqual(len(names), 200)
        self.assertEqual(len(lib.read_rows(ldir)), 200)

    def test_write_json_atomic(self):
        p = self.tmp / "x.json"
        lib.write_json_atomic(p, {"a": "日本語", "n": 1})
        import json
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": "日本語", "n": 1})
        lib.write_json_atomic(p, {"a": 2})
        self.assertEqual(json.loads(p.read_text()), {"a": 2})
        self.assertEqual([f.name for f in self.tmp.iterdir()], ["x.json"])

    def test_no_old_git_name(self):
        self.assertFalse(hasattr(lib, "_git"))
        self.assertRegex(lib.iso_now(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")

    def test_atomic_files_mode_0644(self):
        p = self.tmp / "x.json"
        lib.write_json_atomic(p, {"a": 1})
        self.assertEqual(p.stat().st_mode & 0o777, 0o644)
        lib.upsert_row(self.tmp / "l", lib.Row("a", "s", "作業中", "", "", "", "", ""))
        self.assertEqual((self.tmp / "l" / "tasks.md").stat().st_mode & 0o777, 0o644)


class LockCreateTest(unittest.TestCase):
    def test_busy_in_other_process(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "x.lock"
            code = ("import sys;sys.path.insert(0,%r);"
                    "from core.ledger import file_lock\n"
                    "with file_lock(__import__('pathlib').Path(%r)):\n print('ready',flush=True);sys.stdin.read()"
                    % (str(SKILL), str(p)))
            proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                ready, _, _ = select.select([proc.stdout], [], [], 10)
                self.assertTrue(ready, "child did not take the lock")
                self.assertEqual(proc.stdout.readline().strip(), "ready")
                with self.assertRaises(LockBusy):
                    with lib.file_lock(p, blocking=False):
                        pass
            finally:
                proc.stdin.close()
                proc.wait(timeout=10)
                proc.stdout.close()
            with lib.file_lock(p, blocking=False):
                pass

    def test_create_exclusive(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f"
            lib.create_exclusive(p, "a")
            self.assertEqual(p.read_text(), "a")
            with self.assertRaises(ConductorError):
                lib.create_exclusive(p, "b")
            self.assertEqual(p.read_text(), "a")


    def test_create_exclusive_cleans_partial(self):
        import unittest.mock as m
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f"
            with m.patch("os.fdopen", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    lib.create_exclusive(p, "a")
            self.assertFalse(p.exists())
            lib.create_exclusive(p, "ok")


if __name__ == "__main__":
    unittest.main()
