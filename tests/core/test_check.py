import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
from core import check  # noqa: E402
from core.errors import ContractMismatch, ContractMissing, LockBusy  # noqa: E402
from core.ledger import file_lock  # noqa: E402
from tests.helpers import git  # noqa: E402
from tests.helpers_check import NAME, CheckEnvTest  # noqa: E402


def chk(cmd, name="t", timeout=30):
    return {"name": name, "cmd": cmd, "timeout": timeout}


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_dead(pid, limit=5.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


class RunTest(CheckEnvTest):
    def run_main(self, by="main"):
        return check.run(self.ldir, self.repo, NAME, by)

    def test_pass_and_row_shape(self):
        self.write_contract()
        code, row = self.run_main("worker")
        self.assertEqual(code, 0)
        for k in ("ts", "by", "ok", "head", "dirty", "checks", "protected_violations", "warnings", "same_state"):
            self.assertIn(k, row)
        self.assertEqual(row["by"], "worker")
        self.assertEqual(self.records(), [row])
        self.assertEqual(set(row["checks"][0]), {"name", "cmd", "exit", "seconds", "tail"})

    def test_fail_returns_1(self):
        self.write_contract(checks=[chk("exit 3")])
        code, row = self.run_main()
        self.assertEqual((code, row["ok"], row["checks"][0]["exit"]), (1, False, 3))

    def test_missing_sha_is_4_and_tamper_is_5_without_running(self):
        with self.assertRaises(ContractMissing):
            self.run_main()
        marker = self.tmp / "marker"
        self.write_contract(checks=[chk(f"touch {marker}")])
        cpath = self.ldir / "tasks" / f"{NAME}.contract.json"
        cpath.write_text(cpath.read_text() + " ")
        with self.assertRaises(ContractMismatch):
            self.run_main()
        self.assertFalse(marker.exists())

    def test_no_worktree_is_5(self):
        self.write_contract()
        git(self.repo, "worktree", "remove", "--force", str(self.wt))
        with self.assertRaises(ContractMismatch):
            self.run_main()

    def test_lock_busy(self):
        self.write_contract()
        with check.checks_lock(self.ldir, NAME):
            with self.assertRaises(LockBusy):
                self.run_main()
            with self.assertRaises(LockBusy):
                check.reject(self.ldir, self.repo, NAME, "x")
            with self.assertRaises(LockBusy):
                check.extend(self.ldir, NAME, 1, "ok")

    def test_uncommitted_warning_only_for_main(self):
        self.write_contract()
        (self.wt / "u").write_text("x")
        self.assertTrue(any("未コミット" in w for w in self.run_main("main")[1]["warnings"]))
        self.assertFalse(any("未コミット" in w for w in self.run_main("worker")[1]["warnings"]))

    def test_check_creating_file_warns(self):
        self.write_contract(checks=[chk("touch made")])
        _, row = self.run_main("worker")
        self.assertTrue(any("変わりました" in w for w in row["warnings"]))

    def test_env_and_stdin(self):
        self.write_contract(checks=[chk('test "$CI" = 1 && [ -z "$(cat)" ]')])
        self.assertEqual(self.run_main("worker")[0], 0)


class ReturnCountTest(CheckEnvTest):
    def setUp(self):
        super().setUp()
        self.write_contract(checks=[chk("exit 1")])

    def fail(self):
        self.commit(f"f{len(self.records())}")
        return check.run(self.ldir, self.repo, NAME, "main")

    def test_sequence_a(self):
        self.assertEqual([self.fail()[0], self.fail()[0], self.fail()[0]], [1, 1, 3])
        self.assertEqual(check.extend(self.ldir, NAME, 1, "はい")[0], 0)
        self.assertEqual(self.fail()[0], 3)

    def test_sequence_b(self):
        self.assertEqual([self.fail()[0], self.fail()[0]], [1, 1])
        check.extend(self.ldir, NAME, 1, "はい")
        self.assertEqual(self.fail()[0], 1)
        self.assertEqual(self.fail()[0], 3)

    def test_same_state_not_counted(self):
        self.commit("a")
        c1, r1 = check.run(self.ldir, self.repo, NAME, "main")
        c2, r2 = check.run(self.ldir, self.repo, NAME, "main")
        self.assertEqual((c1, c2, r1["same_state"], r2["same_state"]), (1, 1, False, True))
        self.assertEqual(check.count_returns(self.records()), 1)

    def test_uncommitted_content_change_counts(self):
        self.commit("a")
        (self.wt / "u").write_text("1")
        _, r1 = check.run(self.ldir, self.repo, NAME, "main")
        (self.wt / "u").write_text("2")
        _, r2 = check.run(self.ldir, self.repo, NAME, "main")
        self.assertNotEqual(r1["dirty"], r2["dirty"])
        self.assertFalse(r2["same_state"])
        self.assertEqual(check.count_returns(self.records()), 2)

    def test_worker_not_counted_and_stays_1(self):
        for _ in range(3):
            self.fail()
        for i in range(2):
            self.commit(f"w{i}")
            self.assertEqual(check.run(self.ldir, self.repo, NAME, "worker")[0], 1)
        self.assertEqual(check.count_returns(self.records()), 3)

    def test_reject_counts(self):
        out = []
        for i in range(3):
            self.commit(f"r{i}")
            out.append(check.reject(self.ldir, self.repo, NAME, "足りない")[0])
        self.assertEqual(out, [1, 1, 3])
        last = self.records()[-1]
        self.assertEqual((last["by"], last["ok"], last["reject"]), ("main", False, "足りない"))
        self.assertIn("same_state", last)

    def test_reject_preconditions(self):
        with self.assertRaises(ContractMissing):
            check.reject(self.ldir, self.repo, "nope", "x")
        git(self.repo, "worktree", "remove", "--force", str(self.wt))
        with self.assertRaises(ContractMismatch):
            check.reject(self.ldir, self.repo, NAME, "x")

    def test_extend_needs_sha(self):
        with self.assertRaises(ContractMissing):
            check.extend(self.ldir, "nope", 1, "ok")
        code, row = check.extend(self.ldir, NAME, 2, "ok")
        self.assertEqual((code, row["by"], row["extend"], row["agreed"]), (0, "human", 2, "ok"))


class DirtyTest(CheckEnvTest):
    def test_content_change_alters_dirty_and_nothing_left(self):
        self.commit("a")
        (self.wt / "u").write_text("1")
        git(self.wt, "add", "u")
        before = git(self.wt, "diff", "--cached").stdout
        h1, d1 = check.worktree_state(self.wt, self.results)
        (self.wt / "u").write_text("2")
        git(self.wt, "add", "u")
        mid = git(self.wt, "diff", "--cached").stdout
        h2, d2 = check.worktree_state(self.wt, self.results)
        self.assertEqual(h1, h2)
        self.assertNotEqual(d1, d2)
        self.assertEqual(git(self.wt, "diff", "--cached").stdout, mid)
        self.assertNotEqual(before, mid)
        self.assertEqual(git(self.wt, "status", "--porcelain").stdout.split(), ["A", "u"])
        self.assertEqual([p.name for p in self.results.iterdir()], [])


class ProtectedTest(CheckEnvTest):
    def setUp(self):
        super().setUp()
        self.marker = self.tmp / "marker"
        self.write_contract(checks=[chk(f"touch {self.marker}")], protected_paths=["infra/**"])

    def assert_violation(self, path):
        code, row = check.run(self.ldir, self.repo, NAME, "worker")
        self.assertEqual(code, 1)
        self.assertEqual(row["protected_violations"], [path])
        self.assertEqual(row["checks"], [])
        self.assertFalse(self.marker.exists())

    def test_committed(self):
        self.commit("infra/a")
        self.assert_violation("infra/a")

    def test_uncommitted(self):
        (self.wt / "infra").mkdir()
        (self.wt / "README").write_text("changed\n")
        (self.wt / "infra" / "b").write_text("x")
        git(self.wt, "add", "-A")
        self.assert_violation("infra/b")

    def test_unstaged_modification_of_tracked(self):
        self.commit("infra/t")
        newbase = git(self.wt, "rev-parse", "HEAD").stdout.strip()
        (self.ldir / "tasks" / f"{NAME}.contract.json").unlink()
        (self.ldir / "tasks" / f"{NAME}.contract.sha256").unlink()
        self.base = newbase
        self.write_contract(checks=[chk(f"touch {self.marker}")], protected_paths=["infra/**"], base_ref="")
        self.assertEqual(check.run(self.ldir, self.repo, NAME, "worker")[0], 0)
        self.marker.unlink()
        (self.wt / "infra" / "t").write_text("changed\n")
        self.assert_violation("infra/t")

    def test_untracked(self):
        (self.wt / "infra").mkdir()
        (self.wt / "infra" / "c").write_text("x")
        self.assert_violation("infra/c")

    def test_rename_new_path(self):
        self.commit("old")
        git(self.wt, "mv", "old", "infra_x")
        (self.wt / "infra").mkdir()
        git(self.wt, "mv", "infra_x", "infra/new")
        self.assert_violation("infra/new")

    def test_base_ref_advanced_uses_merge_base(self):
        self.commit("mine")
        (self.repo / "infra").mkdir()
        (self.repo / "infra" / "main-side").write_text("x")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "main moved")
        git(self.wt, "merge", "-q", "--no-edit", self.base_ref)
        self.assertTrue((self.wt / "infra" / "main-side").exists())
        code, row = check.run(self.ldir, self.repo, NAME, "worker")
        self.assertEqual((code, row["protected_violations"]), (0, []))
        self.assertTrue(self.marker.exists())

    def test_empty_base_ref_uses_base(self):
        (self.ldir / "tasks" / f"{NAME}.contract.json").unlink()
        (self.ldir / "tasks" / f"{NAME}.contract.sha256").unlink()
        self.write_contract(checks=[chk("true")], protected_paths=["infra/**"], base_ref="")
        self.commit("infra/a")
        self.assertEqual(check.run(self.ldir, self.repo, NAME, "worker")[1]["protected_violations"], ["infra/a"])

    def test_allow_paths(self):
        (self.ldir / "tasks" / f"{NAME}.contract.json").unlink()
        (self.ldir / "tasks" / f"{NAME}.contract.sha256").unlink()
        self.write_contract(checks=[chk("true")], protected_paths=["infra/**"], allow_paths=["infra/ok/**"])
        self.commit("infra/ok/a")
        self.assertEqual(check.run(self.ldir, self.repo, NAME, "worker")[0], 0)


