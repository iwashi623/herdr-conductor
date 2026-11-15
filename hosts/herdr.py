"""herdr の端末操作 (TerminalHost の実装)。HERDR_* の環境変数を読むのはこのファイルだけ。"""
import json
import os
import subprocess
import time
from pathlib import Path

from core.errors import HostError, UsageError
from core.protocols import LaunchSpec, TabRef


class HerdrHost:
    name = "herdr"

    def __init__(self, retry_interval=None, retry_total=None):
        # 環境変数は spawn の再試行 (start_agent) のときだけ読む
        self._retry_interval = retry_interval
        self._retry_total = retry_total

    def _retry_times(self):
        # タブ作成直後はシェルのプロンプト前で agent_pane_busy になることがあるため再試行する
        interval = self._retry_interval
        if interval is None:
            interval = float(os.environ.get("CONDUCTOR_SPAWN_RETRY_INTERVAL", "0.5"))
        total = self._retry_total
        if total is None:
            total = float(os.environ.get("CONDUCTOR_SPAWN_RETRY_TOTAL", "20"))
        return interval, total

    # --- 内部 ---
    def _run(self, args: list[str]):
        try:
            return subprocess.run(["herdr", *args], capture_output=True, text=True)
        except OSError as e:
            raise HostError(f"herdr を実行できません: {e}")

    @staticmethod
    def is_pane_busy(r) -> bool:
        try:
            return json.loads(r.stderr).get("error", {}).get("code") == "agent_pane_busy"
        except (ValueError, AttributeError):
            return False

    def _json(self, *args):
        r = self._run(list(args))
        if r.returncode != 0:
            raise HostError(f"herdr {' '.join(args[:2])} が失敗しました: {r.stderr.strip()}")
        try:
            return json.loads(r.stdout)
        except ValueError:
            raise HostError(f"herdr {' '.join(args[:2])} の出力を JSON として読めません: {r.stdout[:200]}")

    # --- TerminalHost ---
    def current_pane(self) -> str | None:
        return os.environ.get("HERDR_PANE_ID") or None

    def require_pane(self) -> str:
        pane = os.environ.get("HERDR_PANE_ID", "")
        if not pane:
            raise UsageError("HERDR_PANE_ID が空です。herdr のペイン内で実行してください。")
        return pane

    def current_workspace(self) -> str:
        if os.environ.get("HERDR_ENV") != "1":
            raise HostError("HERDR_ENV=1 ではありません。herdr のペイン内で実行してください。")
        workspace = os.environ.get("HERDR_WORKSPACE_ID", "")
        if not workspace:
            raise HostError("HERDR_WORKSPACE_ID が空です。herdr のペイン内で実行してください。")
        return workspace

    def create_tab(self, workspace: str, label: str, cwd: Path, env: dict[str, str]) -> TabRef:
        args = ["tab", "create", "--workspace", workspace, "--cwd", str(cwd), "--label", label, "--no-focus"]
        for k, v in env.items():
            args += ["--env", f"{k}={v}"]
        tab = self._json(*args)
        try:
            tab_id = tab["result"]["tab"]["tab_id"]
        except (KeyError, TypeError):
            tab_id = None
        try:
            pane_id = tab["result"]["root_pane"]["pane_id"]
        except (KeyError, TypeError):
            pane_id = None
        if not tab_id or not pane_id:
            if isinstance(tab_id, str) and tab_id:
                try:
                    self._run(["tab", "close", tab_id])
                except HostError:
                    pass
            raise HostError(f"tab create の応答に tab_id / pane_id がありません: {tab}")
        return TabRef(tab_id, pane_id)

    def start_agent(self, pane_id: str, name: str, spec: LaunchSpec) -> None:
        retry_interval, retry_total = self._retry_times()
        deadline = time.monotonic() + retry_total
        while True:
            r = self._run(["agent", "start", name, "--kind", spec.kind_hint, "--pane", pane_id,
                           "--timeout", "60000", "--", *spec.command[1:]])
            if r.returncode == 0 or not self.is_pane_busy(r) or time.monotonic() >= deadline:
                break
            time.sleep(retry_interval)
        if r.returncode != 0:
            raise HostError(r.stderr.strip())

    def read_screen(self, pane_id: str, lines: int = 40) -> str:
        try:
            r = self._run(["agent", "read", pane_id, "--source", "visible", "--lines", str(lines)])
        except HostError:
            return ""
        return r.stdout if r.returncode == 0 else ""

    def tab_label(self, tab_id: str) -> str | None:
        g = self._run(["tab", "get", tab_id])
        if g.returncode != 0:
            try:
                code = json.loads(g.stderr).get("error", {}).get("code")
            except (ValueError, AttributeError):
                code = None
            if code == "tab_not_found":
                return None
            raise HostError(g.stderr.strip())
        try:
            return json.loads(g.stdout)["result"]["tab"]["label"]
        except (ValueError, KeyError, TypeError):
            raise HostError(f"tab get の出力を読めませんでした: {g.stdout[:200]}")

    def close_tab(self, tab_id: str) -> None:
        r = self._run(["tab", "close", tab_id])
        if r.returncode != 0:
            raise HostError(r.stderr.strip())

    def live_agent_names(self) -> list[str]:
        agents = self._json("agent", "list").get("result", {}).get("agents", [])
        return [a["name"] for a in agents if isinstance(a, dict) and a.get("name")]

    def show_status(self, pane_id: str, display_name: str, kind_hint: str, summary: str) -> bool:
        try:
            r = self._run(["pane", "report-metadata", pane_id, "--source", "conductor", "--agent", kind_hint,
                           "--display-agent", display_name, "--state-label", f"working={summary}"])
        except HostError:
            return False
        return r.returncode == 0
