"""検査の実行と記録 (振る舞いの層)。端末もエージェントも使わない。"""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from core.contract import load_contract, match_protected
from core.errors import ConductorError, ContractMismatch, ContractMissing
from core.gitutil import git
from core.ledger import file_lock, iso_now, write_json_atomic

GIT_TIMEOUT = 120
TAIL_CHARS = 2000
TERM_GRACE = 5

_current_pgid = None
_committed = False


def _results(ldir: Path) -> Path:
    return Path(ldir) / "results"


def _jsonl(ldir: Path, name: str) -> Path:
    return _results(ldir) / f"{name}.checks.jsonl"


def checks_lock(ldir: Path, name: str):
    return file_lock(_results(ldir) / f"{name}.checks.lock", blocking=False)


def _git(worktree: Path, *args: str, env: dict | None = None):
    try:
        return git(worktree, *args, timeout=GIT_TIMEOUT, env=env)
    except ConductorError as e:
        raise ContractMismatch(f"git の失敗 ({worktree}): {e}")


def _git_out(worktree: Path, *args: str, env: dict | None = None) -> str:
    r = _git(worktree, *args, env=env)
    if r.returncode != 0:
        raise ContractMismatch(f"git の失敗 (git {' '.join(args[:2])}, {worktree}): {r.stderr.strip()}")
    return r.stdout


def worktree_state(worktree: Path, tmpdir: Path) -> tuple[str, str]:
    worktree = Path(worktree)
    head = _git_out(worktree, "rev-parse", "HEAD").strip()
    real = Path(_git_out(worktree, "rev-parse", "--git-path", "index").strip())
    if not real.is_absolute():
        real = worktree / real
    tmpdir = Path(tmpdir)
    tmpdir.mkdir(parents=True, exist_ok=True)
    tmp_index = (tmpdir / f".index.{uuid.uuid4().hex}").resolve()
    try:
        if real.exists():
            shutil.copyfile(real, tmp_index)
        env = {"GIT_INDEX_FILE": str(tmp_index)}
        _git_out(worktree, "add", "-A", env=env)
        dirty = _git_out(worktree, "write-tree", env=env).strip()
    finally:
        tmp_index.unlink(missing_ok=True)
        Path(str(tmp_index) + ".lock").unlink(missing_ok=True)
    return head, dirty


def changed_paths(worktree: Path, base_ref: str, base: str) -> list[str]:
    worktree = Path(worktree)
    point = base
    if base_ref:
        r = _git(worktree, "merge-base", base_ref, "HEAD")
        if r.returncode == 0 and r.stdout.strip():
            point = r.stdout.strip()
    paths: set[str] = set()
    out = _git_out(worktree, "diff", "--no-renames", "-z", "--name-only", point, "HEAD")
    paths.update(p for p in out.split("\0") if p)
    out = _git_out(worktree, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all")
    for ent in out.split("\0"):
        if len(ent) > 3:
            paths.add(ent[3:])
    return sorted(paths)


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def kill_running() -> None:
    """実行中の検査のプロセスグループを止める (SIGTERM のハンドラ用)。"""
    if _current_pgid is not None:
        _killpg(_current_pgid, signal.SIGKILL)


def term_handler(signum, frame) -> None:
    """SIGTERM のハンドラ。記録を書いた後なら何もしない。それまでは検査を止めて 143 で終わる。"""
    if _committed:
        return
    kill_running()
    raise SystemExit(143)


def _tail(path: str) -> str:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - 4 * TAIL_CHARS))
        data = f.read()
    return data.decode("utf-8", errors="replace")[-TAIL_CHARS:]


def _run_one(worktree: Path, check: dict, pgid_file: Path) -> dict:
    global _current_pgid
    cmd = check["cmd"]
    fd, outpath = tempfile.mkstemp(prefix="conductor-check-")
    proc = None
    start = time.monotonic()
    try:
        old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            with os.fdopen(fd, "wb") as outf:
                proc = subprocess.Popen(
                    ["sh", "-c", cmd], cwd=str(worktree), stdin=subprocess.DEVNULL,
                    stdout=outf, stderr=subprocess.STDOUT, start_new_session=True,
                    env={**os.environ, "CI": "1"})
            pgid = proc.pid
            _current_pgid = pgid
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old)
        write_json_atomic(pgid_file, {"pgid": pgid, "cmd": cmd})
        try:
            code = proc.wait(timeout=check["timeout"])
        except subprocess.TimeoutExpired:
            _killpg(pgid, signal.SIGTERM)
            try:
                proc.wait(timeout=TERM_GRACE)
            except subprocess.TimeoutExpired:
                _killpg(pgid, signal.SIGKILL)
                proc.wait()
            code = 124
        seconds = round(time.monotonic() - start, 1)
        return {"name": check["name"], "cmd": cmd, "exit": code,
                "seconds": seconds, "tail": _tail(outpath)}
    finally:
        if proc is not None:
            _killpg(proc.pid, signal.SIGKILL)
            try:
                proc.wait(timeout=TERM_GRACE)
            except subprocess.TimeoutExpired:
                pass
        _current_pgid = None
        Path(pgid_file).unlink(missing_ok=True)
        try:
            os.unlink(outpath)
        except OSError:
            pass


