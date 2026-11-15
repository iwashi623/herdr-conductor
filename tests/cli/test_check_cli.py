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
from core.ledger import file_lock  # noqa: E402
from tests.helpers_check import NAME, CheckEnvTest  # noqa: E402

SCRIPT = SKILL / "check.py"


def chk(cmd, timeout=30):
    return {"name": "t", "cmd": cmd, "timeout": timeout}


def wait_for(path, limit=15.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if path.exists():
            return True
        time.sleep(0.02)
    return path.exists()


class CheckCliTest(CheckEnvTest):
    def env(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("CONDUCTOR_")}
        env.update(extra)
        return env

    def args(self, *extra):
        return [sys.executable, str(SCRIPT), "--main", "boss", "--name", NAME,
                "--cwd", str(self.repo), *extra]

    def run_cli(self, *extra, env=None, cwd=None):
        return subprocess.run(self.args(*extra), capture_output=True, text=True,
                              env=env or self.env(), cwd=cwd, timeout=60)

    def popen(self, *extra, env=None):
        p = subprocess.Popen(self.args(*extra), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=env or self.env())
        self.addCleanup(lambda: (p.kill() if p.poll() is None else None, p.communicate()))
        return p

    def test_worker_role_default_by_worker(self):
        self.write_contract()
        r = self.run_cli(env=self.env(CONDUCTOR_ROLE="worker"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.records()[-1]["by"], "worker")

    def test_worker_forbidden_options(self):
        self.write_contract()
        env = self.env(CONDUCTOR_ROLE="worker")
        for extra in (["--by", "main"], ["--reject", "x"], ["--extend", "1", "--agreed", "y"]):
            self.assertEqual(self.run_cli(*extra, env=env).returncode, 2, extra)
        self.assertEqual(self.records(), [])

    def test_exclusive_and_extend_validation(self):
        self.write_contract()
        for extra in (["--by", "main", "--reject", "x"], ["--extend", "1"],
                      ["--extend", "0", "--agreed", "y"], ["--extend", "1", "--agreed", " "],
                      ["--reject", "x", "--extend", "1", "--agreed", "y"]):
            self.assertEqual(self.run_cli(*extra).returncode, 2, extra)

    def test_ledger_env_works_from_root_cwd(self):
        self.write_contract()
        env = self.env(CONDUCTOR_LEDGER=str(self.ldir), CONDUCTOR_ROLE="worker", CONDUCTOR_WORKER=NAME)
        r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                           env=env, cwd="/", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_contract_errors(self):
        r = self.run_cli()
        self.assertEqual(r.returncode, 4)
        self.write_contract(checks=[chk(f"touch {self.tmp / 'm'}")])
        cpath = self.ldir / "tasks" / f"{NAME}.contract.json"
        cpath.write_text(cpath.read_text() + " ")
        r = self.run_cli()
        self.assertEqual(r.returncode, 5)
        self.assertIn("sha256", r.stderr)
        self.assertFalse((self.tmp / "m").exists())

    def append_in_subprocess(self, sigterm_when):
        code = f"""
import os, signal, sys
sys.path.insert(0, {str(SKILL)!r})
from pathlib import Path
from core import check
signal.signal(signal.SIGTERM, check.term_handler)
when = {sigterm_when!r}
if when == "after":
    real = os.write
    def w(fd, data):
        n = real(fd, data)
        os.kill(os.getpid(), signal.SIGTERM)
        return n
    os.write = w
elif when == "before":
    real = signal.pthread_sigmask
    def m(how, mask):
        old = real(how, mask)
        if how == signal.SIG_BLOCK:
            os.kill(os.getpid(), signal.SIGTERM)
        return old
    signal.pthread_sigmask = m
check._append(Path({str(self.ldir)!r}), "w1", {{"by": "main"}})
print("returned")
"""
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)

    def test_sigterm_after_write_keeps_row(self):
        r = self.append_in_subprocess("after")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "returned"), r.stderr)
        self.assertEqual(len(self.records()), 1)

    def test_sigterm_pending_before_write_writes_nothing(self):
        r = self.append_in_subprocess("before")
        self.assertEqual(r.returncode, 143, r.stderr)
        self.assertEqual(self.records(), [])

    def test_second_run_in_same_process_still_killed_by_sigterm(self):
        pidf = self.tmp / "pid"
        self.write_contract(checks=[chk("true")])
        code = f"""
import os, signal, sys
sys.path.insert(0, {str(SKILL)!r})
from pathlib import Path
from core import check, contract
signal.signal(signal.SIGTERM, check.term_handler)
ldir, repo = Path({str(self.ldir)!r}), Path({str(self.repo)!r})
print(check.run(ldir, repo, "w1", "worker")[0], flush=True)
# 2回目: 検査の中から自分に SIGTERM を送る
p = ldir / "tasks"
(p / "w1.contract.json").unlink(); (p / "w1.contract.sha256").unlink()
c = {{"checks": [{{"name": "t", "cmd": "sleep 300 & echo $! > {pidf}; kill -TERM " + str(os.getpid()) + "; wait", "timeout": 60}}],
     "protected_paths": [], "allow_paths": [], "max_returns": 3, "base": {self.base!r},
     "base_ref": {self.base_ref!r}, "defaults_sha256": "0" * 64, "agreed": "x", "deviation": None}}
contract.write_contract(ldir, "w1", c)
check.run(ldir, repo, "w1", "worker")
print("not reached")
"""
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 143, (r.stdout, r.stderr))
        self.assertNotIn("not reached", r.stdout)
        self.assertEqual(len(self.records()), 1)
        pid = int(pidf.read_text())
        end = time.monotonic() + 5
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_invalid_name_is_2(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "--main", "boss", "--name", "../x",
                            "--cwd", str(self.repo)], capture_output=True, text=True,
                           env=self.env(), timeout=60)
        self.assertEqual(r.returncode, 2)

    def test_missing_worktree_reason_is_distinct(self):
        self.write_contract()
        subprocess.run(["git", "-C", str(self.repo), "worktree", "remove", "--force", str(self.wt)], check=True)
        r = self.run_cli()
        self.assertEqual(r.returncode, 5)
        self.assertIn("worktree がありません", r.stderr)

    def test_reject_extend_preconditions(self):
        self.assertEqual(self.run_cli("--reject", "x").returncode, 4)
        self.assertEqual(self.run_cli("--extend", "1", "--agreed", "y").returncode, 4)
        self.write_contract()
        subprocess.run(["git", "-C", str(self.repo), "worktree", "remove", "--force", str(self.wt)], check=True)
        r = self.run_cli("--reject", "x")
        self.assertEqual(r.returncode, 5)
        self.assertIn("worktree", r.stderr)
        with file_lock(self.results / f"{NAME}.checks.lock"):
            self.assertEqual(self.run_cli("--reject", "x").returncode, 6)
            self.assertEqual(self.run_cli("--extend", "1", "--agreed", "y").returncode, 6)

    def test_lock_busy_exit_6(self):
        self.write_contract()
        with file_lock(self.results / f"{NAME}.checks.lock"):
            self.assertEqual(self.run_cli("--by", "main").returncode, 6)

    def test_concurrent_main_runs_one_wins(self):
        started, go = self.tmp / "started", self.tmp / "go"
        self.write_contract(checks=[chk(f"touch {started}; while [ ! -f {go} ]; do sleep 0.05; done", 60)])
        a = self.popen("--by", "main")
        self.assertTrue(wait_for(started))
        self.assertEqual(self.run_cli("--by", "main").returncode, 6)
        go.write_text("")
        a.communicate(timeout=30)
        self.assertEqual(a.returncode, 0)
        self.assertEqual(len(self.records()), 1)

    def test_sigterm_kills_group_and_writes_nothing(self):
        started, pidf = self.tmp / "started", self.tmp / "pid"
        self.write_contract(checks=[chk(f"sleep 300 & echo $! > {pidf}; touch {started}; wait", 120)])
        p = self.popen("--by", "main")
        self.assertTrue(wait_for(started))
        pid = int(pidf.read_text())
        p.send_signal(signal.SIGTERM)
        p.communicate(timeout=30)
        self.assertEqual(p.returncode, 143)
        end = time.monotonic() + 5
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertEqual(self.records(), [])
        self.assertFalse((self.results / f"{NAME}.checks.pgid").exists())

    def test_summary_main(self):
        self.write_contract(checks=[chk("echo boom; exit 1")])
        (self.wt / "u").write_text("x")
        r = self.run_cli("--by", "main")
        self.assertEqual(r.returncode, 1)
        self.assertIn("echo boom; exit 1", r.stdout)
        self.assertIn("boom", r.stdout)
        self.assertIn("未コミット", r.stdout)
        r = self.run_cli("--by", "main")
        self.assertIn("同じ状態", r.stdout)
        r = self.run_cli("--by", "worker")
        self.assertNotIn("実行したコマンド", r.stdout)

    def test_extend_cli(self):
        self.write_contract()
        r = self.run_cli("--extend", "2", "--agreed", "はい")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.records()[-1]["extend"], 2)


if __name__ == "__main__":
    unittest.main()
