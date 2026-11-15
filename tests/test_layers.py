import ast
import inspect
import sys
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
import layers  # noqa: E402
from agents.claude import ClaudeAgent  # noqa: E402
from core import protocols  # noqa: E402
from core.errors import LayerUnknown  # noqa: E402
from hosts.herdr import HerdrHost  # noqa: E402
from tests.fakes.layers import FakeAgent, FakeHost  # noqa: E402

LAYER_ROOTS = ("hosts", "agents", "layers")
ALLOWED_FOR_LAYERS = ("core.errors", "core.protocols")
BANNED_WORDS = ("herdr", "herdr_", "claude")


def core_files():
    return sorted((SKILL / "core").glob("*.py"))


def module_name(path: Path, base_dir: Path = SKILL) -> str:
    rel = path.relative_to(base_dir).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_modules(path: Path, base_dir: Path = SKILL):
    """import した絶対のモジュール名の一覧。相対 import はファイルの位置から直す。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg = module_name(path, base_dir).split(".")
    if path.name != "__init__.py":
        pkg = pkg[:-1]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[:len(pkg) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            out.append(mod)
            out += [f"{mod}.{a.name}" if mod else a.name for a in node.names]
    return out


def root_of(name: str) -> str:
    return name.split(".")[0]


class CoreRulesTest(unittest.TestCase):
    def test_core_is_found(self):
        self.assertGreaterEqual(len(core_files()), 4)

    def test_core_does_not_import_layers(self):
        for f in core_files():
            for m in imported_modules(f):
                self.assertNotIn(root_of(m), LAYER_ROOTS, f"{f.name}: {m}")

    def test_core_has_no_dynamic_import(self):
        for f in core_files():
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                    self.assertNotIn("importlib", [root_of(n) for n in names if n], f.name)
                if isinstance(node, ast.Call):
                    fn = node.func
                    self.assertFalse(isinstance(fn, ast.Name) and fn.id == "__import__", f.name)
                    self.assertFalse(isinstance(fn, ast.Attribute) and fn.attr == "__import__", f.name)

    def test_subprocess_only_in_gitutil_and_check(self):
        for f in core_files():
            if f.name in ("gitutil.py", "check.py"):
                continue
            self.assertNotIn("subprocess", [root_of(m) for m in imported_modules(f)], f.name)

    def test_no_layer_words_in_string_constants(self):
        for f in core_files():
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    low = node.value.lower()
                    for w in BANNED_WORDS:
                        self.assertNotIn(w, low, f"{f.name}: {node.value[:40]!r}")


def conductor_modules():
    names = {p.stem for p in SKILL.glob("*.py")}
    names |= {p.name for p in SKILL.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    return names | {"core", "hosts", "agents", "layers", "tests"}


class LayerImportRulesTest(unittest.TestCase):
    def test_layers_import_only_core_errors_and_protocols(self):
        files = sorted((SKILL / "hosts").glob("*.py")) + sorted((SKILL / "agents").glob("*.py"))
        self.assertTrue(files)
        mods = conductor_modules()
        for f in files:
            if f.name == "__init__.py":
                continue
            for m in imported_modules(f):
                if root_of(m) not in mods:
                    continue
                # from core.errors import X は "core.errors" と "core.errors.X" の両方が出る
                self.assertTrue(
                    any(m == a or m.startswith(a + ".") for a in ALLOWED_FOR_LAYERS), f"{f}: {m}")


class RelativeImportTest(unittest.TestCase):
    def test_from_dotdot_import_is_resolved(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "core").mkdir()
            (base / "core" / "__init__.py").write_text("")
            for src, want in (("from .. import hosts\n", "hosts"),
                              ("from ..hosts import herdr\n", "hosts.herdr"),
                              ("from . import errors\n", "core.errors")):
                f = base / "core" / "x.py"
                f.write_text(src)
                self.assertIn(want, imported_modules(f, base), src)
            f = base / "core" / "x.py"
            f.write_text("from .. import hosts\n")
            self.assertIn(root_of([m for m in imported_modules(f, base) if root_of(m) in LAYER_ROOTS][0]),
                          LAYER_ROOTS)


class SignatureTest(unittest.TestCase):
    def sig(self, fn):
        return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values()]

    def check(self, proto, cls):
        names = [n for n, v in vars(proto).items() if callable(v) and not n.startswith("_")]
        self.assertTrue(names)
        for n in names:
            self.assertTrue(hasattr(cls, n), f"{cls.__name__}.{n}")
            self.assertEqual(self.sig(getattr(proto, n)), self.sig(getattr(cls, n)), f"{cls.__name__}.{n}")
        for attr in proto.__annotations__:
            self.assertTrue(hasattr(cls, attr) or attr in cls().__dict__, f"{cls.__name__}.{attr}")

    def test_host_signatures(self):
        for cls in (HerdrHost, FakeHost):
            self.check(protocols.TerminalHost, cls)

    def test_agent_signatures(self):
        for cls in (ClaudeAgent, FakeAgent):
            self.check(protocols.Agent, cls)


class LayersSelectTest(unittest.TestCase):
    def test_defaults_and_names(self):
        self.assertIsInstance(layers.host_from_env({}), HerdrHost)
        self.assertIsInstance(layers.agent_from_env({}), ClaudeAgent)
        self.assertIsInstance(layers.host_from_env({"CONDUCTOR_HOST": "herdr"}), HerdrHost)
        self.assertIsInstance(layers.agent_from_env({"CONDUCTOR_AGENT": "claude"}), ClaudeAgent)

    def test_unknown(self):
        with self.assertRaises(LayerUnknown):
            layers.host_from_env({"CONDUCTOR_HOST": "tmux"})
        with self.assertRaises(LayerUnknown):
            layers.agent_from_env({"CONDUCTOR_AGENT": "codex"})


if __name__ == "__main__":
    unittest.main()
