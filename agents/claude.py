"""Claude Code のエージェント層 (Agent の実装)。"""
import json
import math
from pathlib import Path

from core.protocols import HookInput, LaunchSpec

RULES_FILE = Path(__file__).resolve().parent.parent / "worker-rules.md"
GUIDE = ("あなたは conductor の指揮役です｡手順は ~/.claude/skills/conductor/SKILL.md にあります｡"
         "手元に内容がなければ Read で読んでください｡init_main.py の再実行は不要です｡")
CLEAR_LINE = "`/clear` の後です｡指揮を続けるなら人に `/conductor` を打ってもらってください｡"


def _mcp_approvals(repo_root: Path) -> dict:
    """人が出した MCP サーバーの承認だけを settings.local.json から取り出す。"""
    try:
        data = json.loads((repo_root / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    if isinstance(data.get("enableAllProjectMcpServers"), bool):
        out["enableAllProjectMcpServers"] = data["enableAllProjectMcpServers"]
    for key in ("enabledMcpjsonServers", "disabledMcpjsonServers"):
        v = data.get(key)
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[key] = v
    return out


class ClaudeAgent:
    name = "claude"
    hook_output_limit = 10000

    def launch(self, name: str, ledger_dir: Path, model: str | None, prompt: str) -> LaunchSpec:
        command = ["claude", "--name", name, "--permission-mode", "auto"]
        approvals = _mcp_approvals(Path(ledger_dir).resolve().parent.parent)
        if approvals:
            command += ["--settings", json.dumps(approvals, ensure_ascii=False)]
        command += ["--add-dir", str(Path(ledger_dir).resolve()),
                   "--append-system-prompt-file", str(RULES_FILE)]
        if model:
            command += ["--model", model]
        command.append(prompt)
        return LaunchSpec("claude", command)

    def read_hook_input(self, stdin_text: str, fallback_cwd: str) -> HookInput:
        data = {}
        try:
            parsed = json.loads(stdin_text or "{}")
            if isinstance(parsed, dict):
                data = parsed
        except Exception:
            pass
        cwd = data.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            cwd = fallback_cwd
        source = data.get("source")
        if source not in ("compact", "clear"):
            source = "other"
        return HookInput(cwd, source)

    def main_guide(self, main_name: str, ledger_dir: Path) -> str:
        return f"{GUIDE}メイン名: {main_name}｡台帳ディレクトリ: {ledger_dir}｡"

    def clear_notice(self) -> str:
        return CLEAR_LINE + "\n"

    def format_hook_output(self, text: str) -> str:
        return text

    def statusline(self, stdin_text: str) -> str:
        try:
            data = json.loads(stdin_text or "")
        except Exception:
            return "ctx --%"
        try:
            if not isinstance(data, dict):
                return "ctx --%"
            cw = data.get("context_window")
            used = cw.get("used_percentage") if isinstance(cw, dict) else None
            if isinstance(used, (int, float)) and not isinstance(used, bool) and math.isfinite(used):
                line = f"ctx {round(used)}%"
            else:
                line = "ctx --%"
            d = data.get("cwd")
            if not isinstance(d, str) or not d:
                ws = data.get("workspace")
                d = ws.get("current_dir") if isinstance(ws, dict) else None
            if isinstance(d, str) and d:
                base = Path(d).name
                if base:
                    line += f" · {base}"
            return line
        except Exception:
            return "ctx --%"
