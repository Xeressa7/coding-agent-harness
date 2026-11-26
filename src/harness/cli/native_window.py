# pyright: basic
"""PySide6/qasync'e dokunan tek yer: native sohbet penceresi ve açılışı.

`run_native()`/`create_window()`'un dışında hiçbir şey PySide6'yı içe aktarmaz —
ikisi de fonksiyon gövdesinde gecikmeli import eder, bu yüzden bu modül PySide6
kurulu olmadan da import edilebilir (`tests/test_imports.py` bunu her modülden
istiyor; `create_window`/`run_native`'in kendisi yalnız `cli/native.py`'deki
`available()` zaten doğruladıktan sonra çağrılır). Dosya başındaki `basic` modu
bilinçli: PySide6/qasync kurulu değilken bu dosyadaki Qt tipleri pyright'a göre
zaten bilinmez (`reportMissingImports`), strict modun bundan kaskat üretdiği
"Unknown type" uyarıları gerçek bir hata değil — saf fonksiyonlar (`cli/native.py`)
yine strict modda kalır.

Olay sözleşmesi `cli/tui.py`'deki `HarnessApp.consume()`'un birebir aynısı: aynı
olaylar (`core/events.py`), aynı izin/soru akışı. Tek gerçek kipsel pencere izin
diyaloğu; `QuestionAsked` TUI'deki gibi sohbete mesaj olarak düşer, cevap bir
sonraki gönderimdir (ayrı bir pencere açmaz).
"""

