"""Sohbet içi /komutlar: sağlayıcı, model, effort, anatomi ve oturum.

Tam ekran (tui.py) ve sade sohbet (app.py) aynı ChatSession'ı kullanır; komutun
ne yaptığı burada, nasıl gösterildiği ön yüzdedir. Cevaplar Markdown metnidir.
Komutlar yalnız bu sohbeti değiştirir; kalıcı seçim `harness provider/model/effort`.
"""

import secrets
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from harness.core.agent import Agent
from harness.core.anatomy import PRESETS, Anatomy
from harness.core.events import (
    Event,
    ModelReplied,
    TaskStarted,
    ToolFinished,
)
from harness.core.permissions import Decision, PermissionManager
from harness.core.session_log import (
    SessionLogger,
    SessionSummary,
    history_from_events,
    list_sessions,
    read_session,
)
from harness.llm.base import LLMClient, LLMError, ModelInfo
from harness.llm.providers import PROVIDERS, Provider, readiness
from harness.messages import Message
from harness.tools.base import ToolRegistry

LLMFactory = Callable[[Provider, str, str | None], LLMClient]
ModelLoader = Callable[[Provider], Awaitable[list[ModelInfo]]]
PromptLoader = Callable[[Anatomy], str]

HELP = """\
| Komut | Ne yapar |
|---|---|
| `/status` | Sağlayıcı, model, effort, anatomi, proje ve kayıt |
| `/provider` | Sağlayıcıları listeler; `/provider <numara\\|ad>` değiştirir |
| `/model` | Modelleri listeler; `/model <numara\\|ad>` değiştirir |
| `/effort` | Düşünme seviyeleri; `/effort <numara\\|ad\\|varsayılan>` değiştirir |
| `/anatomy` | Açık katmanlar; `/anatomy <ad>` değiştirir (raw, files, bash) |
| `/tools` | Açık araçlar ve izin kuralları |
| `/usage` | Bu oturumun token, süre ve araç toplamları (`/cost`) |
| `/new` | Yeni oturum: geçmiş sıfırlanır, yeni kayıt (`/clear`) |
| `/sessions` | Bu projenin son oturumları |
| `/resume <numara>` | Listeden bir oturuma kaldığı yerden devam eder |
| `/inspect` | Son HTML incelemesini açar |
| `/exit` | Çıkış (`/quit`) |

Değişiklikler yalnız bu sohbet içindir; kalıcı seçim için `harness provider`,
`harness model`, `harness effort`, `harness anatomy`."""

ALIASES = {"/clear": "/new", "/quit": "/exit", "/cost": "/usage", "/q": "/exit"}
DEFAULT_EFFORT_WORDS = {"varsayılan", "default", "none", "yok"}
SESSION_LIMIT = 10


