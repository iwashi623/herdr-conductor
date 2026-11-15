"""端末とエージェントの層を選ぶ。選ぶのはエントリポイントだけで、このファイルを通す。"""
from agents.claude import ClaudeAgent
from core.errors import LayerUnknown
from hosts.herdr import HerdrHost


def host_from_env(environ):
    name = environ.get("CONDUCTOR_HOST") or "herdr"
    if name == "herdr":
        return HerdrHost()
    raise LayerUnknown(f"未知の端末の層です: CONDUCTOR_HOST={name}")


def agent_from_env(environ):
    name = environ.get("CONDUCTOR_AGENT") or "claude"
    if name == "claude":
        return ClaudeAgent()
    raise LayerUnknown(f"未知のエージェントの層です: CONDUCTOR_AGENT={name}")
