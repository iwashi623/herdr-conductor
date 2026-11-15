"""core が層に求める能力の定義。端末 (TerminalHost) とエージェント (Agent)。"""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class TabRef:
    tab_id: str
    pane_id: str


@dataclass(frozen=True)
class LaunchSpec:
    kind_hint: str
    command: list[str]


@dataclass(frozen=True)
class HookInput:
    cwd: str
    source: str  # "compact" | "clear" | "other"


class TerminalHost(Protocol):
    name: str

    def current_pane(self) -> str | None: ...

    def require_pane(self) -> str: ...

    def current_workspace(self) -> str: ...

    def create_tab(self, workspace: str, label: str, cwd: Path, env: dict[str, str]) -> TabRef: ...

    def start_agent(self, pane_id: str, name: str, spec: LaunchSpec) -> None: ...

    def read_screen(self, pane_id: str, lines: int = 40) -> str: ...

    def tab_label(self, tab_id: str) -> str | None: ...

    def close_tab(self, tab_id: str) -> None: ...

    def live_agent_names(self) -> list[str]: ...

    def show_status(self, pane_id: str, display_name: str, kind_hint: str, summary: str) -> bool: ...


class Agent(Protocol):
    name: str
    hook_output_limit: int

    def launch(self, name: str, ledger_dir: Path, model: str | None, prompt: str) -> LaunchSpec: ...

    def read_hook_input(self, stdin_text: str, fallback_cwd: str) -> HookInput: ...

    def main_guide(self, main_name: str, ledger_dir: Path) -> str: ...

    def clear_notice(self) -> str: ...

    def format_hook_output(self, text: str) -> str: ...

    def statusline(self, stdin_text: str) -> str: ...