def run_checks(worktree: Path, checks: list[dict], pgid_file: Path) -> list[dict]:
    return [_run_one(Path(worktree), c, Path(pgid_file)) for c in checks]


def reap_orphans(pgid_file: Path) -> list[str]:
    pgid_file = Path(pgid_file)
    if not pgid_file.exists():
        return []
    try:
        rec = json.loads(pgid_file.read_text(encoding="utf-8"))
        pgid, cmd = int(rec["pgid"]), str(rec["cmd"])
    except (OSError, ValueError, KeyError, TypeError):
        pgid_file.unlink(missing_ok=True)
        return ["前回の検査の記録 (pgid) が読めなかったため消しました。"]
    try:
        r = subprocess.run(["ps", "-o", "pgid=,command=", "-p", str(pgid)],
                           capture_output=True, text=True, timeout=10)
        line = r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        line = ""
    warnings = []
    if line and cmd.strip():
        parts = line.split(None, 1)
        command = parts[1] if len(parts) > 1 else ""
        if parts[0] == str(pgid) and (command.startswith(f"sh -c {cmd}") or command.startswith(cmd)):
            _killpg(pgid, signal.SIGKILL)
            warnings.append(f"前回の検査のプロセスグループ {pgid} が残っていたため止めました: {cmd}")
    pgid_file.unlink(missing_ok=True)
    return warnings


def read_records(ldir: Path, name: str) -> list[dict]:
    path = _jsonl(ldir, name)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def count_returns(records: list[dict]) -> int:
    return len({(r.get("head"), r.get("dirty")) for r in records
                if r.get("by") == "main" and r.get("ok") is False})


def return_limit(contract: dict, records: list[dict]) -> int:
    extra = sum(r["extend"] for r in records
                if r.get("by") == "human" and isinstance(r.get("extend"), int))
    return contract["max_returns"] + extra


def last_main_record(records: list[dict]) -> dict | None:
    for r in reversed(records):
        if r.get("by") == "main":
            return r
    return None


def is_same_state(records: list[dict], head: str, dirty: str) -> bool:
    for r in reversed(records):
        if r.get("by") == "main" and r.get("ok") is False:
            return (r.get("head"), r.get("dirty")) == (head, dirty)
    return False


def verify_for_finish(ldir: Path, root: Path, name: str, worktree: Path) -> tuple[int, str]:
    """finish の照合。(0, "") か (4 または 5, 理由)。呼ぶ側が checks_lock を持つこと。"""
    try:
        contract = load_contract(ldir, name)
    except ContractMissing as e:
        return 4, str(e)
    except ContractMismatch as e:
        return 5, f"契約の照合の失敗 (契約の sha256 の不一致または形式の不正): {e}"
    del contract
    try:
        head, dirty = worktree_state(worktree, _results(ldir))
    except ContractMismatch as e:
        raise ConductorError(str(e))
    last = last_main_record(read_records(ldir, name))
    if last is None:
        return 5, "記録の問題: by=main の記録がありません。メインが check.py --by main で合格させてください。"
    if last.get("ok") is not True:
        return 5, "記録の問題: 最後の by=main の記録が不合格です。"
    if (last.get("head"), last.get("dirty")) != (head, dirty):
        return 5, "記録の問題: 最後の by=main の合格の後に worktree が変わりました (head か dirty の不一致)。check.py --by main をやり直してください。"
    return 0, ""


def _begin() -> None:
    global _committed
    _committed = False


