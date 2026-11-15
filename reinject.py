#!/usr/bin/env python3
"""SessionStart (compact と clear) フック: conductor のセッションだけ台帳/指示書を再注入する。
無関係なセッションでは何も出さず、常に exit 0。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> None:
    import layers
    from core import reinject

    host = layers.host_from_env(os.environ)
    agent = layers.agent_from_env(os.environ)
    try:
        stdin_text = sys.stdin.read()
    except Exception:
        stdin_text = ""
    hook = agent.read_hook_input(stdin_text, os.getcwd())
    text = reinject.build(host, agent, os.environ, hook)
    if text:
        sys.stdout.write(agent.format_hook_output(text))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
