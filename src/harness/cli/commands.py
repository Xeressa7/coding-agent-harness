"""Sohbet içi /komutlar: model, effort ve oturum değiştirme.

Tam ekran (tui.py) ve sade sohbet (app.py) aynı ChatSession'ı kullanır; komutun
ne yaptığı burada, nasıl gösterildiği ön yüzdedir. Cevaplar Markdown metnidir.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from harness.core.agent import Agent
from harness.core.anatomy import Anatomy
from harness.core.events import ModelReplied, TaskStarted
from harness.core.session_log import (
    SessionLogger,
    SessionSummary,
    history_from_events,
    list_sessions,
    read_session,
)
from harness.llm.base import LLMClient
from harness.llm.responses import ModelInfo
from harness.messages import Message

LLMFactory = Callable[[str, str | None], LLMClient]
ModelLoader = Callable[[], Awaitable[list[ModelInfo]]]

HELP = """\
| Komut | Ne yapar |
|---|---|
| `/model` | Modelleri listeler; `/model <numara\\|ad>` modeli değiştirir |
| `/effort` | Düşünme seviyelerini listeler; `/effort <numara\\|ad>` değiştirir |
| `/new` | Yeni oturum: geçmiş sıfırlanır, yeni kayıt dosyası açılır |
| `/sessions` | Bu projenin son oturumları |
| `/resume <numara>` | Listeden bir oturuma kaldığı yerden devam eder |
| `/inspect` | Son HTML incelemesini açar |
| `/exit` | Çıkış |

