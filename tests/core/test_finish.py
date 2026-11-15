import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
import core.ledger as lib  # noqa: E402
from core import check, finish, spawn  # noqa: E402
from core import contract as contract_lib  # noqa: E402
from core.errors import ConductorError, HostError  # noqa: E402
from tests.fakes.layers import FakeAgent, FakeHost  # noqa: E402
from tests.helpers import git, init_repo  # noqa: E402
from tests.helpers_check import make_contract  # noqa: E402

LABELED = {"w1:t5": "auth-fix"}


class FinishTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = init_repo(self.tmp / "repo")
        self.ldir = lib.ledger_dir(self.repo, "boss")
        self.ldir.mkdir(parents=True)
        lib.write_rows(self.ldir, [])

    def tearDown(self):
        self._tmp.cleanup()

    def prepare_pass(self, name="auth-fix"):
        base = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        contract_lib.write_contract(self.ldir, name, make_contract(base, ""))
        code, _ = check.run(self.ldir, self.repo, name, "main")
        assert code == 0

    def add_worker(self, name="auth-fix", worktree=True, verified=True):
        branch = ""
        if worktree:
            branch = f"conductor/{name}"
            git(self.repo, "worktree", "add", str(self.repo / ".worktrees" / name), "-b", branch)
            if verified:
                self.prepare_pass(name)
        lib.upsert_row(self.ldir, lib.Row(
            name=name, summary="s", status="作業中", branch=branch, pr="",
            tab="w1:t5", pane="w1:p9", updated="old"))

    def finish(self, host, *, name="auth-fix", status="完了", pr=None, **kw):
        return finish.run(finish.FinishOptions("boss", name, status, pr, str(self.repo), **kw), host)

    def row(self, name="auth-fix"):
        return next(r for r in lib.read_rows(self.ldir) if r.name == name)

    def test_unknown_worker(self):
        host = FakeHost()
        with self.assertRaises(ConductorError) as cm:
            self.finish(host, name="nobody")
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("nobody", str(cm.exception))
        self.assertEqual(host.calls, [])

    def test_dirty_worktree_refused(self):
        self.add_worker()
        wt = self.repo / ".worktrees" / "auth-fix"
        (wt / "wip.txt").write_text("x")
        host = FakeHost(labels=LABELED)
        with self.assertRaises(ConductorError) as cm:
            self.finish(host)
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("未コミット", str(cm.exception))
        self.assertEqual(host.calls, [])
        self.assertTrue(wt.exists())
        self.assertEqual(self.row().status, "作業中")

    def test_default_status_is_done(self):
        self.add_worker()
        finish.run(finish.FinishOptions("boss", "auth-fix", cwd=str(self.repo)), FakeHost(labels=LABELED))
        self.assertEqual(self.row().status, "完了")

    def test_finish_without_worktree(self):
        self.add_worker(worktree=False)
        host = FakeHost(labels=LABELED)
        self.finish(host)
        self.assertEqual(host.calls, [("tab_label", ("w1:t5",)), ("close_tab", ("w1:t5",))])
        self.assertEqual(self.row().status, "完了")

    def test_tab_close_failure_does_not_block(self):
        self.add_worker()
        host = FakeHost(labels=LABELED, fail={"close_tab": HostError("no such tab")})
        out = self.finish(host)
        self.assertIn("w1:t5", "\n".join(out["warnings"]))
        self.assertFalse((self.repo / ".worktrees" / "auth-fix").exists())
        self.assertEqual(self.row().status, "完了")

    def test_other_main_same_name_worktree_untouched(self):
        # 別メインが同名ワーカーの worktree (別ブランチ) を作っている
        wt = self.repo / ".worktrees" / "auth-fix"
        git(self.repo, "worktree", "add", str(wt), "-b", "conductor/other-main-auth-fix")
        lib.upsert_row(self.ldir, lib.Row(
            name="auth-fix", summary="s", status="作業中", branch="conductor/auth-fix",
            pr="", tab="w1:t5", pane="w1:p9", updated="old"))
        out = self.finish(FakeHost(labels=LABELED))
        self.assertTrue(wt.exists())
        self.assertIn("触れません", "\n".join(out["warnings"]))
        self.assertEqual(self.row().status, "完了")

    def test_empty_branch_does_not_touch_worktree(self):
        wt = self.repo / ".worktrees" / "auth-fix"
        git(self.repo, "worktree", "add", str(wt), "-b", "conductor/auth-fix")
        lib.upsert_row(self.ldir, lib.Row(
            name="auth-fix", summary="s", status="作業中", branch="",
            pr="", tab="w1:t5", pane="w1:p9", updated="old"))
        out = self.finish(FakeHost(labels=LABELED))
        self.assertTrue(wt.exists())
        self.assertIn("触れません", "\n".join(out["warnings"]))

    def test_label_mismatch_does_not_close(self):
        self.add_worker()
        host = FakeHost(labels={"w1:t5": "someone-else"})
        out = self.finish(host)
        self.assertEqual(host.calls, [("tab_label", ("w1:t5",))])
        self.assertIn("someone-else", out["warnings"][0])
        row = self.row()
        self.assertEqual((row.tab, row.pane), ("w1:t5", "w1:p9"))
        self.assertFalse((self.repo / ".worktrees" / "auth-fix").exists())

    def test_missing_tab_clears_without_close(self):
        self.add_worker()
        host = FakeHost(labels={})
        self.finish(host)
        self.assertEqual(host.calls, [("tab_label", ("w1:t5",))])
        row = self.row()
        self.assertEqual((row.tab, row.pane), ("", ""))

    def test_agent_list_not_used(self):
        self.add_worker()
        host = FakeHost(labels=LABELED, agents=["x"])
        self.finish(host)
        self.assertEqual(host.calls_of("live_agent_names"), [])
        self.assertEqual(host.calls_of("close_tab"), [("w1:t5",)])

    def test_second_finish_makes_no_herdr_calls(self):
        # 端末の呼び出しがないこと
        self.add_worker()
        self.finish(FakeHost(labels=LABELED))
        host = FakeHost()
        self.finish(host, status="PR作成済み", pr="https://example.com/pr/2")
        self.assertEqual(host.calls, [])
        row = self.row()
        self.assertEqual((row.status, row.pr), ("PR作成済み", "https://example.com/pr/2"))

    def test_abort_deletes_unmerged_empty_branch(self):
        self.add_worker()
        self.finish(FakeHost(labels=LABELED), status="中止")
        self.assertEqual(git(self.repo, "branch", "--list", "conductor/auth-fix").stdout.strip(), "")

    def test_abort_keeps_branch_with_commits(self):
        self.add_worker()
        wt = self.repo / ".worktrees" / "auth-fix"
        (wt / "a.txt").write_text("x")
        git(wt, "add", "a.txt")
        git(wt, "commit", "-q", "-m", "work")
        out = self.finish(FakeHost(labels=LABELED), status="中止")
        self.assertIn("conductor/auth-fix", git(self.repo, "branch", "--list", "conductor/auth-fix").stdout)
        self.assertIn("conductor/auth-fix", "\n".join(out["warnings"]))
        self.assertEqual(self.row().status, "中止")

    def test_tab_get_other_failure_keeps_tab(self):
        self.add_worker()
        host = FakeHost(fail={"tab_label": HostError("socket error")})
        out = self.finish(host)
        self.assertEqual(host.calls, [("tab_label", ("w1:t5",))])
        self.assertIn("w1:t5", "\n".join(out["warnings"]))
        self.assertIn("socket error", "\n".join(out["warnings"]))
        row = self.row()
        self.assertEqual((row.tab, row.pane), ("w1:t5", "w1:p9"))

    def test_reused_name_worktree_untouched_after_finished_row(self):
        # main A finished x (中止: branch deleted). Main B later made a live worktree with the same name.
        self.add_worker()
        self.finish(FakeHost(labels=LABELED), status="中止")
        wt = self.repo / ".worktrees" / "auth-fix"
        git(self.repo, "worktree", "add", str(wt), "-b", "conductor/auth-fix")
        host = FakeHost()
        out = self.finish(host, status="中止")
        self.assertTrue(wt.exists())
        self.assertIn("conductor/auth-fix", git(self.repo, "branch", "--list", "conductor/auth-fix").stdout)
        self.assertIn(".worktrees", "\n".join(out["warnings"]))
        self.assertEqual(host.calls, [])

    def test_spawn_blocked_after_finish_by_other_main(self):
        # A finishes x, B cannot reuse the name
        self.add_worker()
        self.finish(FakeHost(labels=LABELED), status="中止")
        ldir_b = lib.ledger_dir(self.repo, "bob")
        ldir_b.mkdir(parents=True)
        lib.write_rows(ldir_b, [])
        (ldir_b / "main.json").write_text(json.dumps(
            {"main": "bob", "pane_id": "w1:p3", "cwd": str(self.repo), "updated": "now"}))
        task = self.tmp / "t.md"
        task.write_text("x")
        host = FakeHost()
        opts = spawn.SpawnOptions("bob", "auth-fix", str(task), True, None, "s", str(self.repo))
        with self.assertRaises(ConductorError) as cm:
            spawn.run(opts, host, FakeAgent())
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertEqual(host.calls_of("create_tab"), [])
        self.assertFalse((self.repo / ".worktrees" / "auth-fix").exists())

    def test_invalid_name_rejected(self):
        host = FakeHost()
        with self.assertRaises(ConductorError) as cm:
            self.finish(host, name="../x")
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertEqual(host.calls, [])

    # --- 照合 (DESIGN-3 §4.10) ---
    def commit_in_wt(self, fname="more.txt"):
        wt = self.repo / ".worktrees" / "auth-fix"
        (wt / fname).write_text("x")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", fname)

    def assert_refused(self, code, host=None, **kw):
        host = host or FakeHost(labels=LABELED)
        with self.assertRaises(ConductorError) as cm:
            self.finish(host, **kw)
        self.assertEqual(cm.exception.exit_code, code)
        self.assertEqual(host.calls_of("close_tab"), [])
        self.assertTrue((self.repo / ".worktrees" / "auth-fix").exists())
        self.assertEqual(self.row().status, "作業中")
        return str(cm.exception)

    def test_verified_pass_finishes(self):
        self.add_worker()
        host = FakeHost(labels=LABELED)
        self.finish(host, status="PR作成済み", pr="u")
        self.assertEqual(host.calls_of("close_tab"), [("w1:t5",)])
        self.assertEqual(self.row().status, "PR作成済み")

    def test_commit_after_pass_is_5(self):
        self.add_worker()
        self.commit_in_wt()
        msg = self.assert_refused(5)
        self.assertIn("記録の問題", msg)

    def test_last_main_failed_is_5(self):
        self.add_worker()
        wt = self.repo / ".worktrees" / "auth-fix"
        contract_lib.remove_contract(self.ldir, "auth-fix")
        base = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        contract_lib.write_contract(self.ldir, "auth-fix", make_contract(
            base, "", [{"name": "t", "cmd": "false", "timeout": 30}]))
        check.run(self.ldir, self.repo, "auth-fix", "main")
        self.assertIn("記録の問題", self.assert_refused(5))

    def test_reject_after_pass_is_5(self):
        self.add_worker()
        check.reject(self.ldir, self.repo, "auth-fix", "no")
        self.assertIn("記録の問題", self.assert_refused(5))

    def test_no_record_is_5(self):
        self.add_worker(verified=False)
        base = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        contract_lib.write_contract(self.ldir, "auth-fix", make_contract(base, ""))
        self.assertIn("記録の問題", self.assert_refused(5))

    def test_git_failure_in_verify_is_1(self):
        from unittest import mock
        from core.errors import ContractMismatch
        self.add_worker()
        host = FakeHost(labels=LABELED)
        wt = self.repo / ".worktrees" / "auth-fix"
        with mock.patch.object(check, "worktree_state", side_effect=ContractMismatch("git の失敗 (x)")):
            with self.assertRaises(ConductorError) as cm:
                self.finish(host)
        self.assertNotIsInstance(cm.exception, ContractMismatch)
        self.assertEqual(cm.exception.exit_code, 1)
        self.assertIn("git の失敗", str(cm.exception))
        self.assertTrue(wt.exists())
        self.assertEqual(host.calls_of("close_tab"), [])
        self.assertEqual(self.row().status, "作業中")

    def test_contract_tampered_is_5(self):
        self.add_worker()
        p = self.ldir / "tasks" / "auth-fix.contract.json"
        p.write_text(p.read_text() + " ")
        self.assertIn("sha256", self.assert_refused(5))

    def test_no_sha_file_is_4(self):
        self.add_worker()
        (self.ldir / "tasks" / "auth-fix.contract.sha256").unlink()
        self.assert_refused(4)

    def test_lock_held_is_6(self):
        self.add_worker()
        with check.checks_lock(self.ldir, "auth-fix"):
            self.assert_refused(6)

    def test_skip_checks_finishes_with_warning(self):
        self.add_worker(verified=False)
        host = FakeHost(labels=LABELED)
        out = self.finish(host, skip_checks=True)
        self.assertIn("照合を飛ばしました", "\n".join(out["warnings"]))
        self.assertEqual(self.row().status, "完了")

    def test_abort_does_not_verify(self):
        self.add_worker(verified=False)
        self.finish(FakeHost(labels=LABELED), status="中止")
        self.assertEqual(self.row().status, "中止")

    def test_dirty_stops_before_verification(self):
        self.add_worker(verified=False)
        (self.repo / ".worktrees" / "auth-fix" / "wip.txt").write_text("x")
        self.assertIn("未コミット", self.assert_refused(1))

    def test_worker_role_is_2_and_untouched(self):
        self.add_worker()
        host = FakeHost(labels=LABELED)
        with self.assertRaises(ConductorError) as cm:
            self.finish(host, role="worker", skip_checks=True)
        self.assertEqual(cm.exception.exit_code, 2)
        self.assertEqual(host.calls, [])
        self.assertEqual(self.row().status, "作業中")


if __name__ == "__main__":
    unittest.main()
