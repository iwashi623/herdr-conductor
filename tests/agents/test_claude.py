import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
from agents.claude import ClaudeAgent  # noqa: E402


class ClaudeAgentTest(unittest.TestCase):
    def test_hook_output_limit(self):
        self.assertEqual(ClaudeAgent.hook_output_limit, 10000)

    def test_launch(self):
        spec = ClaudeAgent().launch("w", Path("/led"), "sonnet", "PROMPT")
        self.assertEqual(spec.kind_hint, "claude")
        c = spec.command
        self.assertEqual(c[0], "claude")
        self.assertEqual(c[c.index("--name") + 1], "w")
        self.assertEqual(c[c.index("--permission-mode") + 1], "auto")
        self.assertEqual(c[c.index("--append-system-prompt-file") + 1], str(SKILL / "worker-rules.md"))
        self.assertEqual(c[c.index("--model") + 1], "sonnet")
        self.assertEqual(c[-1], "PROMPT")

    def test_launch_without_model(self):
        c = ClaudeAgent().launch("w", Path("/led"), None, "P").command
        self.assertNotIn("--model", c)
        self.assertEqual(c[-1], "P")

    def test_read_hook_input(self):
        a = ClaudeAgent()
        h = a.read_hook_input(json.dumps({"cwd": "/c", "source": "compact"}), "/fb")
        self.assertEqual((h.cwd, h.source), ("/c", "compact"))
        self.assertEqual(a.read_hook_input(json.dumps({"cwd": "/c", "source": "clear"}), "/fb").source, "clear")
        self.assertEqual(a.read_hook_input(json.dumps({"cwd": "/c", "source": "startup"}), "/fb").source, "other")
        self.assertEqual(a.read_hook_input(json.dumps({"cwd": "/c"}), "/fb").source, "other")
        self.assertEqual(a.read_hook_input(json.dumps({"cwd": "/c", "source": 3}), "/fb").source, "other")
        h = a.read_hook_input("not json", "/fb")
        self.assertEqual((h.cwd, h.source), ("/fb", "other"))
        self.assertEqual(a.read_hook_input("[1]", "/fb").cwd, "/fb")

    def test_main_guide_and_clear_notice(self):
        a = ClaudeAgent()
        g = a.main_guide("boss", Path("/led"))
        self.assertTrue(g.endswith("メイン名: boss｡台帳ディレクトリ: /led｡"))
        self.assertNotIn("記録であり", g)
        self.assertEqual(a.clear_notice(),
                         "`/clear` の後です｡指揮を続けるなら人に `/conductor` を打ってもらってください｡\n")

    def test_format_hook_output_identity(self):
        self.assertEqual(ClaudeAgent().format_hook_output("x\ny\n"), "x\ny\n")

    def test_add_dir_in_argv(self):
        ldir = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(ldir.rmdir)
        prompt = "指示書 /t/task.md を読んで作業を始めてください。報告先は boss です。"
        c = ClaudeAgent().launch("auth-fix", ldir, None, prompt).command
        i = c.index("--add-dir")
        self.assertEqual(c[i + 1], str(ldir))
        self.assertTrue(c[i + 2].startswith("--"))
        self.assertIn("/t/task.md", c[-1])

    def _repo_with_settings(self, content):
        import shutil
        root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, root, True)
        (root / ".orchestra" / "m").mkdir(parents=True)
        if content is not None:
            (root / ".claude").mkdir()
            text = content if isinstance(content, str) else json.dumps(content)
            (root / ".claude" / "settings.local.json").write_text(text)
        return root / ".orchestra" / "m"

    def _settings_arg(self, c):
        return json.loads(c[c.index("--settings") + 1]) if "--settings" in c else None

    def test_settings_passes_only_mcp_approval_keys(self):
        led = self._repo_with_settings({
            "enableAllProjectMcpServers": True,
            "enabledMcpjsonServers": ["playwright"],
            "disabledMcpjsonServers": ["x"],
            "permissions": {"allow": ["Bash(ls)"]}, "env": {"A": "b"}})
        c = ClaudeAgent().launch("w", led, "sonnet", "P").command
        self.assertEqual(self._settings_arg(c), {
            "enableAllProjectMcpServers": True,
            "enabledMcpjsonServers": ["playwright"],
            "disabledMcpjsonServers": ["x"]})

    def test_settings_partial_keys(self):
        led = self._repo_with_settings({"enabledMcpjsonServers": ["a"], "permissions": {}})
        c = ClaudeAgent().launch("w", led, None, "P").command
        self.assertEqual(self._settings_arg(c), {"enabledMcpjsonServers": ["a"]})

    def test_settings_absent_cases(self):
        for content in (None, "{broken", [1, 2], {"permissions": {}},
                        {"enableAllProjectMcpServers": "true"},
                        {"enabledMcpjsonServers": "playwright"},
                        {"enabledMcpjsonServers": [1, 2]}):
            with self.subTest(content=content):
                led = self._repo_with_settings(content)
                c = ClaudeAgent().launch("w", led, None, "P").command
                self.assertNotIn("--settings", c)
                self.assertEqual(c[-1], "P")

    def test_settings_not_swallowed_by_add_dir(self):
        led = self._repo_with_settings({"enableAllProjectMcpServers": True})
        c = ClaudeAgent().launch("w", led, "sonnet", "P").command
        i = c.index("--add-dir")
        self.assertEqual(c[i + 1], str(led))
        self.assertTrue(c[i + 2].startswith("--"))
        self.assertIn("--settings", c)
        self.assertLess(c.index("--settings"), i)
        self.assertEqual(c[-1], "P")

    def test_invalid_stdin_falls_back_to_process_cwd(self):
        a = ClaudeAgent()
        for text in ("not json", ""):
            h = a.read_hook_input(text, "/fb")
            self.assertEqual((h.cwd, h.source), ("/fb", "other"))


if __name__ == "__main__":
    unittest.main()
