"""テストで共有する git の補助。"""
import subprocess
from pathlib import Path

GIT_ENV = ["-c", "user.name=t", "-c", "user.email=t@example.com"]


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *GIT_ENV, "-C", str(cwd), *args],
                          check=True, capture_output=True, text=True)


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path
