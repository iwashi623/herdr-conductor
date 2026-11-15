import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL))
import core.contract as c  # noqa: E402
import core.ledger as ledger  # noqa: E402
from core.errors import (ConductorError, ContractMismatch, ContractMissing, LockBusy)  # noqa: E402

GOOD = {
    "checks": [{"name": "test", "cmd": "npm test", "timeout": 300},
               {"name": "lint", "cmd": "npm run lint", "timeout": 120}],
    "protected_paths": ["infra/**", "**/*.lock"],
    "max_returns": 3,
    "agreed": "2026-11-15T10:00:00+09:00 人の返答: はい",
}
SHA = "a" * 64


def good():
    return copy.deepcopy(GOOD)


class DefaultsFormatTest(unittest.TestCase):
    def bad(self, data):
        with self.assertRaises(ConductorError):
            c.validate_defaults(data)

    def test_good_and_fill(self):
        out = c.validate_defaults(good())
        self.assertEqual(out["max_returns"], 3)
        out = c.validate_defaults({"checks": [], "agreed": "x"})
        self.assertEqual(out["protected_paths"], [])
        self.assertEqual(out["max_returns"], c.DEFAULT_MAX_RETURNS)
        self.assertEqual(out["checks"], [])

    def test_constants(self):
        self.assertEqual(c.TIMEOUT_TOTAL_MAX, 540)
        self.assertEqual(c.DEFAULTS_NAME, "contract-defaults.json")

    def test_unknown_keys(self):
        for k in ("keep", "objective", "acceptance", "allow_paths", "add_checks"):
            d = good()
            d[k] = []
            self.bad(d)

    def test_timeout(self):
        for t in (0, 541, 1.5, "10", True, -1):
            d = good()
            d["checks"] = [{"name": "a", "cmd": "x", "timeout": t}]
            self.bad(d)
        d = good()
        d["checks"] = [{"name": "a", "cmd": "x", "timeout": 540}]
        c.validate_defaults(d)

    def test_total_timeout(self):
        d = good()
        d["checks"] = [{"name": "a", "cmd": "x", "timeout": 300}, {"name": "b", "cmd": "x", "timeout": 241}]
        self.bad(d)
        d["checks"][1]["timeout"] = 240
        c.validate_defaults(d)

    def test_names(self):
        for n in ("A", "a b", "", "a" * 33, "あ", "a\n"):
            d = good()
            d["checks"] = [{"name": n, "cmd": "x", "timeout": 1}]
            self.bad(d)
        d = good()
        d["checks"] = [{"name": "a", "cmd": "x", "timeout": 1}] * 2
        self.bad(d)

    def test_cmd_and_extra_check_keys(self):
        for chk in ({"name": "a", "cmd": "", "timeout": 1}, {"name": "a", "timeout": 1},
                    {"name": "a", "cmd": "x", "timeout": 1, "z": 1}):
            d = good()
            d["checks"] = [chk]
            self.bad(d)

    def test_max_returns(self):
        for v in (0, -1, 1.5, "3", True):
            d = good()
            d["max_returns"] = v
            self.bad(d)

    def test_agreed_and_paths(self):
        for v in ("", None, 1):
            d = good()
            d["agreed"] = v
            self.bad(d)
        d = good()
        del d["agreed"]
        self.bad(d)
        d = good()
        d["protected_paths"] = ["/abs"]
        self.bad(d)
        d["protected_paths"] = "infra/**"
        self.bad(d)
        self.bad([])
        d = good()
        del d["checks"]
        self.bad(d)


