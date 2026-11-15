#!/usr/bin/env python3
"""ワーカーの片付け: タブを閉じ、worktree を外し (ブランチは残す)、台帳を更新する。"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layers  # noqa: E402
from core import finish  # noqa: E402
from core.errors import ConductorError  # noqa: E402


def main(argv=None) -> int:
    try:
        host = layers.host_from_env(os.environ)
    except ConductorError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code
    p = argparse.ArgumentParser()
    p.add_argument("--main", required=True, dest="main_name")
    p.add_argument("--name", required=True)
    p.add_argument("--status", default="完了", choices=finish.FINISH_STATUSES)
    p.add_argument("--pr", default=None)
    p.add_argument("--cwd", default=None)
    p.add_argument("--skip-checks", action="store_true", dest="skip_checks")
    args = p.parse_args(argv)
    opts = finish.FinishOptions(args.main_name, args.name, args.status, args.pr, args.cwd,
                                args.skip_checks, os.environ.get("CONDUCTOR_ROLE", ""))
    try:
        out = finish.run(opts, host)
    except ConductorError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code
    for w in out["warnings"]:
        print(w, file=sys.stderr)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
