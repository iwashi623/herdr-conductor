"""git の呼び出し。標準ライブラリのみ。"""
import os
import subprocess
from pathlib import Path

from core.errors import ConductorError

EXCLUDES = (".orchestra/", ".worktrees/")


def git(cwd: Path, *args: str, timeout: float = 5, env: dict | None = None):
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout,
            env=None if env is None else {**os.environ, **env},
        )
    except subprocess.TimeoutExpired:
        raise ConductorError(f"git {' '.join(args[:2])} が {timeout} 秒でタイムアウトしました")
    except OSError as e:
        raise ConductorError(f"git を実行できません: {e}")


def work_tree_top(cwd: Path) -> Path:
    cwd = Path(cwd)
    r = git(cwd, "rev-parse", "--show-toplevel")
    if r.returncode == 0 and r.stdout.strip():
        return Path(r.stdout.strip())
    return cwd


def head_sha(cwd: Path) -> str:
    r = git(cwd, "rev-parse", "HEAD")
    if r.returncode != 0:
        raise ConductorError(f"HEAD を取得できません: {r.stderr.strip()}")
    return r.stdout.strip()


def is_tracked(root: Path, rel: str) -> bool:
    return git(root, "ls-files", "--error-unmatch", "--", rel).returncode == 0


def tracking_state(root: Path, rel: str) -> str:
    """rel 配下に git 追跡ファイルがあるか。"tracked" / "untracked" / "nogit" (作業ツリー外) / "error" (git の失敗)。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5, env={**os.environ, "LC_ALL": "C"},
        )
        if r.returncode != 0:
            return "nogit" if "not a git repository" in r.stderr else "error"
        r = git(root, "ls-files", "--", rel)
    except (ConductorError, OSError, subprocess.SubprocessError):
        return "error"
    if r.returncode != 0:
        return "error"
    return "tracked" if r.stdout.strip() else "untracked"


def ensure_excluded(root: Path) -> None:
    root = Path(root)
    if not (root / ".git").exists():
        return
    r = git(root, "rev-parse", "--git-common-dir")
    if r.returncode != 0 or not r.stdout.strip():
        return
    common = Path(r.stdout.strip())
    if not common.is_absolute():
        common = root / common
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.exists() else ""
    have = set(text.splitlines())
    add = [e for e in EXCLUDES if e not in have]
    if not add:
        return
    if text and not text.endswith("\n"):
        text += "\n"
    exclude.write_text(text + "\n".join(add) + "\n")
