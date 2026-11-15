#!/usr/bin/env python3
"""名前付きワーカー Claude を herdr の新しいタブで起動し、台帳に記録する。"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layers  # noqa: E402
from core import spawn  # noqa: E402
from core.errors import ConductorError  # noqa: E402


def main(argv=None) -> int:
    try:
        host = layers.host_from_env(os.environ)
        agent = layers.agent_from_env(os.environ)
    except ConductorError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code
    p = argparse.ArgumentParser()
    p.add_argument("--main", required=True, dest="main_name")
    p.add_argument("--name", required=True)
    p.add_argument("--task-file", required=True)
    p.add_argument("--worktree", action="store_true")
    p.add_argument("--model", default=None)
    p.add_argument("--summary", required=True)
    p.add_argument("--cwd", default=None)
    args = p.parse_args(argv)
    opts = spawn.SpawnOptions(args.main_name, args.name, args.task_file, args.worktree,
                              args.model, args.summary, args.cwd)
    try:
        out = spawn.run(opts, host, agent)
    except ConductorError as e:
        print(str(e), file=sys.stderr)
        return e.exit_code
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