def new_session_log(folder: Path) -> SessionLogger:
    # Windows'ta saat kaba ilerler; arka arkaya iki oturum aynı ana düşebilir. Rastgele ek,
    # /new'in eski oturumun dosyasına yazmasını önler.
    return SessionLogger(folder / f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(4)}.jsonl")


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
    """Sohbetin o anki sağlayıcısı, modeli, effort'u, anatomisi, ajanı ve kayıt dosyası.

    Sağlayıcı, model, effort ya da anatomi değişince ajan aynı geçmişle yeniden
    kurulur; her tur TaskStarted'a kendi sağlayıcı/modelini yazdığı için kayıt
    hangi turun neyle koştuğunu gösterir. "Oturum boyu" izinler model ve effort
    değişiminde korunur; /new, /resume ve anatomi değişimi (araçlar değişir)
    izinleri sıfırlar.
    """

    def __init__(
        self,
        *,
        root: Path,
        anatomy: Anatomy,
        provider: Provider,
        model: str,
        effort: str | None,
        make_llm: LLMFactory,
        load_models: ModelLoader,
        load_prompt: PromptLoader,
        sessions_dir: Path,
        remembered_models: dict[str, str] | None = None,
    ) -> None:
        self.root = root
        self.anatomy: Anatomy = anatomy
        self.provider: Provider = provider
        self.model: str = model
        self.effort: str | None = effort
        self._make_llm = make_llm
        self._load_models = load_models
        self._load_prompt = load_prompt
        self._models: dict[str, list[ModelInfo]] = {}
        self._remembered = dict(remembered_models or {})
        self.sessions_dir = sessions_dir
        self.log = new_session_log(sessions_dir)
        self.tools: ToolRegistry = anatomy.tool_registry(root)
        self.permissions = PermissionManager(root, self.tools)
        self.agent = self._build_agent([])

    @property
    def label(self) -> str:
        return f"{self.model} · effort: {self.effort or 'varsayılan'} · {self.provider.name}"

    @property
    def system_prompt(self) -> str:
        return self._load_prompt(self.anatomy)

    def _build_agent(self, history: list[Message]) -> Agent:
        return Agent(
            llm=self._make_llm(self.provider, self.model, self.effort),
            tools=self.tools,
            system_prompt=self.system_prompt,
            permissions=self.permissions,
            model_label=self.model,
            provider=self.provider.name,
            effort=self.effort,
            anatomy=self.anatomy,
            root=self.root,
            history=history,
        )

    def _rebuild(self) -> None:
        self.agent = self._build_agent(self.agent.history[1:])

    async def run(self, text: str) -> CommandReply:
        name, _, argument = text.strip().partition(" ")
        name = ALIASES.get(name.lower(), name.lower())
        argument = argument.strip()
        match name:
            case "/help":
                return CommandReply(HELP)
            case "/exit":
                return CommandReply(exit=True)
            case "/inspect":
                return CommandReply(inspect=True)
            case "/status":
                return CommandReply(self._status())
            case "/provider":
                return self._provider(argument)
            case "/model":
                return await self._model(argument)
            case "/effort":
                return await self._effort(argument)
            case "/anatomy":
                return self._anatomy(argument)
            case "/tools":
                return CommandReply(self._tools_table())
            case "/usage":
                return CommandReply(self._usage())
            case "/new":
                self.log = new_session_log(self.sessions_dir)
                self.permissions = PermissionManager(self.root, self.tools)
                self.agent = self._build_agent([])
                return CommandReply(f"Yeni oturum · {self.label}", reset=True)
            case "/sessions":
                return CommandReply(self._sessions_table())
            case "/resume":
                return self._resume(argument)
            case _:
                return CommandReply(f"Bilinmeyen komut: `{name}`. Liste için `/help`.")

    # ------------------------------------------------------------------ durum

    def _status(self) -> str:
        granted = [n for n in self._tool_names() if self.permissions.policy(n) is Decision.ALLOW]
        rows = [
            ("Sağlayıcı", f"{self.provider.name} — {self.provider.label}"),
            ("Model", f"`{self.model}`"),
            ("Effort", self.effort or "varsayılan"),
            ("Anatomi", f"{self.anatomy.name} ({len(self._tool_names())} araç)"),
            ("Sormadan çalışan", ", ".join(f"`{n}`" for n in granted) or "—"),
            ("Proje", f"`{self.root}`"),
            ("Kayıt", f"`{self.log.path}`"),
        ]
        return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows)

    def _tool_names(self) -> list[str]:
        return [spec.name for spec in self.tools.specs()]

    def _tools_table(self) -> str:
        specs = self.tools.specs()
        if not specs:
            return f"`{self.anatomy.name}` anatomisinde araç yok: model yalnız yazar" + (
                ", harness cevaptaki adlı kod bloklarını dosyaya kaydeder."
                if self.anatomy.save_code_blocks
                else "."
            )
        rule = {
            Decision.ALLOW: "sormadan",
            Decision.ASK: "sorar",
            Decision.DENY: "kapalı",
        }
        rows = [
            f"| `{s.name}` | {s.description.split('. ')[0].rstrip('.')} "
            f"| {rule[self.permissions.policy(s.name)]} |"
            for s in specs
        ]
        return "| Araç | Ne yapar | İzin |\n|---|---|---|\n" + "\n".join(rows)

    def _usage(self) -> str:
        try:
            events = read_session(self.log.path) if self.log.path.exists() else []
        except (OSError, ValueError) as exc:
            return f"Kayıt okunamadı: {exc}"
        return usage_text(events)

    # --------------------------------------------------------------- sağlayıcı

    def _provider(self, argument: str) -> CommandReply:
        providers = list(PROVIDERS.values())
        if not argument:
            rows: list[str] = []
            for i, p in enumerate(providers, start=1):
                ready, detail = readiness(p)
                mark = "●" if p.name == self.provider.name else ""
                rows.append(
                    f"| {mark} {i} | `{p.name}` | {p.label} | {'✓' if ready else '✗'} {detail} |"
                )
            return CommandReply(
                f"Şu an: **{self.label}**\n\n| No | Ad | Sağlayıcı | Durum |\n|---|---|---|---|\n"
                + "\n".join(rows)
                + "\n\nSeçmek için `/provider <numara|ad>`."
            )
        picked = _find(providers, argument, lambda p: (p.name,))
        if picked is None:
            return CommandReply(f"`{argument}` adlı sağlayıcı yok. Liste için `/provider`.")
        if picked.name == self.provider.name:
            return CommandReply(f"Zaten **{self.label}**.")
        model = self._remembered.get(picked.name, picked.default_model)
        try:
            # Önce kurmayı dene: anahtar yoksa mevcut sağlayıcı bozulmasın.
            self._make_llm(picked, model, None)
        except LLMError as exc:
            return CommandReply(f"`{picked.name}` kullanılamıyor: {exc}")
        self._remembered[self.provider.name] = self.model
        self.provider, self.model, self.effort = picked, model, None
        self._rebuild()
        return CommandReply(f"Sağlayıcı: **{self.label}** · geçmiş korundu.")

    async def _model_list(self) -> list[ModelInfo]:
        name = self.provider.name
        if name not in self._models:
            self._models[name] = await self._load_models(self.provider)
        return self._models[name]

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
                + "\n\nSeçmek için `/model <numara|ad>`; düşünme seviyesi ayrıca `/effort`."
            )
        picked = _find(models, argument, lambda m: (m.slug, m.display_name))
        if picked is None:
            return CommandReply(f"`{argument}` adlı model yok. Liste için `/model`.")
        if picked.slug != self.model:
            # Yeni modelde aynı effort varsa korunur; yoksa modelin varsayılanına dönülür.
            keep = self.effort in {e.name for e in picked.efforts}
            self.model, self.effort = picked.slug, self.effort if keep else None
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
                + "\n\nSeçmek için `/effort <numara|ad>`; modelin kendi seçimi için "
                "`/effort varsayılan`."
            )
        if argument.casefold() in DEFAULT_EFFORT_WORDS:
            if self.effort is not None:
                self.effort = None
                self._rebuild()
            return CommandReply(f"Effort: **{self.label}**.")
        picked = _find(info.efforts, argument, lambda e: (e.name,))
        if picked is None:
            return CommandReply(f"`{argument}` adlı effort yok. Liste için `/effort`.")
        if picked.name != self.effort:
            self.effort = picked.name
            self._rebuild()
        return CommandReply(f"Effort: **{self.label}** · geçmiş korundu.")

    def _anatomy(self, argument: str) -> CommandReply:
        if not argument:
            rows = [
                f"| {'●' if a.name == self.anatomy.name else ''} `{a.name}` | "
                f"{', '.join(f'`{t}`' for t in a.tools) or 'araç yok'} |"
                for a in PRESETS.values()
            ]
            return CommandReply(
                f"Şu an: **{self.anatomy.name}**\n\n| Anatomi | Araçlar |\n|---|---|\n"
                + "\n".join(rows)
                + "\n\nDeğiştirmek için `/anatomy <ad>`."
            )
        picked = PRESETS.get(argument.casefold())
        if picked is None:
            return CommandReply(f"`{argument}` adlı anatomi yok. Liste için `/anatomy`.")
        if picked.name != self.anatomy.name:
            self.anatomy = picked
            self.tools = picked.tool_registry(self.root)
            # Araç listesi değişti: eski izinler yeni araçlara taşınmaz.
            self.permissions = PermissionManager(self.root, self.tools)
            self._rebuild()
        return CommandReply(
            f"Anatomi: **{self.anatomy.name}** · {len(self._tool_names())} araç · geçmiş korundu."
        )

    # ----------------------------------------------------------------- oturum

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

    def resume_latest(self) -> CommandReply:
        """`harness -c`: bu projenin en son oturumuna devam."""
        if not self._recent_sessions():
            return CommandReply("Bu projede devam edilecek oturum yok; yeni oturum açıldı.")
        return self._resume("1")

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
        self.permissions = PermissionManager(self.root, self.tools)
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