Model ve effort değişikliği yalnız bu sohbet içindir; kalıcı seçim için `harness model`."""

SESSION_LIMIT = 10


def new_session_log(folder: Path) -> SessionLogger:
    return SessionLogger(folder / f"{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl")


@dataclass(frozen=True)
class CommandReply:
    """Komutun sonucu; ön yüz metni gösterir, bayraklara göre ekranı günceller."""

    text: str = ""
    exit: bool = False
    inspect: bool = False
    # Konuşma değişti (yeni ya da devam edilen oturum): ekran temizlenip transcript çizilir.
    reset: bool = False
    transcript: tuple[tuple[str, str], ...] = ()  # (rol, metin)


def is_command(text: str) -> bool:
    return text.startswith("/")


class ChatSession:
    """Sohbetin o anki modeli, effort'u, ajanı ve kayıt dosyası.

    Model ya da effort değişince ajan aynı geçmişle yeniden kurulur; her tur
    TaskStarted'a kendi modelini yazdığı için kayıt hangi turun hangi modelle
    koştuğunu gösterir.
    """

    def __init__(
        self,
        *,
        root: Path,
        anatomy: Anatomy,
        system_prompt: str,
        model: str,
        effort: str | None,
        make_llm: LLMFactory,
        load_models: ModelLoader,
        sessions_dir: Path,
    ) -> None:
        self.root = root
        self.anatomy = anatomy
        self.system_prompt = system_prompt
        self.model = model
        self.effort = effort
        self._make_llm = make_llm
        self._load_models = load_models
        self._models: list[ModelInfo] | None = None
        self.sessions_dir = sessions_dir
        self.log = new_session_log(sessions_dir)
        self.agent = self._build_agent([])

    @property
    def label(self) -> str:
        return f"{self.model} · effort: {self.effort or 'varsayılan'}"

    def _build_agent(self, history: list[Message]) -> Agent:
        return Agent(
            llm=self._make_llm(self.model, self.effort),
            tools=self.anatomy.tool_registry(self.root),
            system_prompt=self.system_prompt,
            model_label=self.model,
            effort=self.effort,
            anatomy=self.anatomy,
            root=self.root,
            history=history,
        )

    def _rebuild(self) -> None:
        self.agent = self._build_agent(self.agent.history[1:])

    async def run(self, text: str) -> CommandReply:
        name, _, argument = text.strip().partition(" ")
        argument = argument.strip()
        match name.lower():
            case "/help":
                return CommandReply(HELP)
            case "/exit":
                return CommandReply(exit=True)
            case "/inspect":
                return CommandReply(inspect=True)
            case "/model":
                return await self._model(argument)
            case "/effort":
                return await self._effort(argument)
            case "/new":
                self.log = new_session_log(self.sessions_dir)
                self.agent = self._build_agent([])
                return CommandReply(f"Yeni oturum · {self.label}", reset=True)
            case "/sessions":
                return CommandReply(self._sessions_table())
            case "/resume":
                return self._resume(argument)
            case _:
                return CommandReply(f"Bilinmeyen komut: `{name}`. Liste için `/help`.")

    async def _model_list(self) -> list[ModelInfo]:
        if self._models is None:
            self._models = await self._load_models()
        return self._models

    async def _model(self, argument: str) -> CommandReply:
        try:
            models = await self._model_list()
        except Exception as exc:  # ağ, giriş ya da demo: komut sohbeti düşürmemeli
            return CommandReply(f"Model listesi alınamadı: {exc}")
        if not argument:
            rows = [
                f"| {'●' if m.slug == self.model else ''} {i} | {m.display_name} | `{m.slug}` |"
                for i, m in enumerate(models, start=1)
            ]
            return CommandReply(
                f"Şu an: **{self.label}**\n\n| No | Model | Ad |\n|---|---|---|\n"
                + "\n".join(rows)
                + "\n\nSeçmek için `/model <numara|ad>`."
            )
        picked = _find(models, argument, lambda m: (m.slug, m.display_name))
        if picked is None:
            return CommandReply(f"`{argument}` adlı model yok. Liste için `/model`.")
        if picked.slug != self.model:
            # Yeni modelde eski effort olmayabilir: modelin kendi varsayılanına dönülür.
            self.model, self.effort = picked.slug, None
            self._rebuild()
        return CommandReply(f"Model: **{self.label}** · geçmiş korundu.")

    async def _effort(self, argument: str) -> CommandReply:
        try:
            models = await self._model_list()
        except Exception as exc:
            return CommandReply(f"Model listesi alınamadı: {exc}")
        info = next((m for m in models if m.slug == self.model), None)
        if info is None or not info.efforts:
            return CommandReply(f"`{self.model}` için effort seviyeleri bilinmiyor.")
        if not argument:
            rows = [
                f"| {'●' if e.name == self.effort else ''} {i} | {e.name}"
                f"{' (varsayılan)' if e.name == info.default_effort else ''} | {e.description} |"
                for i, e in enumerate(info.efforts, start=1)
            ]
            return CommandReply(
                f"Şu an: **{self.label}**\n\n| No | Effort | Açıklama |\n|---|---|---|\n"
                + "\n".join(rows)
                + "\n\nSeçmek için `/effort <numara|ad>`."
            )
        picked = _find(info.efforts, argument, lambda e: (e.name,))
        if picked is None:
            return CommandReply(f"`{argument}` adlı effort yok. Liste için `/effort`.")
        if picked.name != self.effort:
            self.effort = picked.name
            self._rebuild()
        return CommandReply(f"Effort: **{self.label}** · geçmiş korundu.")

    def _recent_sessions(self) -> list[SessionSummary]:
        if not self.sessions_dir.is_dir():
            return []
        return list_sessions(self.sessions_dir)[:SESSION_LIMIT]

    def _sessions_table(self) -> str:
        sessions = self._recent_sessions()
        if not sessions:
            return "Bu projede kayıtlı oturum yok."
        rows: list[str] = []
        for i, s in enumerate(sessions, start=1):
            mark = " (bu oturum)" if s.path == self.log.path else ""
            task = s.first_task.replace("\n", " ").replace("|", "\\|")
            task = task if len(task) <= 60 else task[:59] + "…"
            rows.append(f"| {i} | {s.updated:%d.%m %H:%M}{mark} | {s.turns} | {task} |")
        return (
            "| No | Son kullanım | Tur | İlk görev |\n|---|---|---|---|\n"
            + "\n".join(rows)
            + "\n\nDevam etmek için `/resume <numara>`."
        )

    def _resume(self, argument: str) -> CommandReply:
        sessions = self._recent_sessions()
        if not argument:
            return CommandReply(self._sessions_table())
        if not argument.isdigit() or not 1 <= int(argument) <= len(sessions):
            return CommandReply(f"`{argument}` numaralı oturum yok. Liste için `/sessions`.")
        path = sessions[int(argument) - 1].path
        try:
            events = read_session(path)
        except (OSError, ValueError) as exc:
            return CommandReply(f"Oturum okunamadı: {exc}")

        self.log = SessionLogger(path)
        self.agent = self._build_agent(history_from_events(events))
        transcript: list[tuple[str, str]] = []
        anatomies: set[str] = set()
        for event in events:
            if isinstance(event, TaskStarted):
                transcript.append(("Sen", event.task))
                if event.anatomy is not None:
                    anatomies.add(event.anatomy.name)
            elif isinstance(event, ModelReplied) and not event.tool_calls and event.text:
                transcript.append(("Ajan", event.text))
        text = f"Oturuma devam ediliyor: `{path.name}` · {self.label}"
        if anatomies - {self.anatomy.name}:
            text += (
                f"\n\nDikkat: bu oturum `{', '.join(sorted(anatomies))}` anatomisiyle koşmuştu;"
                f" şimdi `{self.anatomy.name}` açık."
            )
        return CommandReply(text, reset=True, transcript=tuple(transcript))


def _find[T](items: list[T], argument: str, names: Callable[[T], tuple[str, ...]]) -> T | None:
    """Numarayla (1'den) ya da adla, büyük/küçük harf ayırmadan seçer."""
    if argument.isdigit():
        index = int(argument) - 1
        return items[index] if 0 <= index < len(items) else None
    wanted = argument.casefold()
    return next((i for i in items if wanted in (n.casefold() for n in names(i))), None)
