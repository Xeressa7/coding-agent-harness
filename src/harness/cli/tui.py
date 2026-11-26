"""Tam ekran terminal ön yüzü: sohbet, bağımsız akış, sabit editör ve seçim pencereleri."""

import asyncio
import json
import time
from collections.abc import Callable
from contextlib import aclosing, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Input, Markdown, OptionList, Static, TextArea
from textual.widgets.markdown import MarkdownStream
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerCancelled

from harness.cli.attachments import (
    AttachmentError,
    dropped_paths,
    from_file,
    human_size,
    store,
)
from harness.cli.clipboard import copy_text, paste_image, paste_text
from harness.cli.commands import (
    PANEL_MODES,
    STEP_LIMIT_HINT,
    THINKING_MODES,
    ChatSession,
    Choice,
    CommandReply,
    KeyRequest,
    Picker,
    is_command,
    matching_commands,
)
from harness.cli.inspector import (
    code_blocks_summary,
    intervention_summary,
    seconds,
    summary_items,
    write_report,
)
from harness.cli.render import PermissionChoice, live_outcome, plan_lines
from harness.cli.summary import WorkSummary, elapsed, fit_path
from harness.cli.themes import available, textual_theme
from harness.core.agent import Agent
from harness.core.config import HARNESS_HOME
from harness.core.events import (
    CodeBlocksSaved,
    ContextAssembled,
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
from harness.core.session_log import SessionLogger
from harness.messages import ImageAttachment, ToolCall
from harness.tools.ask_user import pick_answer

NARROW_WIDTH = 100  # bundan dar terminalde sağ panel gizlenir (sohbet okunur kalsın)
KEYMAP_FILE = HARNESS_HOME / "keybindings.json"


def load_keymap(path: Path = KEYMAP_FILE) -> dict[str, str]:
    """Kişisel kısayollar: {"panel": "f2", "quit": "ctrl+q,ctrl+d"}; bozuk dosya yok sayılır.

    Anahtarlar HarnessApp.BINDINGS kimlikleridir; değer Textual tuş adıdır (virgül: birden çok).
    """
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    items = cast(dict[object, object], data).items()
    return {str(k): v for k, v in items if isinstance(v, str) and v.strip()}


# İş sürerken de çalışabilen, yalnız bilgi veren komutlar.
READ_ONLY_COMMANDS = {
    "/help",
    "/inspect",
    "/exit",
    "/quit",
    "/status",
    "/tools",
    "/usage",
    "/cost",
    "/watch",  # panel kaydı okur; koşu sürerken açmak asıl kullanımıdır
    "/theme",
    "/panel",  # görünüm değişir, koşu etkilenmez
    "/thinking",
}


class ComposeInput(TextArea):
    """Enter gönderir; Ctrl+J, Shift+Enter ve satır sonundaki `\\` yeni satır açar.

    Çoğu Linux terminali (GNOME Terminal, Konsole) Shift+Enter'ı düz Enter olarak yollar;
    Ctrl+J ve `\\` + Enter her terminalde çalışır. Ctrl+V sistem panosunu yapıştırır
    (Textual'ın kendi Ctrl+V'si yalnız uygulama içinde kopyalananı bilir).

    Görsel ekleri: panoda metin yoksa Ctrl+V panodaki görseli, terminale sürüklenen
    görsel dosyası da kendisini ekler. Ek, imlecin yerine `[Görsel N]` etiketi olarak
    girer; etiket metinden silinirse ek de düşer (ayrı bir silme tuşu gerekmez).
    """

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class Complete(Message):
        """Tab: yazılmakta olan /komut ilk öneriyle tamamlansın."""

    class AttachmentsChanged(Message):
        """Ek listesi değişti: üstteki ek satırı yenilensin."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._attachments: dict[str, ImageAttachment] = {}  # etiket -> görsel
        self._next_image = 1

    def attach(self, image: ImageAttachment) -> None:
        """Görseli imlecin yerine etiketiyle ekler."""
        label = f"[Görsel {self._next_image}]"
        self._next_image += 1
        self._attachments[label] = image
        before = self._before_cursor()
        spaced = ("" if not before or before.endswith((" ", "\n")) else " ") + label + " "
        result = self.replace(spaced, *self.selection)
        self.move_cursor(result.end_location)
        self.post_message(self.AttachmentsChanged())

    def attachments(self) -> list[tuple[str, ImageAttachment]]:
        """Metinde etiketi duran ekler (silinen etiketin eki sayılmaz)."""
        return [(label, image) for label, image in self._attachments.items() if label in self.text]

    def clear_attachments(self) -> None:
        self._attachments.clear()
        self._next_image = 1
        self.post_message(self.AttachmentsChanged())

    async def _on_paste(self, event: events.Paste) -> None:
        """Sürüklenen görsel dosyaları (terminal yolu yapıştırır) ek olur; gerisi metindir."""
        paths = dropped_paths(event.text)
        if paths is None:
            await super()._on_paste(event)
            return
        event.stop()
        event.prevent_default()
        for path in paths:
            try:
                image = await asyncio.to_thread(from_file, path)
            except AttachmentError as exc:
                self.notify(str(exc), title="Görsel eklenemedi", severity="error")
                continue
            self.attach(image)

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "tab" and self.text.startswith("/") and " " not in self.text:
            event.stop()
            event.prevent_default()
            self.post_message(self.Complete())
        elif event.key in {"enter", "ctrl+enter"} and self._before_cursor().endswith("\\"):
            event.stop()
            event.prevent_default()
            row, column = self.cursor_location
            result = self.replace("\n", (row, column - 1), (row, column))
            self.move_cursor(result.end_location)
        elif event.key in {"enter", "ctrl+enter"}:
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
        elif event.key in {"shift+enter", "ctrl+j"}:
            event.stop()
            event.prevent_default()
            self.insert("\n")
        else:
            await super()._on_key(event)

    def _before_cursor(self) -> str:
        row, column = self.cursor_location
        return self.document.get_line(row)[:column]

    def action_paste(self) -> None:
        """Sistem panosundan yapıştırır; pano aracı yoksa uygulama içi panoya düşer."""
        if not self.read_only:
            self.run_worker(self._paste_system(), group="paste", exclusive=True)

    async def _paste_system(self) -> None:
        text = await asyncio.to_thread(paste_text)
        if not text:
            data = await asyncio.to_thread(paste_image)
            if data is not None:
                try:
                    image = await asyncio.to_thread(store, data, f"pano-{self._next_image}")
                except AttachmentError as exc:
                    self.notify(str(exc), title="Görsel eklenemedi", severity="error")
                    return
                self.attach(image)
                return
            text = self.app.clipboard  # pyright: ignore[reportUnknownMemberType]
        if text:
            result = self.replace(text, *self.selection)
            self.move_cursor(result.end_location)


class ChatMessage(Vertical):
    def __init__(self, role: str, source: str = "", *, kind: str = "agent") -> None:
        """kind: user (kullanıcı), agent (model) ya da command (/komut cevabı)."""
        super().__init__(classes=f"message {kind}-message")
        self.role = role
        self.source = source
        self.markdown = Markdown(source, open_links=False)
        self._stream: MarkdownStream | None = None

    def compose(self) -> ComposeResult:
        yield Static(Text(self.role), classes="message-role")
        yield self.markdown

    async def append_delta(self, text: str) -> None:
        self.source += text
        if self._stream is None:
            self._stream = Markdown.get_stream(self.markdown)
        await self._stream.write(text)

    async def finish(self, source: str) -> None:
        await self.close_stream()
        self.source = source
        await self.markdown.update(source)

    async def close_stream(self) -> None:
        if self._stream is not None:
            stream, self._stream = self._stream, None
            await stream.stop()

    async def on_unmount(self) -> None:
        await self.close_stream()


class ThoughtBlock(Collapsible):
    """Sağlayıcının gösterdiği düşünce (özet ya da açık düşünce); cevabın üstünde, soluk.

    Görünüm `/thinking` ile seçilir: katlı (tek satır başlık, tıklayınca açılır), açık
    (akarken tamamen görünür) ya da gizli (hiç çizilmez; kayıtta kalır). Başlık akarken
    "Düşünüyor…", bitince "Düşünce" der ve kaç kelime olduğunu gösterir.
    """

    def __init__(self, text: str = "", *, collapsed: bool = False, done: bool = False) -> None:
        self.source = text
        self.done = done
        self._body = Static(Text(text), classes="thought-text")
        super().__init__(self._body, title=self._heading(), collapsed=collapsed, classes="thought")

    def _heading(self) -> str:
        words = len(self.source.split())
        label = "Düşünce" if self.done else "Düşünüyor…"
        return f"{label} · {words} kelime" if words else label

    def append(self, text: str) -> None:
        self.source += text
        self._body.update(Text(self.source))
        self.title = self._heading()

    def finish(self) -> None:
        self.done = True
        self.title = self._heading()


def permission_text(arguments: str) -> str:
    """Argümanı okunur kıl: JSON nesnesiyse "anahtar: değer", metinler gerçek satırlarıyla."""
    try:
        data: object = json.loads(arguments)
    except ValueError:
        return arguments
    if not isinstance(data, dict):
        return arguments
    parts: list[str] = []
    fields = cast(dict[str, object], data)
    for key, value in fields.items():
        shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        parts.append(f"{key}:\n{shown}" if "\n" in shown else f"{key}: {shown}")
    return "\n".join(parts)


class PermissionDialog(ModalScreen[PermissionChoice]):
    """İzin katmanının "sor" dediği araç çağrısını gösterir; sonuç izin ver/reddet/oturum boyu.

    Uzun argüman kendi kutusunda kayar, düğmeler hep görünür kalır; klavye: Enter/e izin ver,
    o oturum boyu, Esc/r reddet.
    """

    BINDINGS = [
        Binding("e", "choose('allow')", "İzin ver"),
        Binding("o", "choose('always')", "Oturum boyu"),
        Binding("r", "choose('deny')", "Reddet"),
    ]

    def __init__(self, call: ToolCall, summary: str = "") -> None:
        super().__init__()
        self._call = call
        self._summary = summary

    def compose(self) -> ComposeResult:
        title = f"İzin isteniyor: {self._call.name}" + (
            f" · {self._summary}" if self._summary else ""
        )
        with Vertical(id="permission-dialog"):
            yield Static(Text(title), id="permission-title")
            with Horizontal(id="permission-actions"):
                yield Button("Reddet (r)", id="deny")
                yield Button("Oturum boyu izin ver (o)", id="always")
                yield Button("İzin ver (e)", id="allow", variant="primary")
            yield Static(
                "Enter: odaktaki düğme · ↑↓ / PgUp PgDn: içeriği kaydır · Esc: reddet",
                id="permission-keys",
            )
            with VerticalScroll(id="permission-scroll"):
                yield Static(Text(permission_text(self._call.arguments)), id="permission-args")

    def on_mount(self) -> None:
        self.query_one("#allow", Button).focus()
        self.call_after_refresh(self.fit_content)

    def on_resize(self) -> None:
        self.call_after_refresh(self.fit_content)

    def fit_content(self) -> None:
        """İçerik kutusu, pencere ekranın %90'ını aşmayacak kadar yükseklik alır."""
        title = self.query_one("#permission-title", Static).outer_size.height
        rest = sum(
            self.query_one(f"#{name}").outer_size.height
            for name in ("permission-actions", "permission-keys")
        )
        room = int(self.size.height * 0.9) - 2 - title - rest  # 2: pencere çerçevesi
        self.query_one("#permission-scroll", VerticalScroll).styles.max_height = max(1, room)

    def on_key(self, event: events.Key) -> None:
        # Odak düğmedeyken de oklar içeriği kaydırır (PgUp/PgDn ve Esc uygulama bağlarından gelir).
        scroll = self.query_one("#permission-scroll", VerticalScroll)
        moves = {"up": scroll.scroll_up, "down": scroll.scroll_down}
        if event.key in moves:
            moves[event.key](animate=False)
            event.stop()

    def action_choose(self, choice: PermissionChoice) -> None:
        self.dismiss(choice)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "allow":
                self.dismiss("allow")
            case "always":
                self.dismiss("always")
            case _:
                self.dismiss("deny")


class PickerScreen(ModalScreen[Choice | None]):
    """Aranabilir seçim listesi: yazdıkça süzülür, ↑/↓ gezer, Enter seçer, Esc vazgeçer."""

    def __init__(self, picker: Picker) -> None:
        super().__init__()
        self.picker = picker
        self.shown: list[Choice | None] = []  # listedeki sıraya göre satır; None = grup başlığı

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text(self.picker.title), classes="dialog-title")
            yield Input(placeholder="Ara…", id="picker-search")
            yield OptionList(id="picker-options")
            yield Static("↑↓ gez · Enter seç · Esc vazgeç", classes="dialog-keys")

    def on_mount(self) -> None:
        self.refill("")
        self.query_one(Input).focus()

    def refill(self, query: str) -> None:
        words = query.casefold().split()
        options: list[Option] = []
        self.shown = []
        group = ""
        current: int | None = None
        for choice in self.picker.choices:
            haystack = f"{choice.group} {choice.label} {choice.detail}".casefold()
            if not all(word in haystack for word in words):
                continue
            if choice.group and choice.group != group:
                group = choice.group
                options.append(Option(Text(group, style="bold"), disabled=True))
                self.shown.append(None)
            line = Text(("● " if choice.current else "  ") + choice.label)
            if choice.detail:
                line.append(f"  {choice.detail}", style="dim")
            if choice.current and current is None:
                current = len(options)
            options.append(Option(line, disabled=not choice.enabled))
            self.shown.append(choice)
        listing = self.query_one(OptionList)
        listing.clear_options()
        listing.add_options(options)
        selectable = [i for i, c in enumerate(self.shown) if c is not None and c.enabled]
        if selectable:
            # Arama yokken imleç şu anki seçimde; arama varsa ilk eşleşmede.
            listing.highlighted = current if current in selectable and not words else selectable[0]

    def on_input_changed(self, event: Input.Changed) -> None:
        self.refill(event.value)

    def on_key(self, event: events.Key) -> None:
        # Odak arama kutusundayken de ok tuşları listeyi gezer.
        listing = self.query_one(OptionList)
        if event.key == "down":
            listing.action_cursor_down()
            event.stop()
        elif event.key == "up":
            listing.action_cursor_up()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.select(self.query_one(OptionList).highlighted)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.select(event.option_index)

    def select(self, index: int | None) -> None:
        choice = self.shown[index] if index is not None and index < len(self.shown) else None
        if choice is not None and choice.enabled:
            self.dismiss(choice)


