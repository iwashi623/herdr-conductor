#!/usr/bin/env python3
"""statusLine: 標準入力の JSON から1行を出す。どんな失敗でも `ctx --%` を出して exit 0。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    import layers

    agent = layers.agent_from_env(os.environ)
    try:
        stdin_text = sys.stdin.read()
    except Exception:
        stdin_text = ""
    sys.stdout.write(agent.statusline(stdin_text) + "\n")


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        sys.stdout.write("ctx --%\n")
    sys.exit(0)
