"""名前付きワーカーを新しいタブで起動し、台帳に記録する手順。"""
import json
from dataclasses import dataclass
from pathlib import Path

from core import contract as contract_lib
from core import gitutil, ledger
from core.errors import ConductorError, ContractMismatch, HostError


@dataclass
class SpawnOptions:
    main_name: str
    name: str
    task_file: str
    worktree: bool
    model: str | None
    summary: str
    cwd: str | None = None


def startup_prompt(task_file, main_name: str) -> str:
    return f"指示書 {task_file} を読んで作業を始めてください。報告先は {main_name} です。"


def _read_deviation(ldir: Path, name: str) -> dict | None:
    draft = ldir / "tasks" / f"{name}.contract.draft.json"
    if not draft.exists():
        return None
    try:
        data = json.loads(draft.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConductorError(f"差分の下書きを読めません ({draft}): {e}")
    try:
        return contract_lib.validate_deviation(data)
    except ConductorError as e:
        raise ConductorError(f"差分の下書きが不正です ({draft}): {e}")


def _base_ref(top) -> str:
    r = gitutil.git(top, "rev-parse", "--abbrev-ref", "HEAD")
    if r.returncode != 0:
        raise ConductorError(f"ブランチ名を取得できません: {r.stderr.strip()}")
    ref = r.stdout.strip()
    return "" if ref == "HEAD" else ref


def run(opts: SpawnOptions, host, agent) -> dict:
    workspace = host.current_workspace()
    name = opts.name
    if not ledger.NAME_RE.fullmatch(name):
        raise ConductorError(f"ワーカー名が不正です: {name!r} (小文字英字で始まる英小文字・数字・_・- の32字以内)")
    if name == opts.main_name:
        raise ConductorError(f"ワーカー名がメイン名と同じです: {name}")
    summary = opts.summary.replace("\r", " ").replace("\n", " ").strip()[:80]
    if not summary:
        raise ConductorError("--summary が空です。")
    task_file = Path(opts.task_file).expanduser().resolve()
    if not task_file.is_file():
        raise ConductorError(f"指示書がありません: {task_file}")
    main_cwd = Path(opts.cwd or Path.cwd()).resolve()
    try:
        root = ledger.ledger_root(main_cwd)
        ldir = ledger.ledger_dir(root, opts.main_name)
    except ValueError as e:
        raise ConductorError(str(e))
    if not (ldir / "main.json").exists():
        raise ConductorError(f"{ldir / 'main.json'} がありません。先に init_main.py を実行してください。")
    top = gitutil.work_tree_top(main_cwd)

    # 重複検査 (何も作る前に)
    for mj in sorted((root / ".orchestra").glob("*/main.json")):
        try:
            other = json.loads(mj.read_text(encoding="utf-8")).get("main")
        except (OSError, ValueError, AttributeError):
            continue
        if other == name:
            raise ConductorError(f"ワーカー名 {name} は別のメイン ({mj.parent.name}) の名前と同じです。")
    # このリポジトリの全メインの台帳を見る (別メインが片付けた名前の再利用で、他人の worktree を壊さないため)
    for tm in sorted((root / ".orchestra").glob("*/tasks.md")):
        if any(r.name == name for r in ledger.read_rows(tm.parent)):
            raise ConductorError(
                f"ワーカー名 {name} は、このリポジトリで以前に使われた名前 ({tm.parent.name} の台帳に行があります) で、"
                f"再利用できません。別の名前にしてください。")
    # 補助の検査: 端末のエージェント名は null のことがあり、これだけでは重複を見つけきれない
    # (メインは spawn 前に ListAgents でも確かめる)
    if name in host.live_agent_names():
        raise ConductorError(f"同名のエージェント {name} が既に動いています。")
    worktree = root / ".worktrees" / name
    branch_name = f"conductor/{name}"
    is_git = (root / ".git").exists()
    if worktree.exists():
        raise ConductorError(f"{worktree} が既にあります (前回の残り)。")
    if is_git and gitutil.git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name}").returncode == 0:
        raise ConductorError(f"ブランチ {branch_name} が既にあります (前回の残り)。")

    branch = ""
    cwd = top
    contract = None
    contract_path = None
    contract_written = False
    if opts.worktree:
        if not is_git:
            raise ConductorError(f"{root} は git 管理外のため --worktree は使えません。")
        # 契約の中身は worktree を作る前に作る (失敗しても何も残さない)
        try:
            defaults, defaults_sha = contract_lib.load_defaults(root)
        except ContractMismatch as e:
            raise ConductorError(str(e))
        deviation = _read_deviation(ldir, name)
        base = gitutil.head_sha(top)
        base_ref = _base_ref(top)
        contract = contract_lib.derive_contract(defaults, deviation, base, base_ref, defaults_sha)
        gitutil.ensure_excluded(root)
        r = gitutil.git(root, "worktree", "add", str(worktree), "-b", branch_name, base, timeout=120)
        if r.returncode != 0:
            raise ConductorError(f"git worktree add が失敗しました: {r.stderr.strip()}")
        branch = branch_name
        cwd = worktree

    def rollback() -> str:
        # この実行で作った worktree とブランチだけを戻す。git の失敗は文字列で返す
        msgs = []
        if branch:
            for a in (("worktree", "remove", "--force", str(worktree)), ("branch", "-D", branch_name)):
                try:
                    r = gitutil.git(root, *a)
                    if r.returncode != 0:
                        msgs.append(f"git {a[0]} {a[1]} が失敗しました: {r.stderr.strip()}")
                except ConductorError as e:
                    msgs.append(str(e))
        if contract_written:
            try:
                contract_lib.remove_contract(ldir, name)
            except OSError as e:
                msgs.append(f"契約ファイルを消せませんでした: {e}")
        return "\n".join(msgs)

    if contract is not None:
        # write_contract は台帳のロックを自分で取る (ここはロックの外)
        try:
            contract_path = contract_lib.write_contract(ldir, name, contract)
        except Exception as e:
            extra = rollback()
            msg = f"契約ファイルを書けませんでした: {e}" + (f"\n{extra}" if extra else "")
            raise ConductorError(msg) from e
        contract_written = True

    try:
        tab = host.create_tab(workspace, name, cwd, {
            "CONDUCTOR_ROLE": "worker",
            "CONDUCTOR_LEDGER": str(ldir),
            "CONDUCTOR_WORKER": name,
            "CONDUCTOR_MAIN": opts.main_name,
            "CONDUCTOR_BRANCH": branch,
            "CONDUCTOR_TASK_FILE": str(task_file),
            "CONDUCTOR_HOST": host.name,
            "CONDUCTOR_AGENT": agent.name,
            "CONDUCTOR_CONTRACT": str(contract_path) if contract_path else "",
        })
    except ConductorError as e:
        extra = rollback()
        if extra:
            raise ConductorError(f"{e}\n{extra}")
        raise
    tab_id, pane_id = tab.tab_id, tab.pane_id

    def put_row(status):
        ledger.upsert_row(ldir, ledger.Row(
            name=name, summary=summary, status=status, branch=branch, pr="",
            tab=tab_id, pane=pane_id, updated=ledger.iso_now()))

    try:
        put_row("作業中")  # 仮の行。以降の失敗では中止に更新する
    except Exception as e:
        try:
            host.close_tab(tab_id)
        except ConductorError:
            pass
        extra = rollback()
        raise ConductorError(f"台帳に仮の行を書けませんでした: {e}" + (f"\n{extra}" if extra else ""))
    try:
        return _start(opts, host, agent, name, task_file, ldir, tab_id, pane_id, summary, cwd, branch, contract_path)
    except BaseException as e:
        try:
            put_row("中止")
        except Exception as e2:
            if isinstance(e, ConductorError):
                raise ConductorError(f"{e}\n台帳に中止の行も書けませんでした: {e2}") from e
        raise


def _start(opts, host, agent, name, task_file, ldir, tab_id, pane_id, summary, cwd, branch, contract_path):
    spec = agent.launch(name, ldir, opts.model, startup_prompt(task_file, opts.main_name))
    try:
        host.start_agent(pane_id, name, spec)
    except HostError as e:
        raise ConductorError(
            f"agent start が失敗しました (タブ {tab_id} は残してあります): {e}\n"
            f"--- 画面 ---\n{host.read_screen(pane_id)}")
    host.show_status(pane_id, name, spec.kind_hint, summary)  # 失敗しても続行
    return {"name": name, "tab_id": tab_id, "pane_id": pane_id, "cwd": str(cwd), "branch": branch,
            "contract": str(contract_path) if contract_path else None}