class GitFailureTest(CheckEnvTest):
    def test_git_error_is_5_and_no_row(self):
        from unittest import mock
        from core.errors import ConductorError
        self.write_contract()
        with mock.patch("core.check.git", side_effect=ConductorError("timeout")):
            with self.assertRaises(ContractMismatch) as cm:
                check.run(self.ldir, self.repo, NAME, "main")
            self.assertEqual(cm.exception.exit_code, 5)
            self.assertIn("git の失敗", str(cm.exception))
            with self.assertRaises(ContractMismatch):
                check.changed_paths(self.wt, self.base_ref, self.base)
        self.assertEqual(self.records(), [])


class RunChecksTest(CheckEnvTest):
    def test_timeout_kills_group(self):
        pidf = self.tmp / "pid"
        res = check.run_checks(self.wt, [chk(f"sleep 300 & echo $! > {pidf}; wait", timeout=1)],
                               self.tmp / "pgid")
        self.assertEqual(res[0]["exit"], 124)
        pid = int(pidf.read_text())
        self.assertTrue(wait_dead(pid))
        self.assertFalse((self.tmp / "pgid").exists())

    def test_leftover_processes_killed_after_exit(self):
        pidf = self.tmp / "pid"
        res = check.run_checks(self.wt, [chk(f"sleep 300 & echo $! > {pidf}")], self.tmp / "pgid")
        self.assertEqual(res[0]["exit"], 0)
        self.assertTrue(wait_dead(int(pidf.read_text())))

    def test_large_output(self):
        res = check.run_checks(
            self.wt, [chk("head -c 1048576 /dev/zero | tr '\\0' a; echo END")], self.tmp / "pgid")
        self.assertEqual(res[0]["exit"], 0)
        self.assertEqual(len(res[0]["tail"]), 2000)
        self.assertTrue(res[0]["tail"].endswith("END\n"))

    def test_pgid_file_written_during_run(self):
        seen = self.tmp / "seen"
        pg = self.tmp / "pgid"
        check.run_checks(self.wt, [chk(f"cp {pg} {seen}")], pg)
        rec = json.loads(seen.read_text())
        self.assertEqual(rec["cmd"], f"cp {pg} {seen}")
        self.assertIsInstance(rec["pgid"], int)