def usage_text(events: list[Event]) -> str:
    """Oturumun toplamları; sağlayıcı sayım vermediyse o alan — kalır, sıfır yazılmaz."""
    turns = sum(isinstance(e, TaskStarted) for e in events)
    if not turns:
        return "Bu oturumda henüz tur yok."
    replies = [e for e in events if isinstance(e, ModelReplied)]
    tools = [e for e in events if isinstance(e, ToolFinished)]

    def total(field: str) -> str:
        values = [getattr(e.usage, field) if e.usage else None for e in replies]
        known = [v for v in values if v is not None]
        if not known:
            return "—"
        text = f"{sum(known):,}".replace(",", ".")
        return text if len(known) == len(values) else f"en az {text}"

    seconds = sum(e.duration_seconds or 0 for e in replies)
    by_tool = Counter(e.call.name for e in tools)
    errors = sum(e.result.is_error for e in tools)
    rows = [
        ("Tur / model çağrısı", f"{turns} / {len(replies)}"),
        ("Girdi tokenı", total("input_tokens")),
        ("  önbellekten", total("cached_tokens")),
        ("Çıktı tokenı", total("output_tokens")),
        ("  düşünme", total("reasoning_tokens")),
        ("Model süresi", f"{seconds:.1f} sn"),
        (
            "Araç çağrısı",
            f"{len(tools)}"
            + (f" ({', '.join(f'{n} ×{c}' for n, c in by_tool.most_common())})" if tools else "")
            + (f" · {errors} hata" if errors else ""),
        ),
    ]
    return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows)


def _find[T](items: list[T], argument: str, names: Callable[[T], tuple[str, ...]]) -> T | None:
    """Numarayla (1'den) ya da adla, büyük/küçük harf ayırmadan seçer."""
    if argument.isdigit():
        index = int(argument) - 1
        return items[index] if 0 <= index < len(items) else None
    wanted = argument.casefold()
    return next((i for i in items if wanted in (n.casefold() for n in names(i))), None)