class InfoScreen(ModalScreen[None]):
    """Bilgi komutlarının (/help, /status, /usage, /diff…) penceresi: sohbete yazılmaz."""

    BINDINGS = [Binding("escape", "dismiss", "Kapat"), Binding("q", "dismiss", show=False)]

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self.title_text = title
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog info-dialog"):
            yield Static(Text(self.title_text), classes="dialog-title")
            with VerticalScroll(id="info-body"):
                yield Markdown(self.text, open_links=False)
            yield Static("Esc: kapat · ↑↓ / PgUp PgDn: kaydır", classes="dialog-keys")

    def on_mount(self) -> None:
        self.query_one("#info-body", VerticalScroll).focus()

    async def action_dismiss(self, result: None = None) -> None:
        self.dismiss(result)


def attachment_note(attached: list[tuple[str, ImageAttachment]]) -> str:
    """Sohbetteki kullanıcı mesajının altına eklerin listesi (Markdown)."""
    if not attached:
        return ""
    lines = [f"- {label} {image.name} · {human_size(image.size)}" for label, image in attached]
    return "\n\n" + "\n".join(lines)


def note_text(markdown: str) -> str:
    """Bildirim için düz metin: Markdown işaretleri atılır (bildirim Markdown çizmez)."""
    return markdown.replace("**", "").replace("`", "")