class OrphanTest(CheckEnvTest):
    def start(self, cmd):
        p = subprocess.Popen(["sh", "-c", cmd], start_new_session=True)
        self.addCleanup(lambda: (os.killpg(p.pid, signal.SIGKILL) if p.poll() is None else None, p.wait()))
        time.sleep(0.2)
        return p

    def test_orphan_killed(self):
        cmd = "sleep 300; true"
        p = self.start(cmd)
        pg = self.tmp / "pgid"
        pg.write_text(json.dumps({"pgid": p.pid, "cmd": cmd}))
        warns = check.reap_orphans(pg)
        self.assertEqual(len(warns), 1)
        self.assertEqual(p.wait(timeout=5), -signal.SIGKILL)
        self.assertFalse(pg.exists())

    def test_bare_cmd_exec_killed(self):
        p = self.start("sleep 300")
        pg = self.tmp / "pgid"
        pg.write_text(json.dumps({"pgid": p.pid, "cmd": "sleep 300"}))
        self.assertEqual(len(check.reap_orphans(pg)), 1)
        self.assertEqual(p.wait(timeout=5), -signal.SIGKILL)

    def test_empty_cmd_not_killed(self):
        p = self.start("sleep 300; true")
        pg = self.tmp / "pgid"
        pg.write_text(json.dumps({"pgid": p.pid, "cmd": "  "}))
        self.assertEqual(check.reap_orphans(pg), [])
        self.assertIsNone(p.poll())

    def test_pgid_differs_not_killed(self):
        # p は新しいセッションの先頭ではない子 (pgid は親と同じ) として pid だけ記録に使う
        cmd = "sleep 300"
        p = subprocess.Popen(["sh", "-c", "exec " + cmd])  # pgid はテスト自身のグループ
        self.addCleanup(lambda: (p.kill(), p.wait()))
        time.sleep(0.2)
        pg = self.tmp / "pgid"
        pg.write_text(json.dumps({"pgid": p.pid, "cmd": cmd}))
        self.assertEqual(check.reap_orphans(pg), [])
        self.assertIsNone(p.poll())

    def test_different_command_not_killed(self):
        p = self.start("sleep 300; true")
        pg = self.tmp / "pgid"
        pg.write_text(json.dumps({"pgid": p.pid, "cmd": "something else"}))
        self.assertEqual(check.reap_orphans(pg), [])
        self.assertIsNone(p.poll())
        self.assertFalse(pg.exists())

    def test_run_reaps_orphan(self):
        self.write_contract()
        cmd = "sleep 300; true"
        p = self.start(cmd)
        self.results.mkdir(exist_ok=True)
        (self.results / f"{NAME}.checks.pgid").write_text(json.dumps({"pgid": p.pid, "cmd": cmd}))
        _, row = check.run(self.ldir, self.repo, NAME, "worker")
        self.assertTrue(row["warnings"])
        p.wait(timeout=5)


