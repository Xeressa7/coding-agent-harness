"""Bağlam budama (anatomi katmanı `context_prune`): eski araç sonuçlarını toplu çıkarır.

Her model çağrısı bütün geçmişi yeniden gönderir; bir kez okunan dosya sonraki her
çağrıda bağlamda kalır. 2. turda (2026-11-17) uzun koşuların maliyeti tekrar okumadan
değil, bu taşımadan geliyordu. Budama eski araç sonuçlarının metnini kısa bir notla
değiştirir; araç çağrısının kendisi (ad + argüman) geçmişte kalır, model gerekirse
aracı yeniden çağırır. Tam çıktı oturum kaydında (`ToolFinished`) durur.

Önbellek yüzünden ne zaman budandığı, ne kadar budandığından önemlidir: geçmişin bir
yerini değiştirmek sağlayıcının önbelleğini o noktadan sonrası için kırar. Her adımda
biraz budamak her adımda önbelleği kırar ve maliyeti artırır (benzetimde +%31); bu
yüzden budama seyrek ve toplu yapılır:

- bağlam `threshold` token'ı aşınca, son `keep_steps` araç adımı dışındaki bütün
  sonuçlar bir kerede notla değiştirilir;
- histerezis: budamadan sonra bağlam en az `regrow` token büyümeden yeniden
  tetiklenmez (yoksa son adımlar büyükken her adımda yeniden tetiklenir).

Benzetim: `harness-demos/round2/analysis/baglam_budama.py` (eşik 60k, son 5 adım).
"""

from dataclasses import dataclass

from harness.core.context import grouped
from harness.messages import AssistantMessage, Message, ToolResult

PRUNE_THRESHOLD_TOKENS = 60_000
PRUNE_KEEP_STEPS = 5
PRUNE_REGROW_TOKENS = 20_000
CHARS_PER_TOKEN = 3.5  # sağlayıcı sayım bildirmezse kaba tahmin
STUB_PREFIX = "[harness: bu eski araç sonucu bağlamdan çıkarıldı"


def stub(chars: int) -> str:
    return f"{STUB_PREFIX} ({grouped(chars)} karakter). Gerekirse aracı yeniden çağır.]"


def estimate_tokens(history: list[Message]) -> int:
    chars = 0
    for message in history:
        if isinstance(message, ToolResult):
            chars += len(message.content)
        elif isinstance(message, AssistantMessage):
            chars += len(message.text) + sum(len(c.arguments) for c in message.tool_calls)
        else:
            chars += len(message.text)
    return round(chars / CHARS_PER_TOKEN)


def prune(history: list[Message], keep_steps: int) -> tuple[list[Message], int, int]:
    """Son `keep_steps` araç adımından önceki sonuçlar not olur: (yeni geçmiş, sonuç, karakter)."""
    steps = [i for i, m in enumerate(history) if isinstance(m, AssistantMessage) and m.tool_calls]
    if len(steps) <= keep_steps:
        return history, 0, 0
    boundary = steps[-keep_steps]
    pruned = list(history)
    count = removed = 0
    for i in range(boundary):
        message = pruned[i]
        if not isinstance(message, ToolResult) or message.content.startswith(STUB_PREFIX):
            continue
        note = stub(len(message.content))
        if len(note) >= len(message.content):
            continue  # kısa sonuç: not daha uzun olurdu
        pruned[i] = message.model_copy(update={"content": note})
        count += 1
        removed += len(message.content) - len(note)
    return pruned, count, removed


@dataclass(frozen=True)
class PruneReport:
    results: int
    chars: int
    tokens_before: int
    tokens_after: int  # tahmin: önceki sayım − atılan karakter / CHARS_PER_TOKEN

    def summary(self) -> str:
        return (
            f"{self.results} eski araç sonucu çıkarıldı · ~{self.tokens_before // 1000}k → "
            f"~{self.tokens_after // 1000}k token"
        )


class ContextPruner:
    """Bir görev boyunca eşik ve histerezisi izler; her görev başında yenisi kurulur."""

    def __init__(
        self,
        threshold: int = PRUNE_THRESHOLD_TOKENS,
        keep_steps: int = PRUNE_KEEP_STEPS,
        regrow: int = PRUNE_REGROW_TOKENS,
    ) -> None:
        self._threshold = threshold
        self._keep_steps = keep_steps
        self._regrow = regrow
        self._armed_at = threshold

    def check(
        self, history: list[Message], input_tokens: int | None
    ) -> tuple[list[Message], PruneReport] | None:
        """input_tokens: son model çağrısında sağlayıcının saydığı girdi (yoksa tahmin)."""
        tokens = input_tokens if input_tokens is not None else estimate_tokens(history)
        if tokens < self._armed_at:
            return None
        pruned, count, removed = prune(history, self._keep_steps)
        if not count:  # budanacak eski sonuç yok: bağlam biraz daha büyüyünce yeniden bak
            self._armed_at = tokens + self._regrow
            return None
        after = max(0, tokens - round(removed / CHARS_PER_TOKEN))
        self._armed_at = max(self._threshold, after + self._regrow)
        return pruned, PruneReport(count, removed, tokens, after)
