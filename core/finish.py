"""ワーカーの片付けの手順: タブを閉じ、worktree を外し (ブランチは残す)、台帳を更新する。"""
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from core import check, gitutil, ledger
from core.errors import ConductorError, ContractMismatch, ContractMissing, UsageError

FINISH_STATUSES = ("完了", "PR作成済み", "中止")


@dataclass
class FinishOptions:
    main_name: str
    name: str
    status: str = "完了"
    pr: str | None = None
    cwd: str | None = None
    skip_checks: bool = False
    role: str = ""


def _owns_worktree(root: Path, worktree: Path, branch: str, warnings: list) -> bool:
    if not branch:
        warnings.append(f"台帳にブランチの記録がないため、{worktree} には触れませんでした。")
        return False
    r = gitutil.git(root, "worktree", "list", "--porcelain")
    if r.returncode != 0:
        warnings.append(f"git worktree list が失敗したため、{worktree} には触れませんでした: {r.stderr.strip()}")
        return False
    target = worktree.resolve()
    for block in r.stdout.split("\n\n"):
        path = br = None
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree "):]
            elif line.startswith("branch "):
                br = line[len("branch "):]
        if path is not None and Path(path).resolve() == target:
            if br == f"refs/heads/{branch}":
                return True
            warnings.append(f"{worktree} のブランチが台帳の {branch} と一致しないため、触れませんでした (現在: {br}).")
            return False
    warnings.append(f"{worktree} は git の worktree として登録されていないため、触れませんでした。")
    return False


def _close_tab(host, row, warnings: list) -> bool:
    """タブが閉じた (または既に無い) とみなせるとき True。タブはラベルがワーカー名と一致するときだけ閉じる。"""
    try:
        label = host.tab_label(row.tab)
    except ConductorError as e:
        warnings.append(f"タブ {row.tab} のラベルを確かめられなかったため、閉じませんでした: {e}")
        return False
    if label is None:
        return True  # タブはもう存在しない
    if label != row.name:
        warnings.append(
            f"タブ {row.tab} のラベルが {label!r} で {row.name!r} と違うため、閉じませんでした。")
        return False
    try:
        host.close_tab(row.tab)
    except ConductorError as e:
        warnings.append(f"タブ {row.tab} を閉じられませんでした: {e}")
        return False
    return True


def run(opts: FinishOptions, host) -> dict:
    if opts.role == "worker":
        raise UsageError("ワーカーは finish を実行できません (CONDUCTOR_ROLE=worker)。")
    if not ledger.NAME_RE.fullmatch(opts.name):
        raise ConductorError(f"ワーカー名が不正です: {opts.name!r}")
    try:
        root = ledger.ledger_root(Path(opts.cwd or Path.cwd()).resolve())
        ldir = ledger.ledger_dir(root, opts.main_name)
    except ValueError as e:
        raise ConductorError(str(e))
    row = next((r for r in ledger.read_rows(ldir) if r.name == opts.name), None)
    if row is None:
        raise ConductorError(f"台帳 {ldir / 'tasks.md'} にワーカー {opts.name} がありません。")

    worktree = root / ".worktrees" / opts.name
    warnings = []
    owned = False
    if not row.tab and row.status in FINISH_STATUSES:
        # 前回の片付けが済んでいる。同名の worktree は別のメインのものかもしれないので触らない
        if worktree.exists():
            warnings.append(
                f"この行は片付け済みですが {worktree} があります。別のメインのものかもしれないため触れませんでした。")
    elif worktree.exists():
        owned = _owns_worktree(root, worktree, row.branch, warnings)
    if owned:
        st = gitutil.git(worktree, "status", "--porcelain", timeout=120)
        if st.returncode != 0:
            raise ConductorError(f"git status が失敗しました: {st.stderr.strip()}")
        if st.stdout.strip():
            raise ConductorError(
                f"{worktree} に未コミットの変更があります。コミットするか破棄してから再実行してください。\n{st.stdout}")

    verify = owned and opts.status in ("完了", "PR作成済み")
    with ExitStack() as stack:
        if verify and opts.skip_checks:
            warnings.append("照合を飛ばしました (--skip-checks)。")
        elif verify:
            stack.enter_context(check.checks_lock(ldir, opts.name))
            code, reason = check.verify_for_finish(ldir, root, opts.name, worktree)
            if code == 4:
                raise ContractMissing(reason)
            if code == 5:
                raise ContractMismatch(reason)
        if row.tab:
            if _close_tab(host, row, warnings):
                row.tab = ""
                row.pane = ""
        removed = False
        if owned:
            r = gitutil.git(root, "worktree", "remove", str(worktree), timeout=120)
            if r.returncode != 0:
                raise ConductorError(f"git worktree remove が失敗しました: {r.stderr.strip()}")
            removed = True
        if removed and opts.status == "中止":
            r = gitutil.git(root, "branch", "-d", row.branch)
            if r.returncode != 0:
                warnings.append(f"ブランチ {row.branch} は削除せず残しました: {r.stderr.strip()}")

        row.status = opts.status
        if opts.pr is not None:
            row.pr = opts.pr
        row.updated = ledger.iso_now()
        ledger.upsert_row(ldir, row)
        return {"name": opts.name, "status": row.status, "pr": row.pr,
                "worktree_removed": removed, "warnings": warnings}
