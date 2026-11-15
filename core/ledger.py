"""台帳(tasks.md)と名前の扱い。標準ライブラリのみ。"""
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from dataclasses import dataclass, fields
from pathlib import Path

from core.errors import ConductorError, LockBusy
from core.gitutil import git

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
STATUSES = ("作業中", "承認待ち", "人と対話中", "完了", "PR作成済み", "中止")
HEADERS = ("名前", "担当", "状態", "ブランチ", "PR", "タブ", "ペイン", "最終更新")


@dataclass
class Row:
    name: str
    summary: str
    status: str
    branch: str
    pr: str
    tab: str
    pane: str
    updated: str


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data: dict) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def file_lock(path: Path, blocking: bool = True):
    """flock(LOCK_EX) を取るコンテキストマネージャ。blocking=False で取れなければ LockBusy。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise LockBusy(f"ロックを取れません (別の処理が実行中): {path}")
        yield
    finally:
        f.close()  # close で flock も解放される


def create_exclusive(path: Path, text: str) -> None:
    """O_CREAT|O_EXCL で書く。すでにあれば ConductorError。"""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise ConductorError(f"すでにあります: {path}")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise


class _Lock:
    def __init__(self, ldir: Path):
        self._cm = file_lock(Path(ldir) / ".tasks.lock")

    def __enter__(self):
        self._cm.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)


def ledger_root(cwd: Path) -> Path:
    cwd = Path(cwd)
    r = git(cwd, "rev-parse", "--show-toplevel")
    if r.returncode == 0 and r.stdout.strip():
        top = Path(r.stdout.strip())
        # リンクされた worktree の中なら、本体の作業ツリー (共通 .git の親) を返す
        c = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if c.returncode == 0 and c.stdout.strip():
            common = Path(c.stdout.strip())
            if common.name == ".git":
                return common.parent
        return top
    return cwd


def ledger_dir(root: Path, main: str) -> Path:
    if main in ("", ".", "..") or "/" in main or any(ord(ch) < 32 or ord(ch) == 127 for ch in main):
        raise ValueError(f"invalid main name: {main!r}")
    return Path(root) / ".orchestra" / main


def _cell(value: str) -> str:
    return value.replace("|", "／").replace("\n", " ")


def read_rows(ldir: Path) -> list[Row]:
    path = Path(ldir) / "tasks.md"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells[0] == HEADERS[0] or set(cells[0]) <= set("-: "):
            continue
        cells += [""] * (len(HEADERS) - len(cells))
        rows.append(Row(*cells[: len(HEADERS)]))
    return rows


def write_rows(ldir: Path, rows: list[Row]) -> None:
    with _Lock(ldir):
        _write_rows_unlocked(ldir, rows)


def _write_rows_unlocked(ldir: Path, rows: list[Row]) -> None:
    ldir = Path(ldir)
    lines = [
        "| " + " | ".join(HEADERS) + " |",
        "|" + "|".join("---" for _ in HEADERS) + "|",
    ]
    for r in rows:
        vals = [_cell(getattr(r, f.name)) for f in fields(Row)]
        lines.append("| " + " | ".join(vals) + " |")
    _atomic_write(ldir / "tasks.md", "\n".join(lines) + "\n")


def upsert_row(ldir: Path, row: Row) -> None:
    with _Lock(ldir):
        rows = read_rows(ldir)
        for i, r in enumerate(rows):
            if r.name == row.name:
                rows[i] = row
                break
        else:
            rows.append(row)
        _write_rows_unlocked(ldir, rows)
