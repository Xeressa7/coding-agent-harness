"""Native masaüstü penceresinin PySide6'dan bağımsız, hep strict modda kalan yüzü.

Burada yalnız saf fonksiyonlar var (`Block`, `block_html`, `render_blocks`,
`available()`) — hiçbiri Qt'ye dokunmaz, pyright strict modda tam tip bilgisiyle
denetlenir. Qt/qasync'e dokunan gerçek pencere kodu ve `run_native()` ayrı bir
modülde (`cli/native_window.py`, dosya başında `# pyright: basic` ile daha gevşek
denetlenir — çünkü PySide6 kurulu olmayabilir, o zaman tipler zaten bilinmez).
`run_native()` burada yalnız ona gecikmeli bir yönlendirme olarak durur, böylece
`cli/app.py` hep `native.run_native(...)` çağırabilir.

PySide6 ve qasync isteğe bağlı (`[project.optional-dependencies] desktop`); bu
modül ikisi de kurulu olmadan import edilebilir.
"""

import html
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from harness.cli.commands import ChatSession

MISSING = (
    "PySide6/qasync kurulu değil. Kurmak için: `uv sync --extra desktop` "
    "(ya da `uv pip install PySide6 qasync`). Şimdilik tarayıcıda açılıyor."
)

# Blok türüne göre renk/biçim; web arayüzündeki CSS dilinin Qt'nin zengin metninin
# desteklediği alt kümesine (p/b + inline style) çevrilmiş hâli.
_ROLE = {"user": "Sen", "agent": "Ajan", "question": "Ajan soruyor"}
_PRIMARY = "#6895f4"
_MUTED = "#9199a5"
_DANGER = "#e2585a"
_BORDER = "#2a2e34"


@dataclass
class Block:
    """Sohbet günlüğündeki bir satır/paragraf; `text` akan metinle büyüyebilir."""

    kind: str  # "turn" | "user" | "agent" | "question" | "thought" | "note" | "note-error"
    text: str = ""


def available() -> bool:
    """PySide6 ve qasync import edilebiliyor mu (ikisi de kurulu mu)."""
    try:
        import PySide6.QtWidgets  # noqa: F401  # pyright: ignore[reportMissingImports,reportUnusedImport]
        import qasync  # noqa: F401  # pyright: ignore[reportMissingImports,reportMissingTypeStubs,reportUnusedImport]
    except ImportError:
        return False
    return True


def _escape(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


def block_html(block: Block) -> str:
    """Bir bloğun HTML'i; Qt'den bağımsız, elle test edilebilir."""
    if block.kind == "turn":
        return (
            f'<p style="color:{_MUTED};font-weight:bold;margin:14px 0 4px;">'
            f"{_escape(block.text)}</p>"
        )
    if block.kind in ("note", "note-error"):
        color = _DANGER if block.kind == "note-error" else _MUTED
        return f'<p style="color:{color};font-size:90%;margin:2px 0;">{_escape(block.text)}</p>'
    if block.kind == "thought":
        return (
            f'<p style="color:{_MUTED};font-style:italic;font-size:90%;margin:4px 0;'
            f'border-left:2px solid {_BORDER};padding-left:8px;">{_escape(block.text)}</p>'
        )
    role = _ROLE.get(block.kind, block.kind)
    return (
        f'<p style="margin:10px 0 2px;"><b style="color:{_PRIMARY};">{role}</b></p>'
        f'<p style="margin:0 0 8px;">{_escape(block.text)}</p>'
    )


def render_blocks(blocks: list[Block]) -> str:
    """Bütün günlüğün HTML'i; her değişiklikte yeniden çizilir (akış hızında sorunsuz)."""
    return "".join(block_html(b) for b in blocks)


def run_native(
    make_session: Callable[[httpx.AsyncClient], ChatSession],
    *,
    title: str = "Harness",
    initial_task: str | None = None,
) -> Path:
    """Native pencereyi açar; kapanınca oturumun kayıt dosyasının yolunu döner.

    `make_session`, `cli/tui.py`'deki `run_tui()` ile aynı imzayı taşır — aynı
    `ChatSession` fabrikaları (ör. `cli/app.py:chat_session`) doğrudan geçilebilir.
    Gerçek kurulum `cli/native_window.py`'de (yalnız orada, `available()` zaten
    doğruladıktan sonra PySide6/qasync içe aktarılır).
    """
    from harness.cli.native_window import run_native as _run_native

    return _run_native(make_session, title=title, initial_task=initial_task)
