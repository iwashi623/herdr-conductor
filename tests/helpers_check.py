"""check のテストで共有する準備。"""
import tempfile
import unittest
from pathlib import Path

from core import contract as contract_lib
from core import ledger
from tests.helpers import git, init_repo

NAME = "w1"


def make_contract(base, base_ref, checks=None, **over):
    c = {"checks": checks if checks is not None else [{"name": "t", "cmd": "true", "timeout": 30}],
         "protected_paths": [], "allow_paths": [], "max_returns": 3,
         "base": base, "base_ref": base_ref, "defaults_sha256": "0" * 64,
         "agreed": "x", "deviation": None}
    c.update(over)
    return c


class CheckEnvTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.repo = init_repo(self.tmp / "repo")
        (self.repo / "README").write_text("r\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.base = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.base_ref = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        self.wt = self.repo / ".worktrees" / NAME
        git(self.repo, "worktree", "add", "-q", str(self.wt), "-b", f"conductor/{NAME}")
        self.ldir = ledger.ledger_dir(self.repo, "boss")
        (self.ldir / "tasks").mkdir(parents=True)
        self.results = self.ldir / "results"

    def write_contract(self, **kw):
        checks = kw.pop("checks", None)
        contract_lib.write_contract(
            self.ldir, NAME, make_contract(self.base, kw.pop("base_ref", self.base_ref), checks, **kw))

    def commit(self, fname="f", text=None):
        p = self.wt / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text if text is not None else f"{fname}-{len(list(self.wt.iterdir()))}\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", f"c {fname}")

    def records(self):
        from core import check
        return check.read_records(self.ldir, NAME)
