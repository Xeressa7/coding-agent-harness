"""İzin katmanı: PermissionManager (rapor Bölüm 16).

Her araç çağrısı çalışmadan önce buradan geçer ve üç karardan birini alır:
izin ver, kullanıcıya sor, reddet. Çalışma dizini dışına çıkan yollar
reddedilir. "Sor" kararında soruyu bu modül değil ön yüz sorar; ön yüz
`grant()` çağırırsa o araç bu PermissionManager yaşadığı sürece (bir
oturum) bir daha sorulmaz. Reddetme her zaman kazanır: grant edilmiş bir
araç yine de çalışma dizini dışına çıkarsa DENY sürer.
"""

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from harness.messages import ToolCall
from harness.tools.base import Tool, ToolRegistry


class Decision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# İçeriği/durumu değiştirmeyen araçlar: sormadan izin verilir.
_READ_ONLY = frozenset({"read_file", "search"})


class PermissionManager:
    """Her araç çağrısı için ALLOW/ASK/DENY kararı verir; "sor" kararında kendisi sormaz."""

    def __init__(self, root: Path, tools: ToolRegistry) -> None:
        self._root = root.resolve()
        self._tools = tools
        self._always_allowed: set[str] = set()

    def grant(self, name: str) -> None:
        """Bu araç için kalan oturum boyunca bir daha sorma; DENY yine de kazanır."""
        self._always_allowed.add(name)

    def decide(self, call: ToolCall) -> Decision:
        if call.name in _READ_ONLY:
            return Decision.ALLOW
        tool = self._tools.get(call.name)
        if tool is None:
            # Tanınmayan araç: soracak bir şey yok, ToolRegistry kendi hatasını üretsin.
            return Decision.ALLOW
        path = self._requested_path(call, tool)
        if path is not None and not path.is_relative_to(self._root):
            return Decision.DENY
        if call.name in self._always_allowed:
            return Decision.ALLOW
        return Decision.ASK

    def _requested_path(self, call: ToolCall, tool: Tool[Any]) -> Path | None:
        """Çağrının hedef aldığı yol; argümanlar bozuksa ya da aracın yol argümanı
        yoksa (örn. bash) None döner."""
        try:
            params = tool.params_model.model_validate_json(call.arguments)
        except ValidationError:
            return None
        raw = getattr(params, "path", None)
        if not isinstance(raw, str):
            return None
        return (self._root / raw).resolve()
