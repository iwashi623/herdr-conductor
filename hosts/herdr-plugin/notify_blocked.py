#!/usr/bin/env python3
"""pane.agent_status_changed で agent_status が blocked のとき、人に通知する。
通知は herdr notification show、発火の証跡は logs/blocked.log に1行追記する。"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parent.parent.parent / "logs" / "blocked.log"
DEDUP_SECONDS = 30
MAX_LABEL = 80
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def sanitize(text, limit=None):
    text = CONTROL.sub(" ", str(text)).strip()
    return text[:limit].strip() if limit else text


def now():
    try:
        return float(os.environ["CONDUCTOR_NOTIFY_NOW"])
    except (KeyError, ValueError):
        return time.time()


def load_state(path):
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(path, state):
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def main():
    try:
        data = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "")["data"]
        if data["agent_status"] != "blocked":
            return
    except (ValueError, KeyError, TypeError):
        return
    pane = sanitize(data.get("pane_id") or "")
    label = sanitize(data.get("display_agent") or data.get("agent") or "", MAX_LABEL) or pane
    log = Path(os.environ.get("CONDUCTOR_BLOCKED_LOG") or DEFAULT_LOG)
    state_path = log.parent / ".last_notify.json"
    t = now()
    state = load_state(state_path)
    last = state.get(pane)
    if isinstance(last, (int, float)) and 0 <= t - last < DEDUP_SECONDS:
        return
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        state[pane] = t
        save_state(state_path, state)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')}\t{pane}\t{label}\n")
    except OSError:
        pass
    herdr = os.environ.get("HERDR_BIN_PATH") or "herdr"
    try:
        subprocess.run([herdr, "notification", "show", f"[承認待ち] {label}", "--sound", "request"],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
