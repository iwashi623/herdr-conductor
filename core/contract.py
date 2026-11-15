"""契約 (既定・差分・ワーカーごとの契約) の形式・合成・保存。標準ライブラリと core のみ。"""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from core.errors import ConductorError, ContractMismatch, ContractMissing
from core.gitutil import tracking_state
from core.ledger import _atomic_write, create_exclusive, file_lock, write_json_atomic

TIMEOUT_TOTAL_MAX = 540
DEFAULT_MAX_RETURNS = 3
DEFAULTS_NAME = "contract-defaults.json"
DEFAULTS_DRAFT_NAME = "contract-defaults.draft.json"
DEFAULTS_SHA_NAME = "contract-defaults.sha256"
DEFAULTS_LOCK_NAME = ".defaults.lock"

CHECK_NAME_RE = re.compile(r"[a-z0-9_-]{1,32}")
SHA_RE = re.compile(r"[0-9a-f]{64}")

DEFAULTS_KEYS = {"checks", "protected_paths", "max_returns", "agreed"}
DEVIATION_KEYS = {"add_checks", "drop_checks", "add_protected", "allow_paths", "max_returns", "agreed"}
CONTRACT_KEYS = {"checks", "protected_paths", "allow_paths", "max_returns", "agreed",
                 "base", "base_ref", "defaults_sha256", "deviation"}


def _dict(data, what):
    if not isinstance(data, dict):
        raise ConductorError(f"{what}は JSON のオブジェクトにしてください")
    return data


def _keys(data, allowed, required, what):
    extra = sorted(set(data) - allowed)
    if extra:
        raise ConductorError(f"{what}に使えないキーがあります: {', '.join(map(str, extra))}")
    missing = sorted(required - set(data))
    if missing:
        raise ConductorError(f"{what}に必須のキーがありません: {', '.join(missing)}")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _str_list(v, key, what, no_abs=False) -> list:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ConductorError(f"{what}の {key} は文字列の配列にしてください")
    if no_abs:
        for x in v:
            if not x:
                raise ConductorError(f"{what}の {key} に空文字は使えません")
            if x.startswith("/"):
                raise ConductorError(f"{what}の {key} に / で始まるパスは使えません")
            if x.endswith("/"):
                raise ConductorError(f"{what}の {key} に / で終わるパターンは使えません (一致しません): {x}")
    return list(v)


def _checks(v, key, what) -> list:
    if not isinstance(v, list):
        raise ConductorError(f"{what}の {key} は配列にしてください")
    seen = set()
    out = []
    for item in v:
        if not isinstance(item, dict) or set(item) != {"name", "cmd", "timeout"}:
            raise ConductorError(f"{what}の {key} の要素は name・cmd・timeout だけを持つオブジェクトにしてください")
        name, cmd, timeout = item["name"], item["cmd"], item["timeout"]
        if not isinstance(name, str) or not CHECK_NAME_RE.fullmatch(name):
            raise ConductorError(f"{what}の {key} の name が不正です: {name!r}")
        if name in seen:
            raise ConductorError(f"{what}の {key} の name が重複しています: {name}")
        seen.add(name)
        if not isinstance(cmd, str) or not cmd.strip():
            raise ConductorError(f"{what}の {key} の {name} の cmd は空でない文字列にしてください")
        if not _is_int(timeout) or not 1 <= timeout <= TIMEOUT_TOTAL_MAX:
            raise ConductorError(f"{what}の {key} の {name} の timeout は 1〜{TIMEOUT_TOTAL_MAX} の整数にしてください")
        out.append({"name": name, "cmd": cmd, "timeout": timeout})
    return out


def _total(checks: list, what: str) -> None:
    total = sum(x["timeout"] for x in checks)
    if total > TIMEOUT_TOTAL_MAX:
        raise ConductorError(f"{what}の timeout の合計が {total} 秒で、上限 {TIMEOUT_TOTAL_MAX} 秒を超えています")


def _max_returns(v, what) -> int:
    if not _is_int(v) or v < 1:
        raise ConductorError(f"{what}の max_returns は 1 以上の整数にしてください")
    return v


def _agreed(v, what) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ConductorError(f"{what}の agreed は空でない文字列にしてください")
    return v