import asyncio
from collections.abc import Callable
from contextlib import aclosing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from harness.cli.commands import ChatSession
from harness.cli.inspector import seconds, summary_items
from harness.cli.native import Block, render_blocks
from harness.cli.render import live_outcome, plan_lines
from harness.cli.tui import permission_text
from harness.core.events import (
    CodeBlocksSaved,
    Event,
    HarnessIntervened,
    ModelReasoningDelta,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    PermissionRequested,
    QuestionAsked,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.messages import ToolCall
from harness.tools.ask_user import pick_answer


def create_window(session: ChatSession, *, title: str = "Harness") -> Any:
    """`NativeWindow`'u kurar ve döner; yalnız burada PySide6 içe aktarılır."""
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import (
        QDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QTextBrowser,
        QVBoxLayout,
        QWidget,
    )

    class PermissionDialog(QDialog):
        """İzin isteğini gösterir; `cli/tui.py`'deki `PermissionDialog` ile aynı üç seçenek."""

        def __init__(self, call: ToolCall, summary: str, parent: QWidget | None = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("İzin isteniyor")
            self.choice: str = "deny"  # pencere kapatılırsa (Esc/X) güvenli taraf

            dialog_title = f"İzin isteniyor: {call.name}" + (f" · {summary}" if summary else "")
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel(dialog_title))

            args = QTextBrowser()
            args.setPlainText(permission_text(call.arguments))
            args.setMaximumHeight(160)
            layout.addWidget(args)

            buttons = QHBoxLayout()
            deny = QPushButton("Reddet")
            always = QPushButton("Oturum boyu izin ver")
            allow = QPushButton("İzin ver")
            allow.setDefault(True)
            deny.clicked.connect(lambda: self._choose("deny"))
            always.clicked.connect(lambda: self._choose("always"))
            allow.clicked.connect(lambda: self._choose("allow"))
            for button in (deny, always, allow):
                buttons.addWidget(button)
            layout.addLayout(buttons)
            allow.setFocus()

        def _choose(self, choice: str) -> None:
            self.choice = choice
            self.accept()

    class NativeWindow(QMainWindow):
        """Sohbet penceresi: `ChatSession.agent.run()`'ı doğrudan tüketir."""

        def __init__(self, session: ChatSession, *, title: str = "Harness") -> None:
            super().__init__()
            self.session = session
            self.setWindowTitle(title)
            self.resize(760, 640)

            self._blocks: list[Block] = []
            self._current_reply: Block | None = None
            self._current_thought: Block | None = None
            self._pending_answer: asyncio.Future[str | None] | None = None
            self._pending_options: tuple[str, ...] = ()
            self._current_task: asyncio.Task[None] | None = None
            self._turn_number = 0

            central = QWidget()
            layout = QVBoxLayout(central)
            self.status = QLabel(f"HARNESS · {session.label}")
            layout.addWidget(self.status)

            self.chat = QTextBrowser()
            self.chat.setOpenExternalLinks(False)
            layout.addWidget(self.chat, stretch=1)

            composer = QHBoxLayout()
            self.input = QLineEdit()
            self.input.setPlaceholderText("Görevini yaz…")
            self.input.returnPressed.connect(self._on_submit)
            self.send_button = QPushButton("Gönder")
            self.send_button.clicked.connect(self._on_submit)
            self.stop_button = QPushButton("Durdur")
            self.stop_button.setEnabled(False)
            self.stop_button.clicked.connect(self._on_stop)
            composer.addWidget(self.input, stretch=1)
            composer.addWidget(self.send_button)
            composer.addWidget(self.stop_button)
            layout.addLayout(composer)

            self.setCentralWidget(central)
            self.input.setFocus()

        # -------------------------------------------------------------- günlük

        def _add(self, kind: str, text: str = "") -> Block:
            block = Block(kind, text)
            self._blocks.append(block)
            self._redraw()
            return block

        def _append(self, block: Block, text: str) -> None:
            block.text += text
            self._redraw()

        def _redraw(self) -> None:
            self.chat.setHtml(render_blocks(self._blocks))
            bar = self.chat.verticalScrollBar()
            bar.setValue(bar.maximum())

        # -------------------------------------------------------------- gönderim

        def _on_submit(self) -> None:
            asyncio.ensure_future(self._submit())

        async def _submit(self) -> None:
            text = self.input.text().strip()
            if self._pending_answer is not None:
                self.input.clear()
                future = self._pending_answer
                self._pending_answer = None
                answer = pick_answer(text, self._pending_options)
                self._add("user", answer if answer is not None else "(cevap yok)")
                future.set_result(answer)
                return
            if not text or self._current_task is not None:
                return
            self.input.clear()
            self._current_task = asyncio.ensure_future(self.run_turn(text))

        def _on_stop(self) -> None:
            if self._current_task is not None:
                self._current_task.cancel()

        def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt'nin adlandırması)
            if self._current_task is not None:
                self._current_task.cancel()
            super().closeEvent(event)

        # -------------------------------------------------------------- tur

        async def run_turn(self, task: str) -> None:
            self._turn_number += 1
            self.send_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            try:
                await self.consume(task)
            finally:
                self._current_task = None
                self.send_button.setEnabled(True)
                self.stop_button.setEnabled(False)
                self.input.setFocus()

        async def consume(self, task: str) -> None:
            events_seen: list[Event] = []
            try:
                async with aclosing(self.session.agent.run(task)) as stream:
                    send: bool | str | None = None
                    while True:
                        try:
                            event = await stream.asend(send)
                        except StopAsyncIteration:
                            break
                        send = None
                        self.session.log.write(event)
                        events_seen.append(event)
                        match event:
                            case TaskStarted():
                                self._add("turn", f"TUR {self._turn_number}")
                            case ModelStarted():
                                self._current_reply = None
                                self._current_thought = None
                                self.status.setText("Model bekleniyor…")
                            case ModelTextDelta(text=text):
                                if self._current_reply is None:
                                    self._current_reply = self._add("agent")
                                self.status.setText("Yanıt geliyor…")
                                self._append(self._current_reply, text)
                            case ModelReasoningDelta(text=text):
                                if self._current_thought is None:
                                    self._current_thought = self._add("thought")
                                self.status.setText("Düşünüyor…")
                                self._append(self._current_thought, text)
                            case ModelReplied(text=text, reasoning=reasoning):
                                if self._current_thought is None and reasoning:
                                    self._add("thought", reasoning)
                                self._current_thought = None
                                if self._current_reply is None and text:
                                    self._add("agent", text)
                                self._current_reply = None
                                for call in event.tool_calls:
                                    self._add(
                                        "note", f"→ araç isteği: {call.name}({call.arguments})"
                                    )
                            case PermissionRequested(call=call, summary=summary):
                                self.status.setText("İzin bekleniyor…")
                                choice = await self._ask_permission(call, summary)
                                if choice == "always":
                                    self.session.agent.permissions.grant(call.name)
                                send = choice != "deny"
                            case QuestionAsked(question=question, options=options):
                                body = question
                                if options:
                                    body += "\n" + "\n".join(
                                        f"{i}. {o}" for i, o in enumerate(options, 1)
                                    )
                                self._add("question", body)
                                self.status.setText("Cevabın bekleniyor…")
                                future: asyncio.Future[str | None] = (
                                    asyncio.get_event_loop().create_future()
                                )
                                self._pending_answer = future
                                self._pending_options = options
                                self.send_button.setEnabled(True)
                                try:
                                    send = await future
                                finally:
                                    self._pending_answer = None
                                    self._pending_options = ()
                            case ToolStarted(call=call):
                                self.status.setText(f"{call.name} çalışıyor…")
                                self._add("note", f"● {call.name} çalışıyor…")
                            case (
                                ToolFinished(duration_seconds=duration, details=details) as finished
                            ):
                                self._add(
                                    "note-error" if finished.result.is_error else "note",
                                    f"{live_outcome(finished)} · {seconds(duration)}",
                                )
                                if (
                                    finished.call.name == "update_plan"
                                    and not finished.result.is_error
                                ):
                                    self._add("note", "PLAN\n" + "\n".join(plan_lines(details)))
                            case CodeBlocksSaved():
                                self._add("note", "✓ kod blokları kaydedildi")
                            case HarnessIntervened(layer=layer, summary=summary):
                                self._add("note", f"⚑ harness · {layer}: {summary}")
                            case TaskFinished(reason=reason, error=error):
                                self._add(
                                    "note",
                                    "\n".join(
                                        f"{label}: {value}"
                                        for label, value in summary_items(events_seen)
                                    ),
                                )
                                if reason == "error" and error:
                                    self._add("note-error", f"Hata: {error}")
                                self.status.setText(
                                    {
                                        "done": "Tamamlandı · Enter ile yeni mesaj",
                                        "step_limit": "Adım sınırına ulaşıldı",
                                        "cancelled": "Durduruldu",
                                        "error": f"Hata: {error.splitlines()[0][:160]}"
                                        if error
                                        else "Hata · ayrıntılar kayıtta",
                                    }[reason]
                                )
                            case _:
                                pass
            except asyncio.CancelledError:
                self._write_cancelled(task, events_seen)
                self.status.setText("Durduruldu")
            except Exception as exc:  # noqa: BLE001 — pencere çökmesin, hatayı göster
                self._add("note-error", f"Hata: {exc}")
                self.status.setText("Hata · ayrıntılar kayıtta")

        def _write_cancelled(self, task: str, events_seen: list[Event]) -> None:
            if not any(isinstance(e, TaskStarted) for e in events_seen):
                self.session.log.write(TaskStarted(task=task, timestamp=datetime.now(UTC)))
            if not any(isinstance(e, TaskFinished) for e in events_seen):
                steps = max(
                    (e.step for e in events_seen if isinstance(e, ModelStarted)),
                    default=0,
                )
                self.session.log.write(
                    TaskFinished(reason="cancelled", steps=steps, timestamp=datetime.now(UTC))
                )

        async def _ask_permission(self, call: ToolCall, summary: str) -> str:
            dialog = PermissionDialog(call, summary, self)
            dialog.exec()
            return dialog.choice

    return NativeWindow(session, title=title)


def run_native(
    make_session: Callable[[httpx.AsyncClient], ChatSession],
    *,
    title: str = "Harness",
    initial_task: str | None = None,
) -> Path:
    """Native pencereyi açar; kapanınca oturumun kayıt dosyasının yolunu döner.

    `cli/native.py:run_native()` buraya yönlendirir — `available()` zaten doğruladı.
    """
    import sys

    import qasync
    from PySide6.QtWidgets import QApplication

    app: QApplication = QApplication.instance() or QApplication(sys.argv)  # pyright: ignore[reportAssignmentType] — instance() üst sınıf QCoreApplication döner
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def run() -> Path:
        async with httpx.AsyncClient(timeout=120) as http:
            session = make_session(http)
            window = create_window(session, title=title)
            window.show()
            if initial_task is not None:
                asyncio.ensure_future(window.run_turn(initial_task))
            closed: asyncio.Future[None] = asyncio.get_event_loop().create_future()

            def on_last_window_closed() -> None:
                if not closed.done():
                    closed.set_result(None)

            app.lastWindowClosed.connect(on_last_window_closed)
            await closed
            return session.log.path

    with loop:
        return loop.run_until_complete(run())
