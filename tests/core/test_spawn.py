import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
import core.ledger as lib  # noqa: E402
from core import contract as clib  # noqa: E402
from core import spawn  # noqa: E402
from core.errors import ConductorError, HostError  # noqa: E402
from tests.fakes.layers import FakeAgent, FakeHost  # noqa: E402
from tests.helpers import git, init_repo  # noqa: E402


DEFAULTS = {
    "checks": [{"name": "test", "cmd": "true", "timeout": 10},
               {"name": "lint", "cmd": "true", "timeout": 10}],
    "protected_paths": ["infra/**"],
    "max_returns": 3,
    "agreed": "2026-11-15T10:00:00+09:00 人の返答: はい",
}


def write_defaults(root, data=None, draft=False):
    orch = Path(root) / ".orchestra"
    orch.mkdir(parents=True, exist_ok=True)
    if draft:
        lib.write_json_atomic(orch / clib.DEFAULTS_DRAFT_NAME, data or DEFAULTS)
        return
    lib.write_json_atomic(orch / clib.DEFAULTS_NAME, data or DEFAULTS)
    (orch / clib.DEFAULTS_SHA_NAME).write_text(clib.sha256_file(orch / clib.DEFAULTS_NAME) + "\n")


class SpawnTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = init_repo(self.tmp / "repo")
        self.task = self.tmp / "task.md"
        self.task.write_text("do it\n")
        self.host = FakeHost()
        self.init_main(self.repo)
        write_defaults(self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def init_main(self, root, main="boss"):
        ldir = lib.ledger_dir(root, main)
        ldir.mkdir(parents=True, exist_ok=True)
        lib.write_rows(ldir, [])
        (ldir / "main.json").write_text(json.dumps(
            {"main": main, "pane_id": "w1:p2", "cwd": str(root), "updated": "now"}))
        return ldir

    def spawn(self, *, name="auth-fix", worktree=False, model=None, summary="s",
              cwd=None, task=None, main="boss", host=None):
        opts = spawn.SpawnOptions(main, name, str(task or self.task), worktree, model, summary,
                                  str(cwd or self.repo))
        return spawn.run(opts, host or self.host, FakeAgent())

    def fails(self, **kw):
        with self.assertRaises(ConductorError) as cm:
            self.spawn(**kw)
        self.assertEqual(cm.exception.exit_code, 1)
        return cm.exception

    def test_rejects_bad_name(self):
        self.fails(name="Auth")
        self.assertEqual(self.host.calls_of("create_tab"), [])
        self.fails(name="a\n")
        self.assertEqual(self.host.calls_of("create_tab"), [])

    def test_requires_main_json(self):
        e = self.fails(main="other")
        self.assertIn("init_main.py", str(e))
        self.assertEqual(self.host.calls_of("create_tab"), [])

    def test_duplicate_live_agent(self):
        host = FakeHost(agents=["auth-fix"])
        e = self.fails(host=host)
        self.assertIn("auth-fix", str(e))
        self.assertEqual(host.calls_of("create_tab"), [])

    def test_duplicate_leftover_worktree(self):
        (self.repo / ".worktrees" / "auth-fix").mkdir(parents=True)
        self.fails(worktree=True)
        self.assertEqual(self.host.calls_of("create_tab"), [])

    def test_duplicate_branch(self):
        git(self.repo, "branch", "conductor/auth-fix")
        self.fails(worktree=True)
        self.assertEqual(self.host.calls_of("create_tab"), [])

    def test_worktree_in_non_git(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        self.init_main(plain)
        self.fails(worktree=True, cwd=plain)
        self.assertEqual(self.host.calls_of("create_tab"), [])
        self.assertFalse((plain / ".worktrees").exists())

    def test_success_without_worktree(self):
        out = self.spawn()
        self.assertEqual(out["branch"], "")
        self.assertEqual(out["cwd"], str(self.repo))
        self.assertFalse((self.repo / ".worktrees").exists())
        self.assertEqual(out["tab_id"], "w1:t5")
        self.assertEqual(out["pane_id"], "w1:p9")
        (ws, label, cwd, env), = self.host.calls_of("create_tab")
        self.assertEqual((ws, label, cwd), ("w1", "auth-fix", self.repo))
        self.assertEqual(env["CONDUCTOR_ROLE"], "worker")
        self.assertEqual(env["CONDUCTOR_HOST"], "fake")
        self.assertEqual(env["CONDUCTOR_AGENT"], "fake")
        (pane, name, spec), = self.host.calls_of("start_agent")
        self.assertEqual((pane, name, spec.kind_hint), ("w1:p9", "auth-fix", "fake"))
        self.assertEqual(spec.command[-1], spawn.startup_prompt(self.task, "boss"))
        self.assertEqual(self.host.calls_of("show_status"), [("w1:p9", "auth-fix", "fake", "s")])

    def test_env_branch_and_task_file(self):
        self.spawn(worktree=True)
        env = self.host.calls_of("create_tab")[0][3]
        self.assertEqual(env["CONDUCTOR_BRANCH"], "conductor/auth-fix")
        self.assertEqual(env["CONDUCTOR_TASK_FILE"], str(self.task))
        self.assertEqual(env["CONDUCTOR_LEDGER"], str(lib.ledger_dir(self.repo, "boss")))
        host2 = FakeHost()
        self.spawn(name="plain-one", host=host2)
        env = host2.calls_of("create_tab")[0][3]
        self.assertEqual(env["CONDUCTOR_BRANCH"], "")

    def test_rejects_name_equal_to_main(self):
        self.fails(name="boss")
        self.assertEqual(self.host.calls_of("create_tab"), [])

    def test_tab_create_fails_rolls_back_worktree(self):
        host = FakeHost(fail={"create_tab": HostError("boom")})
        self.fails(worktree=True, host=host)
        self.assertFalse((self.repo / ".worktrees" / "auth-fix").exists())
        b = git(self.repo, "branch", "--list", "conductor/auth-fix")
        self.assertEqual(b.stdout.strip(), "")

    def test_tab_create_missing_ids_rolls_back_worktree(self):
        host = FakeHost(fail={"create_tab": HostError("tab create の応答に tab_id / pane_id がありません")})
        self.fails(worktree=True, host=host)
        self.assertFalse((self.repo / ".worktrees" / "auth-fix").exists())
        self.assertEqual(lib.read_rows(lib.ledger_dir(self.repo, "boss")), [])

    def test_agent_start_fails_records_aborted_row(self):
        host = FakeHost(fail={"start_agent": HostError('{"error":{"code":"agent_not_ready"}}')})
        e = self.fails(worktree=True, host=host)
        self.assertIn("fake screen", str(e))
        rows = lib.read_rows(lib.ledger_dir(self.repo, "boss"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row.name, row.status, row.tab, row.pane, row.branch),
                         ("auth-fix", "中止", "w1:t5", "w1:p9", "conductor/auth-fix"))
        self.assertTrue((self.repo / ".worktrees" / "auth-fix").exists())
        self.assertEqual(host.calls_of("close_tab"), [])
        self.assertEqual(len(host.calls_of("read_screen")), 1)

    def test_summary_newlines_and_truncate(self):
        self.spawn(summary="a\nb" + "x" * 100)
        row = lib.read_rows(lib.ledger_dir(self.repo, "boss"))[0]
        self.assertEqual(len(row.summary), 80)
        self.assertTrue(row.summary.startswith("a bx"))

    def test_linked_worktree_main(self):
        linked = self.tmp / "linked"
        git(self.repo, "worktree", "add", "-q", str(linked), "-b", "feat")
        git(linked, "commit", "-q", "--allow-empty", "-m", "feat work")
        head = git(linked, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(self.spawn(cwd=linked)["cwd"], str(linked))
        self.spawn(worktree=True, name="wk", cwd=linked, host=FakeHost())
        b = subprocess.run(["git", "-C", str(self.repo), "merge-base", "--is-ancestor", head,
                            "conductor/wk"], capture_output=True)
        self.assertEqual(b.returncode, 0)

    def test_name_equals_other_main(self):
        self.init_main(self.repo, "other")
        self.fails(name="other")
        self.assertEqual(self.host.calls_of("create_tab"), [])

    def test_unreadable_other_main_skipped(self):
        d = self.repo / ".orchestra" / "broken"
        d.mkdir(parents=True)
        (d / "main.json").write_text("{not json")
        self.spawn()

    def test_existing_ledger_row_rejected(self):
        lib.upsert_row(lib.ledger_dir(self.repo, "boss"), lib.Row(
            "auth-fix", "old", "完了", "", "", "", "", "now"))
        e = self.fails()
        self.assertIn("再利用", str(e))
        self.assertEqual(self.host.calls_of("create_tab"), [])

    def test_empty_summary_rejected(self):
        self.fails(summary="  \n ")
        self.assertEqual(self.host.calls_of("create_tab"), [])

    def test_rollback_git_failure_in_stderr(self):
        host = FakeHost(fail={"create_tab": HostError("boom")})
        stub = self.tmp / "stub"
        stub.mkdir()
        real = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
        g = stub / "git"
        g.write_text(f'#!/bin/sh\ncase "$*" in *"branch -D"*) echo FAILBRANCH >&2; exit 1;; esac\nexec {real} "$@"\n')
        g.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"}):
            e = self.fails(worktree=True, host=host)
        self.assertIn("FAILBRANCH", str(e))

    def test_provisional_row_exists_when_agent_start_fails(self):
        host = FakeHost(fail={"start_agent": HostError("x")})
        self.fails(host=host)
        row = lib.read_rows(lib.ledger_dir(self.repo, "boss"))[0]
        self.assertEqual((row.status, row.tab, row.pane), ("中止", "w1:t5", "w1:p9"))

    def test_name_in_other_mains_ledger_rejected(self):
        other = self.init_main(self.repo, "other")
        lib.upsert_row(other, lib.Row("auth-fix", "old", "中止", "", "", "", "", "now"))
        e = self.fails()
        self.assertIn("このリポジトリ", str(e))
        self.assertEqual(self.host.calls_of("create_tab"), [])

    def _failing_start(self, start_exc, fail_second_upsert):
        real = lib.upsert_row
        n = {"c": 0}

        def up(ldir, row):
            n["c"] += 1
            if fail_second_upsert and n["c"] >= 2:
                raise OSError("DISKFULL")
            return real(ldir, row)
        host = FakeHost(fail={"start_agent": start_exc})
        with mock.patch.object(lib, "upsert_row", up):
            try:
                self.spawn(host=host)
            except Exception as e:
                return e
        return None

    def test_abort_row_write_failure_keeps_original_error(self):
        e = self._failing_start(ConductorError("ORIGINAL"), True)
        self.assertIsInstance(e, ConductorError)
        self.assertIn("ORIGINAL", str(e))
        self.assertIn("DISKFULL", str(e))

    def test_unexpected_exception_marks_aborted_and_reraises(self):
        e = self._failing_start(KeyError("boom"), False)
        self.assertIsInstance(e, KeyError)
        row = lib.read_rows(lib.ledger_dir(self.repo, "boss"))[0]
        self.assertEqual(row.status, "中止")

    def test_missing_task_file(self):
        self.fails(task=self.tmp / "nope.md")
        self.assertEqual(self.host.calls_of("create_tab"), [])

    def test_workspace_failure_before_worktree(self):
        host = FakeHost(fail={"current_workspace": HostError("no workspace")})
        self.fails(worktree=True, host=host)
        self.assertEqual([m for m, _ in host.calls], ["current_workspace"])
        self.assertFalse((self.repo / ".worktrees").exists())
        b = git(self.repo, "branch", "--list", "conductor/auth-fix")
        self.assertEqual(b.stdout.strip(), "")

    # --- 契約 ---
    def ldir(self):
        return lib.ledger_dir(self.repo, "boss")

    def no_side_effects(self, host=None):
        self.assertEqual((host or self.host).calls_of("create_tab"), [])
        self.assertFalse((self.repo / ".worktrees" / "auth-fix").exists())
        b = git(self.repo, "branch", "--list", "conductor/auth-fix")
        self.assertEqual(b.stdout.strip(), "")

    def test_contract_created_with_defaults(self):
        out = self.spawn(worktree=True)
        jpath = self.ldir() / "tasks" / "auth-fix.contract.json"
        self.assertEqual(out["contract"], str(jpath))
        c = json.loads(jpath.read_text())
        head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        branch = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.assertEqual((c["base"], c["base_ref"]), (head, branch))
        self.assertIsNone(c["deviation"])
        self.assertEqual(c["defaults_sha256"],
                         clib.sha256_file(self.repo / ".orchestra" / "contract-defaults.json"))
        self.assertEqual(clib.load_contract(self.ldir(), "auth-fix"), c)
        env = self.host.calls_of("create_tab")[0][3]
        self.assertEqual(env["CONDUCTOR_CONTRACT"], str(jpath))

    def test_deviation_draft_merged_and_kept(self):
        dev = {"add_checks": [{"name": "e2e", "cmd": "true", "timeout": 5}],
               "drop_checks": ["lint"], "agreed": "ok"}
        draft = self.ldir() / "tasks" / "auth-fix.contract.draft.json"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(json.dumps(dev))
        out = self.spawn(worktree=True)
        c = json.loads(Path(out["contract"]).read_text())
        self.assertEqual([x["name"] for x in c["checks"]], ["test", "e2e"])
        self.assertEqual(c["deviation"], dev)
        self.assertTrue(draft.exists())

    def test_missing_defaults_exit_4(self):
        (self.repo / ".orchestra" / "contract-defaults.json").unlink()
        (self.repo / ".orchestra" / "contract-defaults.sha256").unlink()
        with self.assertRaises(ConductorError) as cm:
            self.spawn(worktree=True)
        self.assertEqual(cm.exception.exit_code, 4)
        self.no_side_effects()

    def test_defaults_failures_exit_1_without_side_effects(self):
        orch = self.repo / ".orchestra"
        dpath = self.ldir() / "tasks" / "auth-fix.contract.draft.json"

        def bad_deviation():
            dpath.parent.mkdir(parents=True, exist_ok=True)
            dpath.write_text(json.dumps({"drop_checks": ["nope"], "agreed": "ok"}))

        def both():
            write_defaults(self.repo, draft=True)

        def tampered():
            (orch / "contract-defaults.json").write_text("{}\n")

        def tracked():
            git(self.repo, "add", "-f", ".orchestra/contract-defaults.json")

        for setup in (bad_deviation, both, tampered, tracked):
            with self.subTest(setup.__name__):
                setup()
                host = FakeHost()
                with self.assertRaises(ConductorError) as cm:
                    self.spawn(worktree=True, host=host)
                self.assertEqual(cm.exception.exit_code, 1)
                self.no_side_effects(host)
                # 後始末
                git(self.repo, "rm", "-q", "--cached", "-f", "--ignore-unmatch",
                    ".orchestra/contract-defaults.json")
                dpath.unlink(missing_ok=True)
                (orch / clib.DEFAULTS_DRAFT_NAME).unlink(missing_ok=True)
                write_defaults(self.repo)

    def test_write_contract_failure_rolls_back(self):
        jpath = self.ldir() / "tasks" / "auth-fix.contract.json"
        jpath.parent.mkdir(parents=True, exist_ok=True)
        jpath.write_text("existing")
        with self.assertRaises(ConductorError) as cm:
            self.spawn(worktree=True)
        self.assertEqual(cm.exception.exit_code, 1)
        self.no_side_effects()
        self.assertEqual(jpath.read_text(), "existing")

    def test_defaults_draft_only_is_imported(self):
        (self.repo / ".orchestra" / "contract-defaults.json").unlink()
        (self.repo / ".orchestra" / "contract-defaults.sha256").unlink()
        write_defaults(self.repo, draft=True)
        out = self.spawn(worktree=True)
        self.assertTrue(out["contract"])
        self.assertTrue((self.repo / ".orchestra" / "contract-defaults.json").exists())
        self.assertFalse((self.repo / ".orchestra" / clib.DEFAULTS_DRAFT_NAME).exists())

    def test_detached_head_base_ref_empty(self):
        git(self.repo, "checkout", "-q", "--detach")
        out = self.spawn(worktree=True)
        c = json.loads(Path(out["contract"]).read_text())
        self.assertEqual(c["base_ref"], "")

    def test_tab_create_failure_removes_contract_keeps_draft(self):
        draft = self.ldir() / "tasks" / "auth-fix.contract.draft.json"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(json.dumps({"agreed": "ok"}))
        host = FakeHost(fail={"create_tab": HostError("boom")})
        self.fails(worktree=True, host=host)
        tasks = self.ldir() / "tasks"
        self.assertFalse((tasks / "auth-fix.contract.json").exists())
        self.assertFalse((tasks / "auth-fix.contract.sha256").exists())
        self.assertTrue(draft.exists())

    def test_agent_start_failure_keeps_contract(self):
        host = FakeHost(fail={"start_agent": HostError("x")})
        self.fails(worktree=True, host=host)
        self.assertTrue((self.ldir() / "tasks" / "auth-fix.contract.json").exists())

    def test_no_worktree_no_contract(self):
        (self.repo / ".orchestra" / "contract-defaults.json").unlink()
        (self.repo / ".orchestra" / "contract-defaults.sha256").unlink()
        out = self.spawn()
        self.assertIsNone(out["contract"])
        self.assertEqual(self.host.calls_of("create_tab")[0][3]["CONDUCTOR_CONTRACT"], "")
        self.assertFalse((self.ldir() / "tasks").exists())


if __name__ == "__main__":
    unittest.main()
