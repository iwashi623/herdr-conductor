import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
import core.ledger as lib  # noqa: E402
from core import check, contract as contract_lib  # noqa: E402
from tests.helpers import git, init_repo  # noqa: E402
from tests.helpers_check import make_contract  # noqa: E402

SCRIPT = SKILL / "finish.py"
FAKES = SKILL / "tests" / "fakes"
TAB_CLOSE_OK = {"stdout": '{"id":"cli:tab:close","result":{"type":"ok"}}', "exit": 0}


def tab_get(label, tab="w1:t5"):
    body = {"id": "cli:tab:get", "result": {"tab": {"tab_id": tab, "label": label, "workspace_id": "w1"}}}
    return {"stdout": json.dumps(body), "exit": 0}


class FinishCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = init_repo(self.tmp / "repo")
        self.log = self.tmp / "herdr.log"
        self.ldir = lib.ledger_dir(self.repo, "boss")
        self.ldir.mkdir(parents=True)
        lib.write_rows(self.ldir, [])

    def tearDown(self):
        self._tmp.cleanup()

    def add_worker(self, name="auth-fix", worktree=True, tab="w1:t5", pane="w1:p9"):
        branch = ""
        if worktree:
            branch = f"conductor/{name}"
            git(self.repo, "worktree", "add", str(self.repo / ".worktrees" / name), "-b", branch)
            base = git(self.repo, "rev-parse", "HEAD").stdout.strip()
            contract_lib.write_contract(self.ldir, name, make_contract(base, ""))
            check.run(self.ldir, self.repo, name, "main")
        lib.upsert_row(self.ldir, lib.Row(
            name=name, summary="s", status="作業中", branch=branch, pr="",
            tab=tab, pane=pane, updated="old"))

    def run_finish(self, *extra, name="auth-fix", responses=None, env_extra=None):
        env = {k: v for k, v in os.environ.items() if k not in ("CONDUCTOR_HOST", "CONDUCTOR_AGENT")}
        env["PATH"] = f"{FAKES}{os.pathsep}{env.get('PATH', '')}"
        env["FAKE_HERDR_LOG"] = str(self.log)
        env["FAKE_HERDR_RESPONSES"] = json.dumps(responses or {
            "tab get": tab_get("auth-fix"), "tab close": TAB_CLOSE_OK})
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--main", "boss", "--name", name,
             "--cwd", str(self.repo), *extra],
            capture_output=True, text=True, env=env,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def row(self, name="auth-fix"):
        return next(r for r in lib.read_rows(self.ldir) if r.name == name)

    def test_clean_finish(self):
        self.add_worker()
        r = self.run_finish("--status", "PR作成済み", "--pr", "https://example.com/pr/1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(["tab", "close", "w1:t5"], self.calls())
        self.assertFalse((self.repo / ".worktrees" / "auth-fix").exists())
        branches = git(self.repo, "branch", "--list", "conductor/auth-fix").stdout
        self.assertIn("conductor/auth-fix", branches)
        row = self.row()
        self.assertEqual(row.status, "PR作成済み")
        self.assertEqual(row.pr, "https://example.com/pr/1")
        self.assertNotEqual(row.updated, "old")
        self.assertEqual((row.tab, row.pane), ("", ""))

    def test_invalid_status_rejected(self):
        self.add_worker()
        r = self.run_finish("--status", "作業中")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_skip_checks_arg_passes(self):
        self.add_worker()
        (self.ldir / "tasks" / "auth-fix.contract.sha256").unlink()
        self.assertEqual(self.run_finish().returncode, 4)
        r = self.run_finish("--skip-checks")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("照合を飛ばしました", r.stderr)

    def test_worker_role_exits_2(self):
        self.add_worker()
        r = self.run_finish("--skip-checks", env_extra={"CONDUCTOR_ROLE": "worker"})
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.row().status, "作業中")

    def test_unknown_host_exits_2(self):
        self.add_worker()
        r = self.run_finish(env_extra={"CONDUCTOR_HOST": "unknown"})
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.row().status, "作業中")

    def test_unknown_host_exits_2_even_without_tab(self):
        self.add_worker(worktree=False, tab="", pane="")
        r = self.run_finish(env_extra={"CONDUCTOR_HOST": "unknown"})
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.row().status, "作業中")

    def test_unknown_host_exits_2_for_unknown_worker(self):
        r = self.run_finish(name="nobody", env_extra={"CONDUCTOR_HOST": "unknown"})
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
