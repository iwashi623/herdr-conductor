#!/usr/bin/env python3
"""メイン Claude セッションを登録する: 台帳ディレクトリと main.json を作る。"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layers  # noqa: E402
from core import init_main  # noqa: E402
from core.errors import ConductorError  # noqa: E402


def main(argv=None) -> int:
    try:
        host = layers.host_from_env(os.environ)
    except ConductorError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code
    p = argparse.ArgumentParser()
    p.add_argument("--main", dest="main_name", default=None)
    p.add_argument("--cwd", default=None)
    p.add_argument("--retire", action="store_true",
                   help="main.json を削除する (tasks.md などは残す)")
    p.add_argument("--force", action="store_true",
                   help="同じペインを別名のメインが使っていても登録する")
    p.add_argument("--probe", action="store_true",
                   help="ペインを得られるかだけを確かめる (何も書かない)")
    args = p.parse_args(argv)
    if not args.probe and not args.main_name:
        p.error("--main is required unless --probe is given")
    try:
        if args.probe:
            init_main.probe(host)
            return 0
        out, warnings = init_main.run(
            host, args.main_name, Path(args.cwd) if args.cwd else None, args.retire, args.force)
    except ConductorError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code
    for w in warnings:
        print(w, file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
