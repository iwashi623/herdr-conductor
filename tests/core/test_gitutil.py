import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
import core.gitutil as lib  # noqa: E402
import core.ledger  # noqa: E402
from core.errors import ConductorError  # noqa: E402

GIT_ENV = ["-c", "user.name=t", "-c", "user.email=t@e"]


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    return path


def commit_all(repo: Path):
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), *GIT_ENV, "commit", "-q", "-m", "i"], check=True)


class GitEnvTest(unittest.TestCase):
    def test_env_reaches_git(self):
        with tempfile.TemporaryDirectory() as d:
            repo = git_init(Path(d) / "r")
            idx = Path(d) / "tmp-index"
            (repo / "f").write_text("x")
            r = lib.git(repo, "add", "f", env={"GIT_INDEX_FILE": str(idx)})
            self.assertEqual(r.returncode, 0)
            self.assertTrue(idx.exists())
            self.assertFalse((repo / ".git" / "index").exists())


class LibTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def test_ensure_excluded_idempotent(self):
        repo = git_init(self.tmp / "repo")
        lib.ensure_excluded(repo)
        lib.ensure_excluded(repo)
        lines = (repo / ".git/info/exclude").read_text().splitlines()
        self.assertEqual(lines.count(".orchestra/"), 1)
        self.assertEqual(lines.count(".worktrees/"), 1)
        self.assertFalse((repo / ".gitignore").exists())

    def test_ensure_excluded_no_git(self):
        d = self.tmp / "plain"
        d.mkdir()
        lib.ensure_excluded(d)
        self.assertFalse((d / ".git").exists())

    def test_ensure_excluded_worktree(self):
        repo = git_init(self.tmp / "repo")
        env = ["-c", "user.name=t", "-c", "user.email=t@e"]
        subprocess.run(["git", "-C", str(repo), *env, "commit", "-q",
                        "--allow-empty", "-m", "i"], check=True)
        wt = self.tmp / "wt"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q",
                        "-b", "b", str(wt)], check=True)
        lib.ensure_excluded(wt)
        text = (repo / ".git/info/exclude").read_text()
        self.assertIn(".orchestra/", text)
        self.assertIn(".worktrees/", text)


class LibExclusiveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def test_work_tree_top_in_linked_worktree(self):
        repo = git_init(self.tmp / "repo")
        (repo / "f").write_text("x")
        commit_all(repo)
        wt = self.tmp / "wt"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "b"], check=True)
        sub = wt / "d"
        sub.mkdir()
        self.assertEqual(lib.work_tree_top(sub).resolve(), wt.resolve())
        self.assertEqual(core.ledger.ledger_root(sub).resolve(), repo)
        self.assertEqual(lib.work_tree_top(repo).resolve(), repo)

    def test_work_tree_top_non_git(self):
        d = self.tmp / "plain"
        d.mkdir()
        self.assertEqual(lib.work_tree_top(d), d)

    def test_head_sha(self):
        repo = git_init(self.tmp / "repo")
        (repo / "f").write_text("x")
        commit_all(repo)
        expect = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(lib.head_sha(repo), expect)

    def test_is_tracked(self):
        repo = git_init(self.tmp / "repo")
        (repo / "t").write_text("x")
        commit_all(repo)
        (repo / "u").write_text("y")
        self.assertTrue(lib.is_tracked(repo, "t"))
        self.assertFalse(lib.is_tracked(repo, "u"))
        plain = self.tmp / "plain"
        plain.mkdir()
        (plain / "t").write_text("x")
        self.assertFalse(lib.is_tracked(plain, "t"))

    def test_git_timeout_becomes_conductor_error(self):
        from unittest import mock
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            with self.assertRaises(ConductorError):
                lib.git(self.tmp, "status")

    def test_git_timeout_param_passed(self):
        from unittest import mock
        with mock.patch("subprocess.run") as run:
            lib.git(self.tmp, "status")
            self.assertEqual(run.call_args.kwargs["timeout"], 5)
            lib.git(self.tmp, "worktree", "add", timeout=60)
            self.assertEqual(run.call_args.kwargs["timeout"], 60)

    def test_git_missing_binary(self):
        from unittest import mock
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("git")):
            with self.assertRaises(ConductorError):
                lib.git(self.tmp, "status")


if __name__ == "__main__":
    unittest.main()