class KeyScreen(ModalScreen[str | None]):
    """API anahtarı için gizli alan; yazılan ekranda görünmez."""

    def __init__(self, request: KeyRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(Text(f"{self.request.provider} bağla"), classes="dialog-title")
            yield Static(Text(self.request.prompt), classes="dialog-text")
            yield Input(placeholder="API anahtarı", password=True, id="key-input")
            yield Static("Enter kaydet · Esc vazgeç", classes="dialog-keys")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.dismiss(event.value.strip() or None)


class HarnessApp(App[None]):
    TITLE = "Harness"
    CSS_PATH = "tui.tcss"
    # Kimlikler (`id`) ~/.harness/keybindings.json'daki adlardır: {"panel": "f2"} gibi.
    BINDINGS = [
        Binding("ctrl+q", "quit", "Çıkış", priority=True, id="quit"),
        Binding("escape", "stop", "Durdur", priority=True, id="stop"),
        Binding("ctrl+c", "interrupt", "Durdur/kopyala", priority=True, id="interrupt"),
        Binding("ctrl+b", "cycle_panel", "Panel", priority=True, id="panel"),
        Binding("ctrl+end", "latest", "En alta", priority=True, id="latest"),
        Binding("pageup", "page_up", show=False, priority=True, id="page_up"),
        Binding("pagedown", "page_down", show=False, priority=True, id="page_down"),
        Binding("ctrl+t", "toggle_theme", "Tema", priority=True, id="theme"),
    ]
    QUIT_WINDOW_SECONDS = 2.0  # boştayken iki Ctrl+C arasındaki süre: çıkış

    def __init__(
        self,
        session: ChatSession,
        *,
        initial_task: str | None = None,
        initial_reply: CommandReply | None = None,
        keymap: dict[str, str] | None = None,
    ) -> None:
        """keymap: kısayol kimliği -> tuş (`load_keymap`; testlerde boş, varsayılan tuşlar)."""
        super().__init__()
        self.session = session
        self.initial_task = initial_task
        self.initial_reply = initial_reply
        self.busy = False
        self.failed = False
        self.report: Path | None = None
        self._turn: Worker[None] | None = None
        self._reply: ChatMessage | None = None
        self._thought: ThoughtBlock | None = None
        self._turn_number = 0
        self._active_question: str | None = None
        self._active_events: list[Event] = []
        self._suggesting = False  # durum satırı şu an komut önerisi gösteriyor
        self._ask_options: tuple[str, ...] = ()  # bekleyen ask_user sorusunun seçenekleri
        self._ask_answer: asyncio.Future[str | None] | None = None
        self.summary = WorkSummary()
        self._tool_entries: dict[str, tuple[Static, Static]] = {}  # çağrı -> (akış, sohbet satırı)
        self._model_entry: Static | None = None
        self._last_interrupt = 0.0
        self._keymap = dict(keymap or {})
        for palette in available().values():
            self.register_theme(textual_theme(palette))
        self.theme = f"harness-{session.theme}"

    # Model, ajan ve kayıt /komutlarla değişebilir; her seferinde oturumdan okunur.
    @property
    def agent(self) -> Agent:
        return self.session.agent

    @property
    def session_log(self) -> SessionLogger:
        return self.session.log

    @property
    def model_label(self) -> str:
        return self.session.label

    @property
    def workdir(self) -> Path:
        """Komutların başladığı klasör: kök + bash'in kalıcı `cd`'si (kökteyse kökün kendisi).

        Proje kökü sabittir (dosya araçları, oturum kaydı oraya göredir); bash'in `cd`'si
        yalnız komutların nerede koştuğunu değiştirir. Üst bar ikisini tek yolda gösterir.
        Araç yeniden kurulunca (anatomi/model değişimi) bash kökten başlar, yol da köke döner.
        """
        bash = self.session.tools.get("bash")
        cwd = getattr(bash, "cwd", ".")
        return self.session.root if cwd in ("", ".") else self.session.root / cwd

    def topbar_text(self) -> Text:
        """Tek satır: model solda, komutların çalıştığı yol sağda; sığmazsa yol baştan kısalır."""
        root = str(self.workdir)
        home = str(Path.home())
        if root == home or root.startswith(home + "/"):
            root = "~" + root[len(home) :]
        head = f" HARNESS  ·  {self.model_label}"
        room = max(self.size.width - len(head) - 6, 12)
        return Text(f"{head}  ·  {fit_path(root, room)}", no_wrap=True, overflow="ellipsis")

    def keys_hint(self) -> str:
        """Alt satırdaki kısayol listesi; keybindings.json'daki değişiklikleri yansıtır."""

        def key(binding_id: str, default: str) -> str:
            chosen = self._keymap.get(binding_id, default).split(",")[0].strip()
            names = {"escape": "Esc", "pageup": "PgUp", "pagedown": "PgDn"}
            return "+".join(names.get(part, part.capitalize()) for part in chosen.split("+"))

        return (
            " Enter gönder · Ctrl+J satır · "
            f"{key('stop', 'escape')}/{key('interrupt', 'ctrl+c')} durdur · "
            f"{key('panel', 'ctrl+b')} panel · {key('theme', 'ctrl+t')} tema · "
            f"{key('quit', 'ctrl+q')} çık"
        )

    def apply_display(self) -> None:
        """Panel ve düşünce görünümü sınıf olarak uygulanır; CSS neyin görüneceğine karar verir.

        Araç satırları hem akışa hem sohbete yazılır; hangisinin görüneceği yalnız CSS'tir,
        böylece görünüm değişince geçmiş turlar da yeni görünüme uyar.
        """
        for mode in PANEL_MODES:
            self.set_class(self.session.panel == mode, f"panel-{mode}")
        for mode in THINKING_MODES:
            self.set_class(self.session.thinking == mode, f"thinking-{mode}")
        title = {"ozet": "ÖZET", "akis": "AKIŞ"}.get(self.session.panel, "")
        self.query_one("#flow-title", Static).update(title)
        collapsed = self.session.thinking == "katli"
        for thought in self.query(ThoughtBlock):
            thought.collapsed = collapsed
        self.refresh_summary()

    def refresh_summary(self) -> None:
        width = max(self.query_one("#flow-pane").size.width - 3, 16)
        self.query_one("#summary", Static).update(self.summary.render(width))

    def compose(self) -> ComposeResult:
        yield Static(self.topbar_text(), id="topbar")
        with Horizontal(id="body"):
            with Vertical(id="chat-pane"):
                with Horizontal(id="chat-heading"):
                    yield Static("SOHBET", id="chat-title")
                    yield Button("↓ En alta", id="latest", variant="default", compact=True)
                with VerticalScroll(id="conversation", can_focus=True):
                    yield Static(
                        Text(
                            "Birlikte ne yapalım?\n\n"
                            "Mesajını aşağıya yaz. Yanıtlar burada akar; "
                            "işlem durumu sağda kalır.\n"
                            "Yukarı kaydırdığında okuduğun yer korunur.\n"
                            "/ yazıp Enter: komut listesi · /sessions: geçmiş oturumlar · "
                            "/model: model seç"
                        ),
                        id="welcome",
                    )
            with Vertical(id="flow-pane"):
                yield Static("ÖZET", id="flow-title")
                with VerticalScroll(id="flow", can_focus=True):
                    yield Static(Text("Hazır.\nHenüz görev yok."), id="flow-empty")
                with VerticalScroll(id="summary-scroll", can_focus=True):
                    yield Static(id="summary")
        with Vertical(id="composer"):
            yield Static(id="attachments")
            yield ComposeInput(
                id="message-input",
                placeholder="Mesajını yaz…  (Ctrl+J ya da \\ + Enter: yeni satır)",
                soft_wrap=True,
                show_line_numbers=False,
                highlight_cursor_line=False,
            )
            with Horizontal(id="actions"):
                yield Static("Hazır · Enter gönder", id="status")
                yield Button("Rapor", id="inspect", disabled=True, compact=True)
                yield Button("Durdur", id="stop", disabled=True, compact=True)
                yield Button("Gönder", id="send", variant="primary", compact=True)
        yield Static(self.keys_hint(), id="keys")

    async def on_mount(self) -> None:
        self.set_interval(1.0, self.tick)
        if self._keymap:
            self.set_keymap(self._keymap)
        self.apply_display()
        self.query_one("#conversation", VerticalScroll).anchor()
        self.query_one("#flow", VerticalScroll).anchor()
        self.query_one(ComposeInput).focus()
        if self.initial_reply is not None:  # harness -c / -r: önceki oturum ya da seçim listesi
            await self.handle_reply(self.initial_reply)
        if self.initial_task:
            await self.submit(self.initial_task)

    def on_resize(self, event: events.Resize) -> None:
        self.set_class(event.size.height < 25, "compact")
        # Dar terminalde panel sohbeti ezer: görünüm ayarı korunur, panel yalnız gizlenir.
        self.set_class(event.size.width < NARROW_WIDTH, "narrow")
        self.query_one("#topbar", Static).update(self.topbar_text())
        self.call_after_refresh(self.refresh_summary)

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(Text(text))

    async def add_flow(self, text: str) -> Static:
        entry = Static(Text(text), classes="flow-entry")
        await self.query_one("#flow", VerticalScroll).mount(entry)
        return entry

    async def add_tool_line(self, text: str) -> Static:
        """Sohbette tek satırlık araç izi (akış paneli görünmüyorken ne olduğunu söyler)."""
        line = Static(Text(text), classes="tool-line")
        await self.query_one("#conversation", VerticalScroll).mount(line)
        return line

    @on(ComposeInput.Submitted)
    async def input_submitted(self, event: ComposeInput.Submitted) -> None:
        await self.submit(event.text)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "send":
                await self.submit(self.query_one(ComposeInput).text)
            case "stop":
                await self.action_stop()
            case "latest":
                self.action_latest()
            case "inspect":
                await self.action_inspect()
            case _:
                pass

    def answer_question(self, typed: str) -> None:
        """Bekleyen ask_user sorusunu çözer: boş cevap yok, seçenek numarası o seçenek."""
        future = self._ask_answer
        if future is None or future.done():
            return
        self.query_one(ComposeInput).clear()
        future.set_result(pick_answer(typed, self._ask_options))

    async def ask_inline(self, question: str, options: tuple[str, ...]) -> str | None:
        """Soru konuşmaya mesaj olarak düşer; cevap alt editörden gelir (ayrı pencere yok).

        Soru beklenirken Enter/Gönder cevabı yollar, bilgi komutları çalışır, Esc turu
        durdurur. Cevap soru mesajının altına işlenir; sohbet bütünsel kalır.
        """
        hint = (
            "Numara ya da kendi cevabın · boş Enter: cevap yok"
            if options
            else "Cevabın · boş Enter: cevap yok"
        )
        body = question
        if options:
            body += "\n\n" + "\n".join(f"{i}. {option}" for i, option in enumerate(options, 1))
        body += f"\n\n*{hint}*"
        message = ChatMessage("Ajan soruyor", body, kind="agent")
        await self.query_one("#conversation", VerticalScroll).mount(message)
        answer_future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._ask_options = options
        self._ask_answer = answer_future
        self.query_one("#send", Button).disabled = False  # Gönder düğmesi de cevabı yollar
        self.query_one(ComposeInput).focus()
        # Durum ancak cevap yolu hazırken "bekleniyor" der; önce yazılırsa erken Enter boşa gider.
        self.set_status("Cevabın bekleniyor… · boş Enter: cevap yok")
        try:
            answer = await answer_future
        finally:
            self._ask_answer = None
            self._ask_options = ()
        suffix = f"\n\n**Cevap:** {answer}" if answer is not None else "\n\n*Cevap yok*"
        await message.finish(body + suffix)
        self.query_one("#send", Button).disabled = True  # iş bitene dek yine kapalı
        return answer

    async def submit(self, text: str) -> None:
        if self._ask_answer is not None:
            typed = text.strip()
            if is_command(typed):
                # Soru beklerken de bilgi komutları çalışır; öbürleri busy korumasına takılır.
                await self.run_command(typed)
                return
            self.answer_question(text)
            return
        question = text.strip()
        if not question:
            return
        editor = self.query_one(ComposeInput)
        if is_command(question):
            await self.run_command(question)
            return
        if self.busy:
            self.set_status("Görev sürüyor; taslağın korunuyor.")
            return
        if self.failed:
            self.set_status("Bu oturum kesildi · /new ya da /resume ile devam et")
            return
        attached = editor.attachments()
        images = tuple(image for _, image in attached)
        if images and await self.session.image_support() is False:
            self.set_status(
                f"{self.session.model} görsel kabul etmiyor · taslak korundu · "
                "/model ile görsel destekli bir model seç ya da etiketi sil"
            )
            return
        self.busy = True
        self._active_question = question
        self._active_events = []
        self._turn_number += 1
        self.query_one("#send", Button).disabled = True
        self.query_one("#stop", Button).disabled = False
        editor.clear()
        editor.clear_attachments()
        editor.focus()
        self.query_one("#welcome").display = False
        self.query_one("#flow-empty").display = False
        # Çizimi beklemeden worker'ı kaydet; durdurma önceki turu hedeflemesin.
        user_mounted = self.query_one("#conversation", VerticalScroll).mount(
            ChatMessage("Sen", question + attachment_note(attached), kind="user")
        )

        async def turn() -> None:
            await user_mounted
            await self.consume(question, images)

        self._turn = self.run_worker(
            turn,
            name="agent-turn",
            group="agent",
            exit_on_error=False,
        )

    async def run_command(self, text: str) -> None:
        name = text.split()[0].lower()
        if self.busy and name not in READ_ONLY_COMMANDS:
            self.set_status("Görev sürüyor; komut için bitmesini bekle ya da Esc.")
            return
        self.query_one(ComposeInput).clear()
        self.set_status("Komut çalışıyor…")
        reply = await self.session.run(text)
        if reply.exit:
            await self.action_quit()
            return
        if reply.inspect:
            await self.action_inspect()
            return
        await self.handle_reply(reply)

    async def handle_reply(self, reply: CommandReply) -> None:
        """Cevabı gösterir; sıradaki adım varsa (liste, anahtar, giriş) onu açar."""
        if reply.theme is not None:
            self.theme = f"harness-{reply.theme}"
        if reply.display:
            self.apply_display()
        await self.show_reply(reply)
        if reply.picker is not None:
            picker = reply.picker

            async def picked(choice: Choice | None) -> None:
                if choice is not None:
                    kind = choice.kind or picker.kind
                    await self.handle_reply(await self.session.choose(kind, choice.value))

            await self.push_screen(PickerScreen(picker), picked)
        elif reply.key is not None:
            request = reply.key

            async def entered(key: str | None) -> None:
                if key is None:
                    self.set_status("Vazgeçildi · anahtar kaydedilmedi")
                else:
                    await self.handle_reply(await self.session.save_key(request.provider, key))

            await self.push_screen(KeyScreen(request), entered)
        elif reply.login:
            self.set_status("Tarayıcıda ChatGPT girişi bekleniyor…")
            self.run_worker(self.login(), group="login", exclusive=True, exit_on_error=False)

    async def login(self) -> None:
        def show_url(url: str) -> None:
            text = f"Tarayıcı açılamadı; bu adresi tarayıcıya yapıştır:\n\n{url}"
            message = ChatMessage("Harness", text, kind="command")
            try:  # giriş ayrı iş parçacığında bekler
                self.call_from_thread(self.query_one("#conversation").mount, message)
            except RuntimeError:
                self.query_one("#conversation").mount(message)

        await self.handle_reply(await self.session.login(show_url))

    async def show_reply(self, reply: CommandReply) -> None:
        """Komut cevabı sohbeti kirletmez: bilgi pencerede, onay kısa bildirimde çıkar."""
        if reply.reset:
            await self.reset_conversation(reply.transcript)
        if reply.clipboard is not None:
            self.copy_to_clipboard(reply.clipboard)
        if reply.text:
            if reply.view == "chat":
                self.query_one("#welcome").display = False
                await self.query_one("#conversation", VerticalScroll).mount(
                    ChatMessage("Harness", reply.text, kind="command")
                )
            elif reply.view == "panel":
                await self.push_screen(InfoScreen(reply.title or "Harness", reply.text))
            else:
                self.notify(note_text(reply.text), title="Harness", timeout=8, markup=False)
        self.query_one("#topbar", Static).update(self.topbar_text())
        self.set_status("Görev sürüyor…" if self.busy else "Hazır · Enter gönder")

    @on(ComposeInput.AttachmentsChanged)
    @on(TextArea.Changed, "#message-input")
    def show_attachments(self) -> None:
        """Mesaj kutusunun üstünde ekli görseller; etiketi silinen görsel listeden düşer."""
        attached = self.query_one(ComposeInput).attachments()
        bar = self.query_one("#attachments", Static)
        bar.display = bool(attached)
        if attached:
            shown = "   ".join(
                f"▣ {label} {image.name} · {human_size(image.size)}" for label, image in attached
            )
            bar.update(Text(f"{shown}   (etiketi silince ek düşer)", no_wrap=True))

    @on(TextArea.Changed, "#message-input")
    def suggest_commands(self, event: TextArea.Changed) -> None:
        """`/` ile başlayan taslakta eşleşen komutlar durum satırında; Tab ilkini tamamlar."""
        text = event.text_area.text
        if not text.startswith("/") or " " in text or "\n" in text:
            if self._suggesting:
                self._suggesting = False
                self.set_status("Görev sürüyor…" if self.busy else "Hazır · Enter gönder")
            return
        found = matching_commands(text)
        self._suggesting = True
        if text == "/":
            self.set_status("Enter: komut listesi · ya da yazmaya devam et")
        elif found:
            shown = " · ".join(f"{c.name} {c.summary.split(';')[0]}" for c in found[:3])
            self.set_status(f"Tab ↹ {shown}")
        else:
            self.set_status(f"{text}: böyle bir komut yok · / Enter: liste")

    @on(ComposeInput.Complete)
    def complete_command(self) -> None:
        editor = self.query_one(ComposeInput)
        found = matching_commands(editor.text)
        if found:
            editor.text = found[0].name + " "
            editor.move_cursor(editor.document.end)

    async def reset_conversation(self, transcript: tuple[tuple[str, str], ...]) -> None:
        """Yeni ya da devam edilen oturum: ekran o oturumun konuşmasını gösterir."""
        await self.query(ChatMessage).remove()
        await self.query(".flow-entry").remove()
        await self.query(".tool-line").remove()
        await self.query(ThoughtBlock).remove()
        self._tool_entries.clear()
        self.summary.reset()
        self.refresh_summary()
        conversation = self.query_one("#conversation", VerticalScroll)
        for role, text in transcript:
            kind = "user" if role == "Sen" else "agent"
            await conversation.mount(ChatMessage(role, text, kind=kind))
        self.query_one("#welcome").display = not transcript
        self.query_one("#flow-empty").display = True
        self._turn_number = sum(role == "Sen" for role, _ in transcript)
        self.failed = False
        self.report = None
        self.query_one("#inspect", Button).disabled = True
        self.query_one("#send", Button).disabled = False

    async def consume(self, question: str, images: tuple[ImageAttachment, ...] = ()) -> None:
        events_seen = self._active_events
        finished = False
        self._reply = None
        try:
            async with aclosing(self.agent.run(question, images)) as stream:
                send: bool | str | None = None
                while True:
                    try:
                        event = await stream.asend(send)
                    except StopAsyncIteration:
                        break
                    send = None
                    self.session_log.write(event)
                    events_seen.append(event)
                    self.summary.update(event)
                    self.refresh_summary()
                    match event:
                        case TaskStarted():
                            await self.add_flow(f"TUR {self._turn_number}")
                        case ModelStarted(step=step):
                            self._reply = None
                            self._thought = None
                            self.set_status("Model bekleniyor…")
                            self._model_entry = await self.add_flow(f"● Model · adım {step}")
                        case ModelTextDelta(text=text):
                            if self._reply is None:
                                self._reply = ChatMessage("Ajan")
                                await self.query_one("#conversation", VerticalScroll).mount(
                                    self._reply
                                )
                            self.set_status("Yanıt geliyor…")
                            await self._reply.append_delta(text)
                        case ModelReasoningDelta(text=text):
                            if self._thought is None:
                                self._thought = self.new_thought()
                                await self.query_one("#conversation", VerticalScroll).mount(
                                    self._thought, before=self._reply
                                )
                            self.set_status("Düşünüyor…")
                            self._thought.append(text)
                        case ModelReplied(
                            step=step, text=text, duration_seconds=duration, reasoning=reasoning
                        ):
                            if self._thought is None and reasoning:
                                # Akış vermeyen adaptör: özet tamamlanmış yanıtla gelir.
                                await self.query_one("#conversation", VerticalScroll).mount(
                                    self.new_thought(reasoning, done=True), before=self._reply
                                )
                            elif self._thought is not None:
                                self._thought.finish()
                            self._thought = None
                            if self._reply is None and text:
                                self._reply = ChatMessage("Ajan")
                                await self.query_one("#conversation", VerticalScroll).mount(
                                    self._reply
                                )
                            if self._reply is not None:
                                await self._reply.finish(text)
                            done = f"✓ Model · adım {step} · {seconds(duration)}"
                            if self._model_entry is not None:
                                self._model_entry.update(Text(done))
                                self._model_entry = None
                            else:
                                await self.add_flow(done)
                        case PermissionRequested(call=call, summary=summary):
                            self.set_status("İzin bekleniyor…")
                            await self.add_flow(f"? izin isteniyor · {call.name}")
                            # Karar argümana göre verilir; ayrıntı yalnız pencerede görünür.
                            choice = await self.push_screen_wait(PermissionDialog(call, summary))
                            if choice == "always":
                                self.agent.permissions.grant(call.name)
                            send = choice != "deny"
                        case QuestionAsked(question=question, options=options):
                            await self.add_flow(f"? ajan soruyor · {question}")
                            send = await self.ask_inline(question, options)
                        case ToolStarted(call=call):
                            self.set_status(f"{call.name} çalışıyor…")
                            self._tool_entries[call.id] = (
                                await self.add_flow(f"● {call.name}"),
                                await self.add_tool_line(f"● {call.name} …"),
                            )
                        case ToolFinished(
                            call=call,
                            result=result,
                            duration_seconds=duration,
                            details=details,
                        ):
                            # Yol, komut ve çıktı akışta görünmez (rapordadır); ne olduğu görünür.
                            outcome = live_outcome(event)
                            entry, line = self._tool_entries.pop(call.id, (None, None))
                            flow_text = Text(f"{outcome}\n  {seconds(duration)}")
                            # Kısa sürenin sohbette anlamı yok; yarım saniyeyi aşınca yazılır.
                            took = (
                                f" · {elapsed(duration)}"
                                if duration is not None and duration >= 0.5
                                else ""
                            )
                            line_text = Text(f"{outcome}{took}")
                            if entry is None or line is None:
                                entry = await self.add_flow("")
                                line = await self.add_tool_line("")
                            entry.update(flow_text)
                            line.update(line_text)
                            line.set_class(result.is_error, "tool-error")
                            if call.name == "bash":
                                # Kalıcı `cd` komutun bittiği klasörü değiştirir; üst barı tazele.
                                self.query_one("#topbar", Static).update(self.topbar_text())
                            if call.name == "update_plan" and not result.is_error:
                                await self.add_flow("PLAN\n" + "\n".join(plan_lines(details)))
                        case CodeBlocksSaved():
                            await self.add_flow(f"✓ {code_blocks_summary(event)}")
                        case HarnessIntervened():
                            await self.add_flow(f"⚑ harness · {intervention_summary(event)}")
                        case TaskFinished(reason=reason, error=error):
                            finished = True
                            if reason in {"cancelled", "error"}:
                                self.failed = True
                            await self.add_flow(
                                "\n".join(
                                    f"{label}: {value}"
                                    for label, value in summary_items(events_seen)
                                )
                            )
                            if reason == "error" and error:
                                await self.add_flow(f"! Hata: {error}")
                            self.set_status(
                                {
                                    "done": "Tamamlandı · Enter ile yeni mesaj",
                                    "step_limit": STEP_LIMIT_HINT,
                                    "cancelled": "Durduruldu · /new ya da /resume ile devam et",
                                    "error": (
                                        f"Hata: {error.splitlines()[0][:160]}"
                                        if error
                                        else "Hata · ayrıntılar kayıtta"
                                    ),
                                }[reason]
                            )
                        case ContextAssembled():
                            pass  # Bağlam terminale sığmaz; kayıtta ve HTML incelemede durur.
        except asyncio.CancelledError:
            if not finished:
                self._record_cancelled_turn(question, events_seen)
                self.failed = True
                self.set_status("Durduruldu · /new ya da /resume ile devam et")
            raise
        except Exception:
            self.failed = True
            self.set_status("Hata · ayrıntılar kayıtta")
            if not finished:
                await self.add_flow("! Görev tamamlanamadı; kayıt korundu.")
        finally:
            if self._reply is not None:
                await self._reply.close_stream()
                if self.failed:
                    self._reply.query_one(".message-role", Static).update(
                        Text("Ajan · yanıt tamamlanmadı")
                    )
            if events_seen:
                # Rapor burada üretilmez: büyük kayıtta saniyeler sürer; "İncele" deyince yazılır.
                self.query_one("#inspect", Button).disabled = False
            self.busy = False
            self.query_one("#send", Button).disabled = self.failed
            self.query_one("#stop", Button).disabled = True
            if self._active_question == question:
                self._active_question = None
                self._active_events = []

    def _record_cancelled_turn(self, question: str, events_seen: list[Event]) -> None:
        if not any(isinstance(event, TaskStarted) for event in events_seen):
            started = TaskStarted(
                task=question,
                model=self.model_label,
                timestamp=datetime.now(UTC),
            )
            self.session_log.write(started)
            events_seen.append(started)
        if any(isinstance(event, TaskFinished) for event in events_seen):
            return
        steps = max(
            (event.step for event in events_seen if isinstance(event, ModelStarted)),
            default=0,
        )
        finished = TaskFinished(
            reason="cancelled",
            steps=steps,
            timestamp=datetime.now(UTC),
        )
        self.session_log.write(finished)
        events_seen.append(finished)

    async def wait_turn(self) -> None:
        if self._turn is not None:
            with suppress(WorkerCancelled):
                await self._turn.wait()

    def new_thought(self, text: str = "", *, done: bool = False) -> ThoughtBlock:
        return ThoughtBlock(text, collapsed=self.session.thinking == "katli", done=done)

    def copy_to_clipboard(self, text: str) -> None:
        """OSC 52'ye ek olarak sistem pano aracıyla da yazar (VTE OSC 52'yi yok sayar)."""
        super().copy_to_clipboard(text)

        async def copy() -> None:
            if not await asyncio.to_thread(copy_text, text):
                self.notify(
                    "Pano aracı bulunamadı (wl-clipboard ya da xclip kurun); "
                    "terminal destekliyorsa OSC 52 ile kopyalandı.",
                    title="Kopyala",
                    severity="warning",
                )

        self.run_worker(copy(), group="clipboard", exit_on_error=False)

    async def action_interrupt(self) -> None:
        """Ctrl+C, Linux alışkanlığıyla: önce seçili metni kopyalar, sonra işi durdurur,
        sonra taslağı siler; boştayken art arda iki kez basılırsa çıkar."""
        focused = self.focused
        selected = (
            focused.selected_text if isinstance(focused, TextArea) else None
        ) or self.screen.get_selected_text()
        if selected:
            self.copy_to_clipboard(selected)
            self.screen.clear_selection()
            self.notify(f"Kopyalandı ({len(selected)} karakter)", timeout=2)
            return
        if self.screen is not self.screen_stack[0] or self.busy:
            await self.action_stop()
            return
        editor = self.query_one(ComposeInput)
        if editor.text:
            editor.clear()
            self.set_status("Taslak silindi · çıkmak için Ctrl+C'ye iki kez bas")
            return
        now = time.monotonic()
        if now - self._last_interrupt < self.QUIT_WINDOW_SECONDS:
            await self.action_quit()
            return
        self._last_interrupt = now
        self.set_status("Çıkmak için Ctrl+C'ye tekrar bas")

    def action_cycle_panel(self) -> None:
        name = self.session.cycle_panel()
        self.apply_display()
        self.notify(f"Panel: {name}", timeout=2)

    def tick(self) -> None:
        """Tur sürerken özet panelindeki sayaç saniyede bir ilerlesin."""
        if self.busy:
            self.refresh_summary()

    async def action_stop(self) -> None:
        if isinstance(self.screen, PermissionDialog):
            self.screen.dismiss("deny")  # Esc izin penceresinde "reddet" demektir
            return
        if isinstance(self.screen, (PickerScreen, KeyScreen, InfoScreen)):
            self.screen.dismiss(None)
            return
        if self.busy and self._turn is not None:
            self._turn.cancel()
            await self.wait_turn()
            # Başlamadan iptal edilen coroutine consume() finally bloğuna giremez.
            if self.busy:
                if self._active_question is not None:
                    self._record_cancelled_turn(self._active_question, self._active_events)
                    self.query_one("#inspect", Button).disabled = False
                    self._active_question = None
                    self._active_events = []
                self.busy = False
                self.failed = True
                self.set_status("Durduruldu · /new ya da /resume ile devam et")
                self.query_one("#send", Button).disabled = True
                self.query_one("#stop", Button).disabled = True

    async def action_quit(self) -> None:
        await self.action_stop()
        self.exit()

    def action_latest(self) -> None:
        self.query_one("#conversation", VerticalScroll).scroll_end(animate=False, immediate=True)

    def page_target(self) -> VerticalScroll:
        if isinstance(self.screen, PermissionDialog):
            return self.screen.query_one("#permission-scroll", VerticalScroll)
        flow = self.query_one("#flow", VerticalScroll)
        return (
            flow
            if self.focused is flow or (self.focused is not None and flow in self.focused.ancestors)
            else self.query_one("#conversation", VerticalScroll)
        )

    def action_page_up(self) -> None:
        self.page_target().scroll_page_up(animate=False)

    def action_page_down(self) -> None:
        self.page_target().scroll_page_down(animate=False)

    def action_toggle_theme(self) -> None:
        self.theme = f"harness-{self.session.cycle_theme()}"

    async def build_report(self) -> Path | None:
        """Kayıttan HTML incelemeyi o an üretir (arayüzü dondurmamak için ayrı iş parçacığında)."""
        path = self.session_log.path
        if not path.is_file() or path.stat().st_size == 0:
            self.set_status("Henüz inceleme raporu yok.")
            return None
        self.set_status("İnceleme hazırlanıyor…")
        try:
            self.report = await asyncio.to_thread(write_report, path)
        except (OSError, ValueError):
            await self.add_flow("! Rapor oluşturulamadı; JSONL kaydına bak.")
            return None
        self.set_status(f"İnceleme: {self.report}")
        return self.report

    async def action_inspect(self) -> None:
        report = await self.build_report()
        if report is not None:
            self.open_url(report.resolve().as_uri())


def run_tui(
    make_session: Callable[[httpx.AsyncClient], ChatSession],
    *,
    resume: bool = False,
    pick: bool = False,
) -> Path:
    """Tam ekran sohbeti açar; kapanınca son kullanılan kayıt dosyasının yolunu döner."""

    async def run() -> Path:
        async with httpx.AsyncClient(timeout=120) as http:
            session = make_session(http)
            reply = (
                session.resume_latest()
                if resume
                else await session.run("/resume")
                if pick
                else None
            )
            await HarnessApp(session, initial_reply=reply, keymap=load_keymap()).run_async()
            return session.log.path

    return asyncio.run(run())