class DeviationTest(unittest.TestCase):
    def test_agreed_required(self):
        with self.assertRaises(ConductorError):
            c.validate_deviation({})
        with self.assertRaises(ConductorError):
            c.validate_deviation({"agreed": ""})
        c.validate_deviation({"agreed": "はい"})

    def test_keys(self):
        for k in ("checks", "protected_paths", "base", "keep"):
            with self.assertRaises(ConductorError):
                c.validate_deviation({"agreed": "x", k: []})
        d = {"agreed": "x", "add_checks": [{"name": "e", "cmd": "y", "timeout": 5}],
             "drop_checks": ["a"], "add_protected": ["s/**"], "allow_paths": ["i/**"], "max_returns": 4}
        self.assertEqual(c.validate_deviation(d), d)
        with self.assertRaises(ConductorError):
            c.validate_deviation({"agreed": "x", "drop_checks": "a"})
        with self.assertRaises(ConductorError):
            c.validate_deviation({"agreed": "x", "max_returns": 0})


class StrictInputTest(unittest.TestCase):
    def test_empty_and_trailing_slash(self):
        for pat in ("", "infra/", "a/b/"):
            for fn, d in ((c.validate_defaults, {**good(), "protected_paths": [pat]}),
                          (c.validate_deviation, {"agreed": "x", "add_protected": [pat]}),
                          (c.validate_deviation, {"agreed": "x", "allow_paths": [pat]})):
                with self.assertRaises(ConductorError):
                    fn(d)

    def test_derive_validates_inputs(self):
        defaults = c.validate_defaults(good())
        with self.assertRaises(ConductorError):
            c.derive_contract(defaults, {"agreed": "x", "add_checks": [{"cmd": "y", "timeout": 1}]}, "b", "", SHA)
        with self.assertRaises(ConductorError):
            c.derive_contract({"agreed": "x"}, None, "b", "", SHA)

    def test_sha_file_binary(self):
        with tempfile.TemporaryDirectory() as d:
            ldir = Path(d)
            k = c.derive_contract(c.validate_defaults(good()), None, "abc", "", SHA)
            c.write_contract(ldir, "w", k)
            (ldir / "tasks" / "w.contract.sha256").write_bytes(b"\xff\xfe\x00")
            with self.assertRaises(ContractMismatch):
                c.load_contract(ldir, "w")


class DeriveTest(unittest.TestCase):
    def setUp(self):
        self.defaults = c.validate_defaults(good())

    def derive(self, dev):
        return c.derive_contract(self.defaults, dev, "abc123", "main", SHA)

    def test_no_deviation(self):
        k = self.derive(None)
        self.assertEqual(k["checks"], self.defaults["checks"])
        self.assertEqual(k["protected_paths"], self.defaults["protected_paths"])
        self.assertEqual(k["allow_paths"], [])
        self.assertEqual(k["max_returns"], 3)
        self.assertEqual((k["base"], k["base_ref"], k["defaults_sha256"]), ("abc123", "main", SHA))
        self.assertEqual(k["agreed"], self.defaults["agreed"])
        self.assertIsNone(k["deviation"])
        c.validate_contract(k)

    def test_compose(self):
        dev = {"agreed": "ok", "add_checks": [{"name": "e2e", "cmd": "npm run e2e", "timeout": 120}],
               "drop_checks": ["lint"], "add_protected": ["src/legacy/**"],
               "allow_paths": ["infra/cache/**"], "max_returns": 4}
        k = self.derive(dev)
        self.assertEqual([x["name"] for x in k["checks"]], ["test", "e2e"])
        self.assertEqual(k["protected_paths"], ["infra/**", "**/*.lock", "src/legacy/**"])
        self.assertEqual(k["allow_paths"], ["infra/cache/**"])
        self.assertEqual(k["max_returns"], 4)
        self.assertEqual(k["deviation"], dev)
        c.validate_contract(k)

    def test_errors(self):
        with self.assertRaises(ConductorError):
            self.derive({"agreed": "x", "drop_checks": ["nope"]})
        with self.assertRaises(ConductorError):
            self.derive({"agreed": "x", "add_checks": [{"name": "test", "cmd": "y", "timeout": 1}]})
        with self.assertRaises(ConductorError):
            self.derive({"agreed": "x", "add_checks": [{"name": "e", "cmd": "y", "timeout": 121}]})
        self.derive({"agreed": "x", "add_checks": [{"name": "e", "cmd": "y", "timeout": 120}]})
        # drop で枠が空けば足せる
        self.derive({"agreed": "x", "drop_checks": ["test"],
                     "add_checks": [{"name": "e", "cmd": "y", "timeout": 400}]})

    def test_contract_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            ldir = Path(d) / "m"
            k = self.derive({"agreed": "x"})
            c.write_contract(ldir, "w1", k)
            self.assertEqual(c.load_contract(ldir, "w1"), k)


