"""Takılma uyarısı: aynı araç çağrısı art arda tekrarlanınca modeli uyarır, sonra durdurur.

Model bazen aynı dosyayı aynı satırlarla tekrar tekrar okur ya da aynı komutu
yeniden çalıştırır; sonuç değişmediği için ilerleyemez. Bu katman ardışık özdeş
çağrıları (aynı ad + aynı argümanlar) sayar: `warn_after`. tekrarda çağrı çalışır
ama sonucuna bir hatırlatma eklenir; `block_after`. tekrarda çağrı hiç çalışmaz,
modele hata döner. Farklı bir çağrı sayacı sıfırlar. Model çağrısı yapmaz.
"""

import json
from dataclasses import dataclass
from typing import Literal

from harness.messages import ToolCall

GuardAction = Literal["warn", "block"]


@dataclass(frozen=True)
class GuardVerdict:
    action: GuardAction
    repeats: int

    def message(self, tool: str) -> str:
        if self.action == "block":
            return (
                f"[harness] `{tool}` aynı argümanlarla art arda {self.repeats}. kez çağrıldı; "
                "çağrı çalıştırılmadı. Sonuç değişmeyecek: başka bir yol dene ya da işi bitir."
            )
        return (
            f"[harness] `{tool}` aynı argümanlarla art arda {self.repeats}. kez çağrıldı. "
            "Önceki sonuca bak; yöntemini değiştir ya da işi bitir."
        )


class RepeatGuard:
    """Ardışık özdeş araç çağrılarını sayar; her görev başında yenisi kurulur."""

    def __init__(self, warn_after: int = 3, block_after: int = 5) -> None:
        self._warn_after = warn_after
        self._block_after = block_after
        self._last: tuple[str, str] | None = None
        self._repeats = 0

    def check(self, call: ToolCall) -> GuardVerdict | None:
        signature = (call.name, _normalized(call.arguments))
        self._repeats = self._repeats + 1 if signature == self._last else 1
        self._last = signature
        if self._repeats >= self._block_after:
            return GuardVerdict("block", self._repeats)
        if self._repeats >= self._warn_after:
            return GuardVerdict("warn", self._repeats)
        return None


def _normalized(arguments: str) -> str:
    """`{"a":1, "b":2}` ile `{"b": 2, "a": 1}` aynı çağrıdır."""
    try:
        return json.dumps(json.loads(arguments), sort_keys=True, ensure_ascii=False)
    except ValueError:
        return arguments