class RejectLoopTest(CheckEnvTest):
    def test_pass_reject_loop_flags_same_state(self):
        self.write_contract()
        self.commit("a")
        self.assertEqual(check.run(self.ldir, self.repo, NAME, "main")[0], 0)
        c1, r1 = check.reject(self.ldir, self.repo, NAME, "足りない")
        self.assertEqual(check.run(self.ldir, self.repo, NAME, "main")[0], 0)
        c2, r2 = check.reject(self.ldir, self.repo, NAME, "足りない")
        self.assertEqual((c1, c2, r1["same_state"], r2["same_state"]), (1, 1, False, True))
        self.assertEqual(check.count_returns(self.records()), 1)


class RecordsTest(CheckEnvTest):
    def test_read_skips_bad_lines(self):
        self.results.mkdir()
        (self.results / f"{NAME}.checks.jsonl").write_text('{"by":"main"}\nnot json\n[1]\n{"by":"human","extend":1}\n')
        self.assertEqual(len(check.read_records(self.ldir, NAME)), 2)
        self.assertEqual(check.read_records(self.ldir, "none"), [])

    def test_same_state_uses_last_main_failure_not_pass(self):
        recs = [{"by": "main", "ok": False, "head": "h", "dirty": "d"},
                {"by": "main", "ok": True, "head": "h", "dirty": "d"}]
        self.assertTrue(check.is_same_state(recs, "h", "d"))
        self.assertFalse(check.is_same_state(recs, "h", "e"))
        self.assertFalse(check.is_same_state([recs[1]], "h", "d"))

    def test_last_main_includes_reject(self):
        recs = [{"by": "main", "ok": False, "head": "h", "dirty": "d", "reject": "x"}, {"by": "worker"}]
        self.assertEqual(check.last_main_record(recs), recs[0])
        self.assertTrue(check.is_same_state(recs, "h", "d"))
        self.assertFalse(check.is_same_state(recs, "h", "e"))
        self.assertIsNone(check.last_main_record([]))


if __name__ == "__main__":
    unittest.main()
