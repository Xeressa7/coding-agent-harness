"""Bağlam yöneticisi: ContextManager (rapor Bölüm 19).

Uzun araç çıktılarını kırpar (baş ve son korunur, orta atılır, modele
kısaltıldığı söylenir) ve mesaj geçmişinin modelin bağlam penceresini
aşmasını önler.
"""

from collections.abc import Sequence

from harness.messages import Message, ToolResult


def _truncate_text(text: str, head_chars: int = 2000, tail_chars: int = 2000) -> str:
    """Uzun bir metni baş ve sonunu koruyarak kırpar, ortasını atar.

    Metin `head_chars + tail_chars`'tan kısaysa dokunmadan döner.
    """
    if len(text) <= head_chars + tail_chars:
        return text

    omitted = len(text) - head_chars - tail_chars
    head = text[:head_chars]
    tail = text[-tail_chars:]
    return f"{head}\n\n… [{omitted} karakter kısaltıldı] …\n\n{tail}"


def truncated_history(
    history: Sequence[Message], head_chars: int = 2000, tail_chars: int = 2000
) -> list[Message]:
    """Geçmişteki araç sonuçlarının içeriğini kırpar; diğer mesajlara dokunmaz."""
    result: list[Message] = []
    for message in history:
        if isinstance(message, ToolResult):
            message = message.model_copy(
                update={"content": _truncate_text(message.content, head_chars, tail_chars)}
            )
        result.append(message)
    return result