def validate_defaults(data) -> dict:
    what = "既定"
    _dict(data, what)
    _keys(data, DEFAULTS_KEYS, {"checks", "agreed"}, what)
    checks = _checks(data["checks"], "checks", what)
    _total(checks, what)
    return {
        "checks": checks,
        "protected_paths": _str_list(data.get("protected_paths", []), "protected_paths", what, True),
        "max_returns": _max_returns(data.get("max_returns", DEFAULT_MAX_RETURNS), what),
        "agreed": _agreed(data["agreed"], what),
    }


def validate_deviation(data) -> dict:
    what = "差分の下書き"
    _dict(data, what)
    _keys(data, DEVIATION_KEYS, {"agreed"}, what)
    out = {"agreed": _agreed(data["agreed"], what)}
    if "add_checks" in data:
        out["add_checks"] = _checks(data["add_checks"], "add_checks", what)
    if "drop_checks" in data:
        out["drop_checks"] = _str_list(data["drop_checks"], "drop_checks", what)
    for k in ("add_protected", "allow_paths"):
        if k in data:
            out[k] = _str_list(data[k], k, what, True)
    if "max_returns" in data:
        out["max_returns"] = _max_returns(data["max_returns"], what)
    return out


def validate_contract(data) -> dict:
    what = "契約"
    _dict(data, what)
    _keys(data, CONTRACT_KEYS, CONTRACT_KEYS, what)
    checks = _checks(data["checks"], "checks", what)
    _total(checks, what)
    for k in ("base", "base_ref"):
        if not isinstance(data[k], str) or (k == "base" and not data[k]):
            raise ConductorError(f"{what}の {k} が不正です")
    if not isinstance(data["defaults_sha256"], str) or not SHA_RE.fullmatch(data["defaults_sha256"]):
        raise ConductorError(f"{what}の defaults_sha256 は64桁の小文字の16進数にしてください")
    dev = data["deviation"]
    return {
        "checks": checks,
        "protected_paths": _str_list(data["protected_paths"], "protected_paths", what, True),
        "allow_paths": _str_list(data["allow_paths"], "allow_paths", what, True),
        "max_returns": _max_returns(data["max_returns"], what),
        "base": data["base"],
        "base_ref": data["base_ref"],
        "defaults_sha256": data["defaults_sha256"],
        "agreed": _agreed(data["agreed"], what),
        "deviation": None if dev is None else validate_deviation(dev),
    }


def derive_contract(defaults: dict, deviation: dict | None, base: str, base_ref: str,
                    defaults_sha256: str) -> dict:
    defaults = validate_defaults(defaults)
    if deviation is not None:
        deviation = validate_deviation(deviation)
    dev = {} if deviation is None else deviation
    names = [x["name"] for x in defaults["checks"]]
    for n in dev.get("drop_checks", []):
        if n not in names:
            raise ConductorError(f"drop_checks の名前が既定にありません: {n}")
    drop = set(dev.get("drop_checks", []))
    for x in dev.get("add_checks", []):
        if x["name"] in names:
            raise ConductorError(f"add_checks の名前が既定と重なっています: {x['name']}")
    checks = [dict(x) for x in defaults["checks"] if x["name"] not in drop]
    checks += [dict(x) for x in dev.get("add_checks", [])]
    _total(checks, "契約")
    contract = {
        "checks": checks,
        "protected_paths": list(defaults["protected_paths"]) + list(dev.get("add_protected", [])),
        "allow_paths": list(dev.get("allow_paths", [])),
        "max_returns": dev.get("max_returns", defaults["max_returns"]),
        "base": base,
        "base_ref": base_ref,
        "defaults_sha256": defaults_sha256,
        "agreed": defaults["agreed"],
        "deviation": None if deviation is None else dict(deviation),
    }
    return validate_contract(contract)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def _json_text(data: dict) -> str:
    # write_json_atomic と同じ形式
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _read_sha(path: Path) -> str | None:
    """sha256 ファイルを bytes で読む。形式が不正 (文字化け含む) なら None。"""
    try:
        text = Path(path).read_bytes().decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    return text if SHA_RE.fullmatch(text) else None


