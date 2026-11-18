"""Bitirmeden önce kontrol (`finish_check` katmanı): model çağrısı yapmadan, yalnız olaylardan.

Model araç istemeyi bırakınca ("bitti") görev boyunca görülenlere bakılır: son dosya
düzenlemesinden sonra komut çalıştı mı, planda bitmemiş adım kaldı mı, test koruyucusunun
uyarısına özette cevap verildi mi. Eksik varsa modele bir kez hatırlatılır, döngü bir adım
daha sürer; ikinci "bitti" kabul edilir. Claude Code'daki Stop kancasının karşılığıdır.

Hatırlatma geçmişe kullanıcı mesajı olarak girer. Kayıtta müdahale olarak durur; oturuma
devam edilince `note()` aynı metni özetten yeniden kurar (core/session_log.py).
"""

from collections.abc import Mapping
from typing import cast

from harness.messages import ToolCall, ToolResult
from harness.tools.ask_user import ASSUMPTION_LINE

_EDIT_TOOLS = frozenset({"edit_file", "write_file", "delete_file"})
_SEPARATOR = " · "
CONTRACT_LINE = "Değişen sözleşme"  # test koruyucusunun istediği özet satırı (core/guards.py)


def note(summary: str) -> str:
    """Modele giden hatırlatma; kayıttaki müdahale özetinden de aynısı kurulur."""
    reasons = "\n".join(f"- {reason}" for reason in summary.split(_SEPARATOR))
    return (
        "[harness] Bitirmeden önce kontrol:\n"
        f"{reasons}\n"
        "Gerekiyorsa şimdi yap; gerekmiyorsa nedenini tek cümleyle yazıp bitir."
    )


class FinishCheck:
    """Bir görev boyunca araç sonuçlarını izler; ilk "bitti"de eksikleri bir kez söyler."""

    def __init__(self, *, can_run_commands: bool) -> None:
        self._can_run = can_run_commands
        self._unverified = False  # son başarılı düzenlemeden sonra komut çalışmadı
        self._open_steps: list[str] = []
        self._guard_warned = False
        self._unanswered = False  # ask_user cevapsız kaldı: varsayım özette yazılmalı
        self._used = False

    def saw(self, call: ToolCall, result: ToolResult, details: Mapping[str, object]) -> None:
        if result.is_error:
            return
        if call.name in _EDIT_TOOLS:
            self._unverified = True
        elif call.name == "bash":
            self._unverified = False  # çalıştırdığı şeyin test olup olmadığına karışılmaz
        elif call.name == "update_plan":
            self._open_steps = _open_steps(details.get("steps"))
        elif call.name == "ask_user" and details.get("answered") is False:
            self._unanswered = True

    def guard_warned(self) -> None:
        self._guard_warned = True

    def check(self, final_text: str) -> str | None:
        """Eksiklerin özeti (kayda girer, `note()` ile modele gider); eksik yoksa None."""
        if self._used:
            return None
        reasons: list[str] = []
        if self._can_run and self._unverified:
            reasons.append(
                "son dosya düzenlemesinden sonra hiç komut çalıştırmadın "
                "(test, derleme ya da en azından çalıştırma)"
            )
        if self._open_steps:
            shown = ", ".join(f'"{s}"' for s in self._open_steps[:3])
            more = f" ve {len(self._open_steps) - 3} adım daha" if len(self._open_steps) > 3 else ""
            reasons.append(f"planda bitmemiş adım var: {shown}{more}")
        if self._guard_warned and CONTRACT_LINE not in final_text:
            reasons.append(
                f"test koruyucusu eski bir doğrulamanın değiştiğini söyledi; özetinde "
                f'"{CONTRACT_LINE}: …" satırı yok'
            )
        if self._unanswered and ASSUMPTION_LINE not in final_text:
            reasons.append(f'sorun cevapsız kaldı; özetinde "{ASSUMPTION_LINE}: …" satırı yok')
        if not reasons:
            return None
        self._used = True
        return _SEPARATOR.join(reasons)


def _open_steps(steps: object) -> list[str]:
    """update_plan ayrıntısından tamamlanmamış adımların adları."""
    if not isinstance(steps, list):
        return []
    found: list[str] = []
    for item in cast(list[object], steps):
        if isinstance(item, dict):
            entry = cast(dict[str, object], item)
            if entry.get("status") != "completed":
                found.append(str(entry.get("step", "")))
    return found
