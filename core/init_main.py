"""メインのセッションを登録する手順: 台帳ディレクトリと main.json を作る。"""
import json
from pathlib import Path

from core import gitutil, ledger
from core.errors import PaneClash, UsageError


def probe(host) -> None:
    """ペインを得られるかだけを確かめる。何も書き込まない。"""
    host.require_pane()


def run(host, main: str, cwd: Path | None, retire: bool, force: bool) -> tuple[str, list[str]]:
    """(標準出力に出す台帳ディレクトリ, 標準エラー出力に出す警告) を返す。"""
    cwd = Path(cwd or Path.cwd()).resolve()
    if retire:
        try:
            ldir = ledger.ledger_dir(ledger.ledger_root(cwd), main)
        except ValueError as e:
            raise UsageError(str(e))
        (ldir / "main.json").unlink(missing_ok=True)
        return str(ldir), []

    pane = host.require_pane()
    if not cwd.is_dir():
        raise UsageError(f"--cwd がディレクトリではありません: {cwd}")
    root = ledger.ledger_root(cwd)
    try:
        ldir = ledger.ledger_dir(root, main)
    except ValueError as e:
        raise UsageError(str(e))
    mine = ldir / "main.json"
    if not force:
        clash = []
        for f in sorted((root / ".orchestra").glob("*/main.json")):
            if f.parent.name == main:
                continue
            try:
                other = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(other, dict) and other.get("pane_id") == pane:
                clash.append(f.parent.name)
        if clash:
            raise PaneClash("\n".join(clash))
    warnings = []
    if mine.exists():
        try:
            old = json.loads(mine.read_text(encoding="utf-8")).get("pane_id")
        except (OSError, ValueError, AttributeError):
            old = None
        if old and old != pane:
            warnings.append(f"警告: 同名のメイン {main} は別のペイン {old} で登録済みでした。上書きします。")
    ldir.mkdir(parents=True, exist_ok=True)
    gitutil.ensure_excluded(root)
    if not (ldir / "tasks.md").exists():
        ledger.write_rows(ldir, [])
    ledger.write_json_atomic(mine, {
        "main": main,
        "pane_id": pane,
        "cwd": str(cwd),
        "updated": ledger.iso_now(),
    })
    return str(ldir), warnings
