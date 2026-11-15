"""conductor の例外。終了コードは exit_code で決める。"""


class ConductorError(Exception):
    """スクリプトが理由付きで失敗するときに使う。"""

    exit_code = 1


class UsageError(ConductorError):
    exit_code = 2


class LayerUnknown(ConductorError):
    exit_code = 2


class PaneClash(ConductorError):
    exit_code = 3


class HostError(ConductorError):
    exit_code = 1


class ContractMissing(ConductorError):
    exit_code = 4


class ContractMismatch(ConductorError):
    exit_code = 5


class LockBusy(ConductorError):
    exit_code = 6
