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
from core import contract as clib  # noqa: E402
from tests.helpers import git, init_repo  # noqa: E402

SCRIPT = SKILL / "spawn.py"
FAKES = SKILL / "tests" / "fakes"

DEFAULTS = {
    "agent list": {"stdout": '{"id":"cli:agent:list","result":{"agents":[]}}', "exit": 0},
    "tab create": {
        "stdout": '{"id":"cli:tab:create","result":{"tab":{"tab_id":"w1:t5"},"root_pane":{"pane_id":"w1:p9"}}}',
        "exit": 0,
    },
    "agent start": {
        "stdout": '{"id":"cli:agent:start","result":{"agent":{"name":"auth-fix","pane_id":"w1:p9","agent_status":"idle"}}}',
        "exit": 0,
    },
    "agent read": {"stdout": "screen text\n", "exit": 0},
    "pane report-metadata": {"stdout": '{"id":"cli:pane:report_metadata","result":{"type":"ok"}}', "exit": 0},
}


class SpawnCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = init_repo(self.tmp / "repo")
        self.log = self.tmp / "herdr.log"
        self.task = self.tmp / "task.md"
        self.task.write_text("do it\n")
        ldir = lib.ledger_dir(self.repo, "boss")
        ldir.mkdir(parents=True, exist_ok=True)
        lib.write_rows(ldir, [])
        (ldir / "main.json").write_text(json.dumps(
            {"main": "boss", "pane_id": "w1:p2", "cwd": str(self.repo), "updated": "now"}))
        self.write_defaults()

    def write_defaults(self):
        orch = self.repo / ".orchestra"
        lib.write_json_atomic(orch / "contract-defaults.json", {
            "checks": [{"name": "test", "cmd": "true", "timeout": 10}],
            "protected_paths": [], "max_returns": 3, "agreed": "ok"})
        (orch / "contract-defaults.sha256").write_text(
            clib.sha256_file(orch / "contract-defaults.json") + "\n")

    def tearDown(self):
        self._tmp.cleanup()

    def run_spawn(self, *extra, name="auth-fix", responses=None, task=None, no_summary=False, env_extra=None):
        resp = dict(DEFAULTS)
        resp.update(responses or {})
        env = {k: v for k, v in os.environ.items()
               if k not in ("HERDR_ENV", "HERDR_WORKSPACE_ID", "CONDUCTOR_HOST", "CONDUCTOR_AGENT")}
        env["PATH"] = f"{FAKES}{os.pathsep}{env.get('PATH', '')}"
        env["FAKE_HERDR_LOG"] = str(self.log)
        env["CONDUCTOR_SPAWN_RETRY_INTERVAL"] = "0.01"
        env["CONDUCTOR_SPAWN_RETRY_TOTAL"] = "0.3"
        env["FAKE_HERDR_RESPONSES"] = json.dumps(resp)
        env["HERDR_ENV"] = "1"
        env["HERDR_WORKSPACE_ID"] = "w1"
        env.update(env_extra or {})
        extra = list(extra)
        if "--summary" not in extra and not no_summary:
            extra += ["--summary", "s"]
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--main", "boss", "--name", name,
             "--task-file", str(task or self.task), "--cwd", str(self.repo), *extra],
            capture_output=True, text=True, env=env,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def calls_of(self, a, b):
        return [c for c in self.calls() if c[:2] == [a, b]]

    def test_success_worktree(self):
        r = self.run_spawn("--worktree", "--model", "sonnet", "--summary", "fix auth")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        wt = self.repo / ".worktrees" / "auth-fix"
        self.assertTrue(wt.is_dir())
        self.assertEqual(out, {"name": "auth-fix", "tab_id": "w1:t5", "pane_id": "w1:p9",
                               "cwd": str(wt), "branch": "conductor/auth-fix",
                               "contract": str(lib.ledger_dir(self.repo, "boss") / "tasks"
                                               / "auth-fix.contract.json")})
        git(self.repo, "rev-parse", "--verify", "refs/heads/conductor/auth-fix")
        tab = self.calls_of("tab", "create")[0]
        self.assertIn("--no-focus", tab)
        self.assertIn("CONDUCTOR_ROLE=worker", tab)
        self.assertEqual(tab[tab.index("--workspace") + 1], "w1")
        self.assertEqual(tab[tab.index("--cwd") + 1], str(wt))
        ldir = lib.ledger_dir(self.repo, "boss")
        self.assertIn(f"CONDUCTOR_LEDGER={ldir}", tab)
        start = self.calls_of("agent", "start")[0]
        for tok in ("--permission-mode", "auto", "--append-system-prompt-file",
                    str(SKILL / "worker-rules.md"), "--name", "auth-fix", "--model", "sonnet"):
            self.assertIn(tok, start)
        self.assertEqual(start[start.index("--pane") + 1], "w1:p9")
        meta = self.calls_of("pane", "report-metadata")[0]
        self.assertIn("working=fix auth", meta)
        rows = lib.read_rows(ldir)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "auth-fix")
        self.assertEqual(rows[0].status, "作業中")
        self.assertEqual(rows[0].tab, "w1:t5")
        self.assertEqual(rows[0].pane, "w1:p9")
        self.assertEqual(rows[0].branch, "conductor/auth-fix")

    def test_worktree_without_defaults_exits_4(self):
        (self.repo / ".orchestra" / "contract-defaults.json").unlink()
        (self.repo / ".orchestra" / "contract-defaults.sha256").unlink()
        r = self.run_spawn("--worktree")
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(self.calls_of("tab", "create"), [])
        self.assertFalse((self.repo / ".worktrees").exists())

    def test_success_without_worktree_contract_null(self):
        r = self.run_spawn()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(json.loads(r.stdout)["contract"])

    def test_summary_required(self):
        r = self.run_spawn(no_summary=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.calls_of("tab", "create"), [])

    def test_agent_start_fails(self):
        resp = {"agent start": {"stdout": "", "exit": 1, "stderr": '{"error":{"code":"agent_not_ready"}}'}}
        r = self.run_spawn(responses=resp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("screen text", r.stderr)
        self.assertEqual(len(self.calls_of("agent", "read")), 1)

    def test_task_path_with_space_and_japanese(self):
        d = self.tmp / "指示 書"
        d.mkdir()
        t = d / "タスク.md"
        t.write_text("x")
        r = self.run_spawn(task=t)
        self.assertEqual(r.returncode, 0, r.stderr)
        last = self.calls_of("agent", "start")[0][-1]
        self.assertIn(str(t), last)
        self.assertIn("boss", last)

    def test_worker_tab_env_has_layer_names(self):
        r = self.run_spawn()
        self.assertEqual(r.returncode, 0, r.stderr)
        tab = self.calls_of("tab", "create")[0]
        self.assertIn("CONDUCTOR_HOST=herdr", tab)
        self.assertIn("CONDUCTOR_AGENT=claude", tab)

    def test_unknown_host_exits_2_before_any_work(self):
        r = self.run_spawn(env_extra={"CONDUCTOR_HOST": "unknown"})
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.repo / ".worktrees").exists())

    def test_unknown_agent_exits_2(self):
        r = self.run_spawn(env_extra={"CONDUCTOR_AGENT": "unknown"})
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