class ContractValidateTest(unittest.TestCase):
    def valid(self):
        return c.derive_contract(c.validate_defaults(good()), None, "abc", "", SHA)

    def test_ok_empty_base_ref(self):
        c.validate_contract(self.valid())

    def test_bad(self):
        for key in ("checks", "protected_paths", "allow_paths", "max_returns", "agreed",
                    "base", "base_ref", "defaults_sha256", "deviation"):
            k = self.valid()
            del k[key]
            with self.assertRaises(ConductorError, msg=key):
                c.validate_contract(k)
        k = self.valid()
        k["extra"] = 1
        with self.assertRaises(ConductorError):
            c.validate_contract(k)
        k = self.valid()
        k["defaults_sha256"] = "zz"
        with self.assertRaises(ConductorError):
            c.validate_contract(k)


class LoadDefaultsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.orch = self.root / ".orchestra"
        self.orch.mkdir()
        self.draft = self.orch / "contract-defaults.draft.json"
        self.path = self.orch / c.DEFAULTS_NAME
        self.sha = self.orch / "contract-defaults.sha256"

    def tearDown(self):
        self._tmp.cleanup()

    def test_import_draft(self):
        self.draft.write_text(json.dumps(GOOD))
        d, sha = c.load_defaults(self.root)
        self.assertFalse(self.draft.exists())
        self.assertEqual(d, c.validate_defaults(good()))
        self.assertEqual(sha, c.sha256_file(self.path))
        self.assertEqual(self.sha.read_text(), sha + "\n")
        self.assertEqual(c.load_defaults(self.root), (d, sha))

    def test_bad_draft_kept(self):
        self.draft.write_text(json.dumps({**GOOD, "keep": []}))
        with self.assertRaises(ConductorError):
            c.load_defaults(self.root)
        self.assertTrue(self.draft.exists())
        self.assertFalse(self.path.exists())
        self.draft.write_text("{not json")
        with self.assertRaises(ConductorError):
            c.load_defaults(self.root)
        self.assertTrue(self.draft.exists())

    def test_both(self):
        self.draft.write_text(json.dumps(GOOD))
        c.load_defaults(self.root)
        self.draft.write_text(json.dumps(GOOD))
        with self.assertRaises(ConductorError) as cm:
            c.load_defaults(self.root)
        self.assertIn("既定と下書きの両方がある", str(cm.exception))
        self.assertTrue(self.draft.exists())

    def test_missing(self):
        with self.assertRaises(ContractMissing):
            c.load_defaults(self.root)

    def test_tampered(self):
        self.draft.write_text(json.dumps(GOOD))
        c.load_defaults(self.root)
        self.path.write_text(self.path.read_text() + " ")
        with self.assertRaises(ContractMismatch):
            c.load_defaults(self.root)

    def test_sha_file_problems(self):
        self.draft.write_text(json.dumps(GOOD))
        c.load_defaults(self.root)
        self.sha.write_text("xyz\n")
        with self.assertRaises(ContractMismatch):
            c.load_defaults(self.root)
        self.sha.unlink()
        with self.assertRaises(ContractMismatch):
            c.load_defaults(self.root)

    def test_tracked(self):
        subprocess.run(["git", "-C", str(self.root), "init", "-q"], check=True)
        self.draft.write_text(json.dumps(GOOD))
        c.load_defaults(self.root)
        subprocess.run(["git", "-C", str(self.root), "add", "-f", ".orchestra/contract-defaults.json"], check=True)
        with self.assertRaises(ConductorError) as cm:
            c.load_defaults(self.root)
        self.assertNotIsInstance(cm.exception, (ContractMismatch, ContractMissing))


class ContractFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ldir = Path(self._tmp.name) / "m"
        self.k = c.derive_contract(c.validate_defaults(good()), None, "abc", "main", SHA)

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_read(self):
        p = c.write_contract(self.ldir, "w", self.k)
        self.assertEqual(p, self.ldir / "tasks" / "w.contract.json")
        text = p.read_text(encoding="utf-8")
        self.assertEqual(text, json.dumps(self.k, ensure_ascii=False, indent=2) + "\n")
        self.assertEqual((self.ldir / "tasks" / "w.contract.sha256").read_text(),
                         c.sha256_bytes(text.encode("utf-8")) + "\n")
        self.assertEqual(c.load_contract(self.ldir, "w"), self.k)

    def test_twice(self):
        c.write_contract(self.ldir, "w", self.k)
        with self.assertRaises(ConductorError):
            c.write_contract(self.ldir, "w", self.k)

    def test_partial_existing(self):
        (self.ldir / "tasks").mkdir(parents=True)
        (self.ldir / "tasks" / "w.contract.sha256").write_text("x")
        with self.assertRaises(ConductorError):
            c.write_contract(self.ldir, "w", self.k)
        self.assertFalse((self.ldir / "tasks" / "w.contract.json").exists())

    def test_missing_and_tamper(self):
        p = c.write_contract(self.ldir, "w", self.k)
        sha = self.ldir / "tasks" / "w.contract.sha256"
        p.write_text(p.read_text() + " ")
        with self.assertRaises(ContractMismatch):
            c.load_contract(self.ldir, "w")
        p.unlink()
        with self.assertRaises(ContractMismatch):
            c.load_contract(self.ldir, "w")
        sha.unlink()
        with self.assertRaises(ContractMissing):
            c.load_contract(self.ldir, "w")

    def test_invalid_contract_content(self):
        p = self.ldir / "tasks"
        p.mkdir(parents=True)
        text = json.dumps({"checks": []}) + "\n"
        (p / "w.contract.json").write_text(text)
        (p / "w.contract.sha256").write_text(c.sha256_bytes(text.encode()) + "\n")
        with self.assertRaises(ContractMismatch):
            c.load_contract(self.ldir, "w")

    def test_remove(self):
        c.write_contract(self.ldir, "w", self.k)
        c.remove_contract(self.ldir, "w")
        self.assertEqual(list((self.ldir / "tasks").iterdir()), [])
        c.remove_contract(self.ldir, "w")  # 2回目も落ちない


class GlobTest(unittest.TestCase):
    def test_table(self):
        rows = [("infra/**", "infra/x/y", True), ("infra/**", "infra", False),
                ("*.lock", "a/b.lock", False), ("**/*.lock", "x.lock", True),
                ("**/*.lock", "a/b.lock", True), ("a/**/b", "a/b", True),
                ("infra/**", "Infra/x", False)]
        for pat, path, want in rows:
            self.assertEqual(c.match_protected([path], [pat], []), [path] if want else [], (pat, path))

    def test_allow(self):
        got = c.match_protected(["infra/cache/a", "infra/b", "ok.txt"], ["infra/**"], ["infra/cache/**"])
        self.assertEqual(got, ["infra/b"])


class ErrorCodesTest(unittest.TestCase):
    def test_codes(self):
        self.assertEqual((ContractMissing.exit_code, ContractMismatch.exit_code, LockBusy.exit_code), (4, 5, 6))
        self.assertTrue(issubclass(LockBusy, ConductorError))


if __name__ == "__main__":
    unittest.main()
