#!/usr/bin/env python3
"""ワーカーの検査: 契約の checks を実行して記録する。--reject / --extend は記録だけ足す。"""
import argparse
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import check as core_check  # noqa: E402
from core.errors import ConductorError, ContractMismatch, UsageError  # noqa: E402
from core.ledger import NAME_RE, ledger_dir, ledger_root  # noqa: E402


def _paths(args, env) -> tuple[Path, Path]:
    led = env.get("CONDUCTOR_LEDGER")
    if led:
        ldir = Path(led)
        return ldir, ldir.parent.parent
    if not args.main_name:
        raise UsageError("--main (または環境変数 CONDUCTOR_MAIN) が必要です")
    root = ledger_root(Path(args.cwd) if args.cwd else Path.cwd())
    try:
        return ledger_dir(root, args.main_name), root
    except ValueError as e:
        raise UsageError(str(e))


def main(argv=None) -> int:
    env = os.environ
    p = argparse.ArgumentParser()
    p.add_argument("--main", dest="main_name", default=env.get("CONDUCTOR_MAIN"))
    p.add_argument("--name", default=env.get("CONDUCTOR_WORKER"))
    p.add_argument("--cwd", default=None)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--by", choices=("worker", "main"), default=None)
    g.add_argument("--reject", default=None)
    g.add_argument("--extend", type=int, default=None)
    p.add_argument("--agreed", default=None)
    args = p.parse_args(argv)
    is_worker = env.get("CONDUCTOR_ROLE") == "worker"
    if is_worker and (args.by == "main" or args.reject is not None or args.extend is not None):
        p.error("ワーカーからは --by main / --reject / --extend を使えません")
    if not args.name:
        p.error("--name (または環境変数 CONDUCTOR_WORKER) が必要です")
    if not NAME_RE.fullmatch(args.name):
        p.error(f"--name が不正です: {args.name!r}")
    if args.reject is not None and not args.reject.strip():
        p.error("--reject には理由を書いてください")
    if args.extend is not None and (args.extend < 1 or not (args.agreed or "").strip()):
        p.error("--extend は 1 以上の整数と、空でない --agreed が必要です")
    if args.agreed is not None and args.extend is None:
        p.error("--agreed は --extend と一緒に使います")
    by = args.by or ("worker" if is_worker else "main")
    signal.signal(signal.SIGTERM, core_check.term_handler)
    try:
        ldir, root = _paths(args, env)
        if args.extend is not None:
            code, row = core_check.extend(ldir, args.name, args.extend, args.agreed)
            by = "human"
        elif args.reject is not None:
            code, row = core_check.reject(ldir, root, args.name, args.reject)
        else:
            code, row = core_check.run(ldir, root, args.name, by)
    except ContractMismatch as e:
        print(str(e), file=sys.stderr)
        return e.exit_code
    except ConductorError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code
    print(core_check.format_summary(row, by))
    return code


if __name__ == "__main__":
    sys.exit(main())