def _append(ldir: Path, name: str, row: dict) -> None:
    """SIGTERM を止めて書く。書く前に SIGTERM が来ていれば書かずに解除して 143 で終わる。
    書いた後に来た SIGTERM は捨てる (行があるのに 143 になることを避ける)。"""
    global _committed
    path = _jsonl(ldir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    _committed = False
    old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    try:
        if signal.SIGTERM in signal.sigpending():
            return  # finally で解除され、ハンドラが 143 で終わらせる
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        _committed = True  # 以後の SIGTERM はハンドラが無視する
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def _worktree(root: Path, name: str) -> Path:
    wt = Path(root) / ".worktrees" / name
    if not wt.is_dir():
        raise ContractMismatch(f"worktree がありません: {wt}")
    return wt


def _exit_for_failure(contract: dict, ldir: Path, name: str) -> int:
    records = read_records(ldir, name)
    return 3 if count_returns(records) >= return_limit(contract, records) else 1


def run(ldir: Path, root: Path, name: str, by: str) -> tuple[int, dict]:
    _begin()
    ldir = Path(ldir)
    results = _results(ldir)
    with checks_lock(ldir, name):
        warnings = reap_orphans(results / f"{name}.checks.pgid")
        contract = load_contract(ldir, name)
        wt = _worktree(root, name)
        head, dirty = worktree_state(wt, results)
        if by == "main":
            tree = _git_out(wt, "rev-parse", "HEAD^{tree}").strip()
            if tree != dirty:
                warnings.append("未コミットの変更があります。ワーカーは報告の前に commit してください。")
        paths = changed_paths(wt, contract["base_ref"], contract["base"])
        violations = match_protected(paths, contract["protected_paths"], contract["allow_paths"])
        checks: list[dict] = []
        if not violations:
            checks = run_checks(wt, contract["checks"], results / f"{name}.checks.pgid")
            _, dirty_after = worktree_state(wt, results)
            if dirty_after != dirty:
                warnings.append("検査の実行中にファイルが変わりました (検査がファイルを作ったか変えました)。")
        ok = not violations and all(c["exit"] == 0 for c in checks)
        same = (not ok) and by == "main" and is_same_state(read_records(ldir, name), head, dirty)
        row = {"ts": iso_now(), "by": by, "ok": ok, "head": head, "dirty": dirty,
               "checks": checks, "protected_violations": violations,
               "warnings": warnings, "same_state": same}
        _append(ldir, name, row)
        if ok:
            return 0, row
        if by == "worker":
            return 1, row
        return _exit_for_failure(contract, ldir, name), row


def reject(ldir: Path, root: Path, name: str, reason: str) -> tuple[int, dict]:
    _begin()
    ldir = Path(ldir)
    results = _results(ldir)
    with checks_lock(ldir, name):
        contract = load_contract(ldir, name)
        wt = _worktree(root, name)
        head, dirty = worktree_state(wt, results)
        same = is_same_state(read_records(ldir, name), head, dirty)
        row = {"ts": iso_now(), "by": "main", "ok": False, "head": head, "dirty": dirty,
               "reject": reason, "same_state": same}
        _append(ldir, name, row)
        return _exit_for_failure(contract, ldir, name), row


def extend(ldir: Path, name: str, n: int, agreed: str) -> tuple[int, dict]:
    _begin()
    ldir = Path(ldir)
    with checks_lock(ldir, name):
        sha = ldir / "tasks" / f"{name}.contract.sha256"
        if not sha.exists():
            raise ContractMissing(f"契約がありません (sha256 ファイルがない): {sha}")
        row = {"ts": iso_now(), "by": "human", "extend": n, "agreed": agreed}
        _append(ldir, name, row)
        return 0, row


def format_summary(row: dict, by: str) -> str:
    lines = []
    if row.get("by") == "human":
        return f"差し戻しの上限を {row['extend']} 回延ばしました。"
    lines.append("合格" if row["ok"] else "不合格")
    if "reject" in row:
        lines.append(f"差し戻しの理由: {row['reject']}")
    for c in row.get("checks", []):
        lines.append(f"- {'OK' if c['exit'] == 0 else 'NG'} {c['name']} (exit {c['exit']}, {c['seconds']}秒)")
    if by == "main" and row.get("checks"):
        lines.append("実行したコマンド:")
        lines.extend(f"  {c['cmd']}" for c in row["checks"])
    for c in row.get("checks", []):
        if c["exit"] != 0:
            tail = [l for l in c["tail"].splitlines() if l.strip()][-8:]
            lines.append(f"[{c['name']}] 出力の末尾:")
            lines.extend(f"  {l}" for l in tail)
    if row.get("protected_violations"):
        lines.append("protected_paths の違反 (検査は実行していません):")
        lines.extend(f"  {p}" for p in row["protected_violations"])
    for w in row.get("warnings", []):
        lines.append(f"警告: {w}")
    if row.get("same_state"):
        lines.append("同じ状態です (前回の不合格から head も dirty も変わっていません)。")
    return "\n".join(lines)
