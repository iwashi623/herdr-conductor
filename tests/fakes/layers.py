"""テスト用の偽の端末とエージェント。"""
import json
from pathlib import Path

from core.errors import UsageError
from core.protocols import HookInput, LaunchSpec, TabRef


class FakeHost:
    name = "fake"

    def __init__(self, pane=None, workspace="w1", tab=TabRef("w1:t5", "w1:p9"),
                 labels={}, agents=[], fail={}):
        self.pane = pane
        self.workspace = workspace
        self.tab = tab
        self.labels = dict(labels)
        self.agents = list(agents)
        self.fail = dict(fail)
        self.calls = []

    def _rec(self, method, *args):
        self.calls.append((method, args))
        if method in self.fail:
            raise self.fail[method]

    def calls_of(self, method):
        return [a for m, a in self.calls if m == method]

    def current_pane(self) -> str | None:
        self._rec("current_pane")
        return self.pane

    def require_pane(self) -> str:
        self._rec("require_pane")
        if self.pane is None:
            raise UsageError("pane がありません")
        return self.pane

    def current_workspace(self) -> str:
        self._rec("current_workspace")
        return self.workspace

    def create_tab(self, workspace: str, label: str, cwd: Path, env: dict[str, str]) -> TabRef:
        self._rec("create_tab", workspace, label, cwd, env)
        return self.tab

    def start_agent(self, pane_id: str, name: str, spec: LaunchSpec) -> None:
        self._rec("start_agent", pane_id, name, spec)

    def read_screen(self, pane_id: str, lines: int = 40) -> str:
        self._rec("read_screen", pane_id, lines)
        return "fake screen\n"

    def tab_label(self, tab_id: str) -> str | None:
        self._rec("tab_label", tab_id)
        return self.labels.get(tab_id)

    def close_tab(self, tab_id: str) -> None:
        self._rec("close_tab", tab_id)

    def live_agent_names(self) -> list[str]:
        self._rec("live_agent_names")
        return list(self.agents)

    def show_status(self, pane_id: str, display_name: str, kind_hint: str, summary: str) -> bool:
        self._rec("show_status", pane_id, display_name, kind_hint, summary)
        return True


class FakeAgent:
    name = "fake"

    def __init__(self, hook_output_limit=10000):
        self.hook_output_limit = hook_output_limit

    def launch(self, name: str, ledger_dir: Path, model: str | None, prompt: str) -> LaunchSpec:
        command = ["fake-agent", "--name", name, "--add-dir", str(ledger_dir)]
        if model:
            command += ["--model", model]
        command.append(prompt)
        return LaunchSpec("fake", command)

    def read_hook_input(self, stdin_text: str, fallback_cwd: str) -> HookInput:
        try:
            data = json.loads(stdin_text or "{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        cwd = data.get("cwd") if isinstance(data.get("cwd"), str) and data.get("cwd") else fallback_cwd
        source = data.get("source") if data.get("source") in ("compact", "clear") else "other"
        return HookInput(cwd, source)

    def main_guide(self, main_name: str, ledger_dir: Path) -> str:
        return f"FAKE-GUIDE main={main_name} ledger={ledger_dir} "

    def clear_notice(self) -> str:
        return "FAKE-CLEAR\n"

    def format_hook_output(self, text: str) -> str:
        return text

    def statusline(self, stdin_text: str) -> str:
        return "FAKE-STATUSLINE"