def load_defaults(root: Path) -> tuple[dict, str]:
    orch = Path(root) / ".orchestra"
    path = orch / DEFAULTS_NAME
    draft = orch / DEFAULTS_DRAFT_NAME
    sha_path = orch / DEFAULTS_SHA_NAME
    with file_lock(orch / DEFAULTS_LOCK_NAME):
        has_def, has_draft = path.exists(), draft.exists()
        if has_def and has_draft:
            raise ConductorError(
                f"既定と下書きの両方がある: {path} と {draft}。下書きを消して既存の既定で起動し直してください")
        if not has_def and not has_draft:
            raise ContractMissing(f"既定も既定の下書きもありません: {path}")
        if has_draft:
            try:
                data = validate_defaults(json.loads(draft.read_text(encoding="utf-8")))
            except (ValueError, OSError) as e:
                raise ConductorError(f"既定の下書きを読めません ({draft}): {e}")
            except ConductorError as e:
                raise ConductorError(f"既定の下書きが不正です ({draft}): {e}")
            text = _json_text(data)
            # 先に sha256、次に既定。途中で止まっても下書きが残るので取り込み直せる
            _atomic_write(sha_path, sha256_bytes(text.encode("utf-8")) + "\n")
            write_json_atomic(path, data)
            draft.unlink()
        state = tracking_state(root, f".orchestra/{DEFAULTS_NAME}")
        if state not in ("untracked", "nogit"):
            raise ConductorError(f"既定ファイルが git で追跡されている (または状態を確かめられない) ため使いません: {path}")
        if not sha_path.exists():
            raise ContractMismatch(f"既定の sha256 ファイルがありません: {sha_path}")
        recorded = _read_sha(sha_path)
        if recorded is None:
            raise ContractMismatch(f"既定の sha256 ファイルの形式が不正です: {sha_path}")
        actual = sha256_file(path)
        if actual != recorded:
            raise ContractMismatch(f"既定ファイルの sha256 が記録と違います: {path}")
        try:
            return validate_defaults(json.loads(path.read_text(encoding="utf-8"))), actual
        except (ValueError, ConductorError) as e:
            raise ConductorError(f"既定ファイルが不正です ({path}): {e}")


def _contract_paths(ldir: Path, name: str) -> tuple[Path, Path]:
    tasks = Path(ldir) / "tasks"
    return tasks / f"{name}.contract.json", tasks / f"{name}.contract.sha256"


def write_contract(ldir: Path, name: str, contract: dict) -> Path:
    """台帳のロック (.tasks.lock) を自分で取る。ledger._Lock を持ったまま呼ばないこと
    (同じプロセスで新しい fd の flock を取ろうとして固まる)。"""
    jpath, spath = _contract_paths(ldir, name)
    text = _json_text(contract)
    with file_lock(Path(ldir) / ".tasks.lock"):
        jpath.parent.mkdir(parents=True, exist_ok=True)
        for p in (jpath, spath):
            if p.exists():
                raise ConductorError(f"すでにあります: {p}")
        create_exclusive(jpath, text)
        try:
            create_exclusive(spath, sha256_bytes(text.encode("utf-8")) + "\n")
        except BaseException:
            jpath.unlink(missing_ok=True)
            raise
    return jpath


def remove_contract(ldir: Path, name: str) -> None:
    for p in _contract_paths(ldir, name):
        p.unlink(missing_ok=True)


def load_contract(ldir: Path, name: str) -> dict:
    jpath, spath = _contract_paths(ldir, name)
    if not spath.exists():
        raise ContractMissing(f"契約がありません (sha256 ファイルがない): {spath}")
    recorded = _read_sha(spath)
    if not jpath.exists():
        raise ContractMismatch(f"契約ファイルがありません: {jpath}")
    raw = jpath.read_bytes()
    if recorded is None or sha256_bytes(raw) != recorded:
        raise ContractMismatch(f"契約ファイルの sha256 が記録と違います: {jpath}")
    try:
        return validate_contract(json.loads(raw.decode("utf-8")))
    except (ValueError, ConductorError) as e:
        raise ContractMismatch(f"契約ファイルが不正です ({jpath}): {e}")


def match_protected(paths: list[str], protected: list[str], allow: list[str]) -> list[str]:
    def hit(p: str, pats: list[str]) -> bool:
        pp = PurePosixPath(p)
        return any(pp.full_match(pat) for pat in pats)

    return [p for p in paths if hit(p, protected) and not hit(p, allow)]
