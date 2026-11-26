"""Sohbet içi /komutlar: model, effort, bağlantı, anatomi ve oturum.

Tam ekran (tui.py) ve sade sohbet (app.py) aynı ChatSession'ı kullanır; komutun
ne yaptığı burada, nasıl gösterildiği ön yüzdedir. Cevaplar Markdown metnidir.

Seçim isteyen komutlar (`/model`, `/effort`, `/connect`, `/anatomy`, `/resume`)
argümansız yazılınca bir `Picker` döner: ön yüz listeyi gösterir (tam ekranda
aranabilir pencere, sade akışta numaralı tablo) ve seçilen satırı `choose` ile
geri verir. Bir seçim bir sonrakini açabilir: model seçilince effort sorulur,
sağlayıcı bağlanınca o sağlayıcının modelleri gelir. Model, effort ve anatomi
seçimleri kalıcıdır: bir sonraki `harness` aynı seçimlerle açılır.
"""

import asyncio
import os
import secrets
import subprocess
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from harness.cli.inspector import reread_text, work_rows
from harness.cli.themes import THEME_FILE, available, palette_for
from harness.cli.watch import WatchServer
from harness.core.agent import Agent
from harness.core.anatomy import LAYERS, PRESETS, Anatomy, compose, get_preset
from harness.core.checkpoints import Checkpoints, CheckpointUnavailable
from harness.core.events import (
    Event,
    HarnessIntervened,
    ModelReplied,
    TaskStarted,
    ToolFinished,
)
from harness.core.metrics import read_stats, work_stats
from harness.core.permissions import Decision, PermissionManager
from harness.core.project_context import ctx_command
from harness.core.pruning import PRUNE_KEEP_STEPS, estimate_tokens, prune
from harness.core.session_log import (
    SessionLogger,
    SessionSummary,
    history_from_events,
    list_sessions,
    read_session,
)
from harness.llm.base import LLMClient, LLMError, ModelInfo
from harness.llm.chatgpt_auth import DEFAULT_PATH as CHATGPT_CREDENTIALS
from harness.llm.credentials import key_source, load_keys, remove_key, save_key
from harness.llm.providers import PROVIDERS, Provider, readiness
from harness.messages import Message
from harness.tools.base import ToolRegistry, short

LLMFactory = Callable[[Provider, str, str | None], LLMClient]
ModelLoader = Callable[[Provider], Awaitable[list[ModelInfo]]]
PromptLoader = Callable[[Anatomy], str]
# Tarayıcı girişi; adres yalnız tarayıcı açılamazsa geri çağrıyla verilir. Dönen: e-posta.
LoginRunner = Callable[[Callable[[str], None]], Awaitable[str | None]]


@dataclass(frozen=True)
class Command:
    """Bir /komut: yardım tablosu, komut paleti ve yazarken çıkan öneriler buradan beslenir."""

    name: str
    summary: str
    group: str
    usage: str = ""  # argüman örneği: "<ad>", "[arama]"


COMMANDS: tuple[Command, ...] = (
    Command("/model", "Model seçer; bağlı bütün sağlayıcıların modelleri tek listede", "Model"),
    Command("/effort", "Düşünme seviyesi; `/effort varsayılan` modelin seçimine döner", "Model"),
    Command("/connect", "Sağlayıcı bağlar: ChatGPT girişi ya da API anahtarı", "Model"),
    Command("/disconnect", "Kayıtlı anahtarı ya da ChatGPT girişini siler", "Model", "<ad>"),
    Command("/anatomy", "Açık katmanlar: paketler (raw, files, bash, full) ve ekler", "Harness"),
    Command("/memory", "Hafıza katmanını (agent-context) açar ya da kapatır", "Harness"),
    Command("/tools", "Açık araçlar ve izin kuralları", "Harness"),
    Command("/sessions", "Geçmiş oturumlar: devam et, panelde aç, dışa aktar", "Oturum"),
    Command(
        "/resume", "Bir oturuma kaldığı yerden devam eder; adla aranabilir", "Oturum", "[arama]"
    ),
    Command("/new", "Yeni oturum: geçmiş sıfırlanır, yeni kayıt (`/clear`)", "Oturum"),
    Command("/undo", "Dosya değiştiren son turu geri alır; git geçmişine dokunmaz", "Oturum"),
    Command("/compact", "Bağlamı toplar: son 5 adım dışındaki araç sonuçları nota döner", "Oturum"),
    Command("/export", "Bu oturumu Markdown olarak dışa aktarır", "Oturum", "[dosya]"),
    Command("/copy", "Ajanın son cevabını panoya kopyalar", "Oturum"),
    Command("/status", "Model, effort, anatomi, proje ve kayıt", "Bilgi"),
    Command("/usage", "Bu oturumun token, süre ve araç toplamları (`/cost`)", "Bilgi"),
    Command("/diff", "Çalışma dizinindeki değişiklikler (git diff)", "Bilgi"),
    Command("/watch", "Canlı paneli tarayıcıda açar: sohbet burada, akış orada", "İzleme"),
    Command("/inspect", "Son HTML incelemesini açar", "İzleme"),
    Command("/theme", "Renk teması: koyu, açık, yüksek kontrast, renk körü, kişisel", "Görünüm"),
    Command("/panel", "Sağ panel: özet, akış ya da kapalı (Ctrl+B sıradakine geçer)", "Görünüm"),
    Command("/thinking", "Düşünce görünümü: katlı, açık ya da gizli", "Görünüm"),
    Command("/help", "Bu liste", "Görünüm"),
    Command("/exit", "Çıkış (`/quit`)", "Görünüm"),
)

HELP = (
    "| Komut | Ne yapar |\n|---|---|\n"
    + "\n".join(f"| `{c.name}{' ' + c.usage if c.usage else ''}` | {c.summary} |" for c in COMMANDS)
    + "\n\nArgümansız yazılan seçim komutları liste açar. Doğrudan da seçilebilir: "
    "`/model opencode-go/glm-5.3`, `/effort high`, `/anatomy bash`. Seçimler kalıcıdır. "
    "Tam ekranda yalnız `/` yazıp Enter'a basmak aranabilir komut listesini açar; "
    "`/` ile başlayınca öneriler durum satırında çıkar, Tab tamamlar."
)


def matching_commands(prefix: str) -> list[Command]:
    """Yazılmakta olan komutla başlayanlar (öneri satırı ve Tab tamamlama)."""
    wanted = prefix.casefold()
    known = [c for c in COMMANDS if c.name.startswith(wanted)]
    aliases = [
        next(c for c in COMMANDS if c.name == target)
        for alias, target in ALIASES.items()
        if alias.startswith(wanted) and target not in {c.name for c in known}
    ]
    return known + [a for i, a in enumerate(aliases) if a not in aliases[:i]]


# Cevabı tam ekranda sohbete değil, kapanan bir pencereye yazan komutlar.
PANEL_COMMANDS = {
    "/help": "Komutlar",
    "/status": "Durum",
    "/tools": "Araçlar",
    "/usage": "Kullanım",
    "/diff": "Çalışma dizinindeki değişiklikler",
    # Adres/yol veren komutlar: 8 sn'lik bildirimde kaybolmasın, pencerede kalsın.
    "/watch": "Canlı panel",
    "/export": "Dışa aktarma",
}

ALIASES = {
    "/clear": "/new",
    "/quit": "/exit",
    "/q": "/exit",
    "/cost": "/usage",
    "/models": "/model",
    "/provider": "/model",
    "/providers": "/connect",
    "/login": "/connect",
    "/logout": "/disconnect",
    "/flow": "/panel",
    "/thoughts": "/thinking",
}
DEFAULT_EFFORT_WORDS = {"varsayılan", "default", "none", "yok"}
SESSION_LIMIT = 100
# Sohbette bir mesajın en çok kaç model adımı süreceği. Keşif görevleri 20'yi aşıyor
# (2026-10-29 gerçek koşu: "repoyu incele" 20 adımda sonuca varmadan durdu).
# 2026-11-18: 2. ve 3. turun gerçek görevleri 40–100+ çağrı sürdü (T4 bir koşuda 103);
# 50 sınırı işi yarıda kesiyordu. Döngüye giren model için `+loop_guard` katmanı var.
CHAT_STEP_LIMIT = 150
STEP_LIMIT_HINT = "Adım sınırı doldu; geçmiş korundu: kaldığı yerden sürmesi için “devam” yaz."

PickerKind = Literal[
    "model",
    "effort",
    "connect",
    "anatomy",
    "resume",
    "theme",
    "panel",
    "thinking",
    "sessions",
    "session",
    "command",
]
# Sağ panel ve düşünce görünümleri (tam ekran). Ad ASCII'dir (ayar dosyası, komut argümanı);
# Türkçe harfli yazım da kabul edilir. Sözlük sırası Ctrl+B'nin dolaşma sırasıdır.
PANEL_MODES = {
    "ozet": "Özet: plan, dokunulan dosyalar ve tur ölçüleri; araçlar sohbette tek satır",
    "akis": "Akış: her olay sağda ayrı satır (eğitim ve izleme için)",
    "kapali": "Panel yok; araçlar sohbette tek satır",
}
DEFAULT_PANEL = "ozet"
THINKING_MODES = {
    "katli": "Tek satır başlık; tıklayınca açılır",
    "acik": "Düşünce akarken tamamen görünür",
    "gizli": "Gösterilmez (kayıtta ve incelemede kalır)",
}
DEFAULT_THINKING = "katli"
_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def view_name(value: str | None, modes: dict[str, str], default: str) -> str:
    """Kullanıcının yazdığı görünüm adı (ör. "Akış") -> kayıtlı ad ("akis"); bilinmeyen: default."""
    key = (value or "").strip().translate(_ASCII).lower()
    return key if key in modes else default


# Cevabın nerede gösterileceği (tam ekranda): sohbet, kapanan pencere ya da kısa bildirim.
# Sade akış hepsini aynı biçimde yazar.
ReplyView = Literal["chat", "panel", "note"]


def new_session_log(folder: Path) -> SessionLogger:
    # Windows'ta saat kaba ilerler; arka arkaya iki oturum aynı ana düşebilir. Rastgele ek,
    # /new'in eski oturumun dosyasına yazmasını önler.
    return SessionLogger(folder / f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(4)}.jsonl")


@dataclass(frozen=True)
class Choice:
    """Listede bir satır. `kind` verilirse satır başka bir seçim yapar (ör. bağlan)."""

    value: str
    label: str
    detail: str = ""
    group: str = ""
    current: bool = False
    enabled: bool = True
    kind: PickerKind | None = None


@dataclass(frozen=True)
class Picker:
    """Ön yüzün göstereceği seçim listesi; seçilen satır `ChatSession.choose`'a gider."""

    kind: PickerKind
    title: str
    choices: tuple[Choice, ...]


@dataclass(frozen=True)
class KeyRequest:
    """Ön yüz gizli bir alan açar; girilen anahtar `ChatSession.save_key`'e gider."""

    provider: str
    prompt: str


@dataclass(frozen=True)
class CommandReply:
    """Komutun sonucu; ön yüz metni gösterir, bayraklara göre ekranı günceller."""

    text: str = ""
    exit: bool = False
    inspect: bool = False
    # Konuşma değişti (yeni ya da devam edilen oturum): ekran temizlenip transcript çizilir.
    reset: bool = False
    transcript: tuple[tuple[str, str], ...] = ()  # (rol, metin)
    picker: Picker | None = None  # sıradaki seçim
    key: KeyRequest | None = None  # sıradaki adım: gizli anahtar girişi
    login: bool = False  # sıradaki adım: tarayıcıyla ChatGPT girişi (`ChatSession.login`)
    theme: str | None = None  # renk teması değişti: ön yüz yeni temayı uygular
    display: bool = False  # panel ya da düşünce görünümü değişti: ön yüz yeniden düzenler
    # Sohbeti kirletmesin: bilgi komutları pencerede, onaylar kısa bildirim olarak çıkar.
    view: ReplyView = "note"
    title: str = ""  # pencere başlığı
    clipboard: str | None = None  # ön yüz bu metni panoya kopyalar


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
        save_choice: Callable[["ChatSession"], None] | None = None,
        login: LoginRunner | None = None,
        open_url: Callable[[str], None] | None = None,
        theme: str | None = None,
        panel: str | None = None,
        thinking: str | None = None,
        max_steps: int = CHAT_STEP_LIMIT,
        checkpoints: bool = True,
    ) -> None:
        """checkpoints: her turdan önce görüntü alınır (`/undo`). Tek görevlik koşuda (-p)
        geri alacak tur olmadığı için kapalıdır; deney süresine de görüntü maliyeti eklenmez."""
        self.root = root
        self._use_checkpoints = checkpoints
        self.max_steps = max_steps  # tur başı adım sınırı (döngüye giren modele karşı bütçe)
        self.anatomy: Anatomy = anatomy
        self.provider: Provider = provider
        self.model: str = model
        self.effort: str | None = effort
        self._make_llm = make_llm
        self._load_models = load_models
        self._load_prompt = load_prompt
        self._save_choice = save_choice
        self._login = login
        self._open_url = open_url
        self.theme: str = palette_for(theme).name
        self.panel: str = view_name(panel, PANEL_MODES, DEFAULT_PANEL)
        self.thinking: str = view_name(thinking, THINKING_MODES, DEFAULT_THINKING)
        self._watch: WatchServer | None = None
        self._models: dict[str, list[ModelInfo]] = {}
        self.sessions_dir = sessions_dir
        self.log = new_session_log(sessions_dir)
        self.tools: ToolRegistry = anatomy.tool_registry(root)
        # `--allow` ile açılışta verilen izinler ("all" dahil): her yeni izin yöneticisine
        # (yeni oturum, devam, anatomi değişimi) yeniden uygulanır. Sohbet içinde verilen
        # "hep izin ver" ise yalnız o izin yöneticisi yaşadıkça geçerlidir.
        self.allowed: tuple[str, ...] = ()
        self.permissions = self._new_permissions()
        # Her turdan önce çalışma dizininin görüntüsü (core/checkpoints.py); `/undo` geri alır.
        self.checkpoints = self._new_checkpoints()
        self.agent = self._build_agent([])

    def _new_checkpoints(self) -> Checkpoints | None:
        return Checkpoints(self.root) if self._use_checkpoints else None

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
            max_steps=self.max_steps,
            # Kimlik oturum kaydına bağlı: model/katman değişince aynı kalır, /new ile yenilenir,
            # /resume eski oturumun kimliğine döner (ctx o notları zaten verdiğini bilir).
            memory_session_id=self.log.path.stem,
            checkpoints=self.checkpoints,
        )

    def _rebuild(self) -> None:
        notes = self.agent.notes
        self.agent = self._build_agent(self.agent.history[1:])
        self.agent.notes = notes  # model/katman değişse de bekleyen not modele ulaşsın

    def allow(self, names: list[str]) -> None:
        """`--allow`: araçlar bu çalıştırma boyunca sormadan çalışır (yol denetimi yine geçerli)."""
        self.allowed = (*self.allowed, *names)
        self._apply_allowed(self.permissions)

    def _new_permissions(self) -> PermissionManager:
        permissions = PermissionManager(self.root, self.tools)
        self._apply_allowed(permissions)
        return permissions

    def _apply_allowed(self, permissions: PermissionManager) -> None:
        names = self._tool_names()
        for name in self.allowed:
            for granted in names if name == "all" else [name]:
                if granted in names:
                    permissions.grant(granted)

    def _save(self) -> None:
        if self._save_choice is not None:
            self._save_choice(self)

    async def run(self, text: str) -> CommandReply:
        name, _, argument = text.strip().partition(" ")
        name = ALIASES.get(name.lower(), name.lower())
        reply = await self._dispatch(name, argument.strip())
        if name in PANEL_COMMANDS and reply.view == "note" and reply.text and not reply.picker:
            reply = replace(reply, view="panel", title=reply.title or PANEL_COMMANDS[name])
        return reply

    async def _dispatch(self, name: str, argument: str) -> CommandReply:
        match name:
            case "/":
                return CommandReply(picker=self._command_palette())
            case "/help":
                return CommandReply(HELP)
            case "/sessions":
                return self._sessions(argument)
            case "/diff":
                return CommandReply(await self._diff())
            case "/copy":
                return self._copy()
            case "/export":
                return self._export(argument)
            case "/memory":
                return self._memory()
            case "/exit":
                return CommandReply(exit=True)
            case "/inspect":
                return CommandReply(inspect=True)
            case "/watch":
                return CommandReply(self._watch_panel(argument))
            case "/theme":
                return self._theme(argument)
            case "/panel":
                return self._panel(argument)
            case "/thinking":
                return self._thinking(argument)
            case "/status":
                return CommandReply(self._status())
            case "/model":
                return await self._model(argument)
            case "/effort":
                return await self._effort(argument)
            case "/connect":
                return await self._connect(argument)
            case "/disconnect":
                return self._disconnect(argument)
            case "/anatomy":
                return self._anatomy(argument)
            case "/tools":
                return CommandReply(self._tools_table())
            case "/usage":
                return CommandReply(self._usage())
            case "/new":
                self.log = new_session_log(self.sessions_dir)
                self.permissions = self._new_permissions()
                # Geri alma yeni oturumun turlarıyla sınırlı.
                self.checkpoints = self._new_checkpoints()
                self.agent = self._build_agent([])
                return CommandReply(f"Yeni oturum · {self.label}", reset=True)
            case "/resume":
                return self._resume(argument)
            case "/undo":
                return CommandReply(await self._undo(), view="chat")
            case "/compact":
                return CommandReply(self._compact())
            case _:
                return CommandReply(f"Bilinmeyen komut: `{name}`. Liste için `/help`.")

    async def choose(self, kind: PickerKind, value: str) -> CommandReply:
        """Listeden seçilen satır. Listeden seçilen model, effort sorusunu da açar."""
        if kind == "model":
            return await self._select_model(value, ask_effort=True)
        if kind == "command":  # komut paletinden: komutun kendisi (argümansız) çalışır
            return await self.run(value)
        if kind == "session":  # oturum gezgininde seçilen eylem: "resume 3", "watch 3"…
            return await self.run(f"/{value}")
        return await self.run(f"/{kind} {value}")

    def _command_palette(self) -> Picker:
        choices = tuple(
            Choice(c.name, c.name, c.summary.replace("`", ""), group=c.group) for c in COMMANDS
        )
        return Picker("command", "Komutlar (yazdıkça süzülür)", choices)

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

    # ------------------------------------------------------------------ model

    async def _models_of(self, provider: Provider) -> list[ModelInfo]:
        # Başarılı liste oturum boyu saklanır; hata saklanmaz (sunucu sonradan açılabilir).
        if provider.name not in self._models:
            self._models[provider.name] = await self._load_models(provider)
        return self._models[provider.name]

    async def image_support(self) -> bool | None:
        """Şu anki model görsel kabul ediyor mu; sağlayıcı bildirmiyorsa ya da liste
        alınamıyorsa None (görsel gönderilir, sağlayıcı reddederse hata kayıtta görünür)."""
        try:
            models = await self._models_of(self.provider)
        except Exception:  # liste alınamadı: bilinmiyor, göndermeyi engelleme
            return None
        found = next((m for m in models if m.slug == self.model), None)
        return found.images if found is not None else None

    def _forget_models(self, provider: Provider) -> None:
        for other in PROVIDERS.values():
            if other.api_key_env == provider.api_key_env or other.name == provider.name:
                self._models.pop(other.name, None)

    async def _model(self, argument: str) -> CommandReply:
        if not argument:
            return CommandReply(picker=await self._model_picker())
        return await self._select_model(argument, ask_effort=False)

    async def _model_picker(self, only: Provider | None = None) -> Picker:
        """Bağlı sağlayıcıların modelleri (listeler aynı anda istenir), sonra bağlı olmayanlar."""
        offered = [self.provider] + [p for p in PROVIDERS.values() if p.name != self.provider.name]
        if only is not None:
            offered = [only]
        ready = [p for p in offered if p is self.provider or p is only or readiness(p)[0]]
        waiting = [p for p in offered if p not in ready]
        results = await asyncio.gather(*(self._models_of(p) for p in ready), return_exceptions=True)
        choices: list[Choice] = []
        for provider, result in zip(ready, results, strict=True):
            group = f"{provider.name} · {provider.label}"
            if isinstance(result, BaseException):
                # Uzak sağlayıcıda sorun çoğunlukla giriş/anahtar: satır yeniden bağlamaya gider.
                choices.append(
                    Choice(
                        value=provider.name,
                        label="ulaşılamadı",
                        detail=short(str(result), 90),
                        group=group,
                        enabled=not provider.local,
                        kind="connect",
                    )
                )
                continue
            for info in result:
                current = provider.name == self.provider.name and info.slug == self.model
                notes = [info.display_name] if info.display_name != info.slug else []
                notes += [info.description] if info.description else []
                choices.append(
                    Choice(
                        value=f"{provider.name}/{info.slug}",
                        label=info.slug,
                        detail=" · ".join(notes),
                        group=group,
                        current=current,
                    )
                )
        for provider in waiting:
            choices.append(
                Choice(
                    value=provider.name,
                    label=provider.name,
                    detail=f"{provider.label} · bağlanmak için seç",
                    group="Bağlı değil",
                    kind="connect",
                )
            )
        title = f"{only.name} modelleri" if only else f"Model seç · şu an: {self.label}"
        return Picker("model", title, tuple(choices))

    async def _select_model(self, value: str, *, ask_effort: bool) -> CommandReply:
        """`sağlayıcı/model` ya da bu sağlayıcıdaki model adı.

        OpenRouter gibi sağlayıcılarda model adının kendisi `/` içerir
        (`openai/gpt-oss-120b`); ön ek bir sağlayıcı adı olsa da model orada yoksa
        adın tamamı bu sağlayıcıda aranır.
        """
        candidates: list[tuple[Provider, str]] = []
        head, separator, rest = value.partition("/")
        if separator and head.casefold() in PROVIDERS:
            candidates.append((PROVIDERS[head.casefold()], rest))
        candidates.append((self.provider, value))
        problem = ""
        for provider, name in candidates:
            try:
                models = await self._models_of(provider)
            except Exception as exc:  # ağ, giriş ya da demo: komut sohbeti düşürmemeli
                problem = f"`{provider.name}` model listesi alınamadı: {exc}"
                continue
            picked = _named(models, name, lambda m: (m.slug, m.display_name))
            if picked is not None:
                return self._switch(provider, picked, ask_effort=ask_effort)
        return CommandReply(problem or f"`{value}` adlı model yok. Liste için `/model`.")

    def _switch(self, provider: Provider, picked: ModelInfo, *, ask_effort: bool) -> CommandReply:
        if provider.name != self.provider.name:
            try:
                # Önce kurmayı dene: anahtar yoksa mevcut sağlayıcı bozulmasın.
                self._make_llm(provider, picked.slug, None)
            except LLMError as exc:
                return CommandReply(f"`{provider.name}` kullanılamıyor: {exc}")
        # Yeni modelde aynı effort varsa korunur; yoksa modelin varsayılanına dönülür.
        effort = self.effort if self.effort in {e.name for e in picked.efforts} else None
        if (provider.name, picked.slug, effort) != (self.provider.name, self.model, self.effort):
            self.provider, self.model, self.effort = provider, picked.slug, effort
            self._rebuild()
        self._save()
        text = f"Model: **{self.label}** · geçmiş korundu."
        if ask_effort and picked.efforts:
            return CommandReply(text, picker=self._effort_picker(picked))
        return CommandReply(text)

    async def _current_info(self) -> ModelInfo | str:
        try:
            models = await self._models_of(self.provider)
        except Exception as exc:
            return f"Model listesi alınamadı: {exc}"
        info = next((m for m in models if m.slug == self.model), None)
        if info is None or not info.efforts:
            return f"`{self.model}` için effort seviyeleri bilinmiyor."
        return info

    def _effort_picker(self, info: ModelInfo) -> Picker:
        default = f"modelin kendi seçimi ({info.default_effort})" if info.default_effort else ""
        choices = [
            Choice(
                "varsayılan",
                "varsayılan",
                default or "modelin kendi seçimi",
                current=self.effort is None,
            )
        ]
        choices += [
            Choice(
                e.name,
                e.name,
                e.description + (" · önerilen" if e.name == info.default_effort else ""),
                current=e.name == self.effort,
            )
            for e in info.efforts
        ]
        return Picker("effort", f"{info.display_name} ne kadar düşünsün?", tuple(choices))

    async def _effort(self, argument: str) -> CommandReply:
        if argument.casefold() in DEFAULT_EFFORT_WORDS:
            if self.effort is not None:
                self.effort = None
                self._rebuild()
            self._save()
            return CommandReply(f"Effort: **{self.label}**.")
        info = await self._current_info()
        if isinstance(info, str):
            return CommandReply(info)
        if not argument:
            return CommandReply(picker=self._effort_picker(info))
        picked = _named(info.efforts, argument, lambda e: (e.name,))
        if picked is None:
            return CommandReply(f"`{argument}` adlı effort yok. Liste için `/effort`.")
        if picked.name != self.effort:
            self.effort = picked.name
            self._rebuild()
        self._save()
        return CommandReply(f"Effort: **{self.label}** · geçmiş korundu.")

    # --------------------------------------------------------------- bağlantı

    def _connect_picker(self) -> Picker:
        choices: list[Choice] = []
        for provider in PROVIDERS.values():
            ready, detail = readiness(provider)
            if provider.local:
                status = detail
            elif provider.api_key_env:
                source = key_source(provider.api_key_env)
                status = {
                    "env": f"bağlı · {provider.api_key_env} ortam değişkeninden",
                    "file": "bağlı · anahtar kayıtlı",
                    None: "bağlı değil · API anahtarı",
                }[source]
            else:
                status = "bağlı · giriş yapılmış" if ready else "bağlı değil · tarayıcıyla giriş"
            choices.append(
                Choice(
                    provider.name,
                    provider.name,
                    f"{provider.label} · {status}",
                    current=provider.name == self.provider.name,
                )
            )
        return Picker("connect", "Sağlayıcı bağla", tuple(choices))

    async def _connect(self, argument: str) -> CommandReply:
        if not argument:
            return CommandReply(picker=self._connect_picker())
        provider = PROVIDERS.get(argument.casefold())
        if provider is None:
            return CommandReply(f"`{argument}` adlı sağlayıcı yok. Liste için `/connect`.")
        if provider.local:
            self._forget_models(provider)
            try:
                await self._models_of(provider)
            except Exception:
                return CommandReply(
                    f"`{provider.name}` yerel sunucusu kapalı ({readiness(provider)[1]}). "
                    "Sunucuyu başlatıp yeniden dene; anahtar gerekmez."
                )
            return CommandReply(
                f"`{provider.name}` çalışıyor; anahtar gerekmez.",
                picker=await self._model_picker(only=provider),
            )
        if provider.api == "chatgpt":
            if self._login is None:
                return CommandReply("Bu oturumda giriş yapılamaz; terminalde `harness login`.")
            return CommandReply("Tarayıcıda ChatGPT girişi açılıyor…", login=True)
        return CommandReply(
            key=KeyRequest(
                provider.name,
                f"{provider.label} · API anahtarı ({provider.api_key_env}). "
                "Ekranda görünmez, ~/.harness/auth.json'a kaydedilir.",
            )
        )

    async def save_key(self, provider_name: str, key: str) -> CommandReply:
        """Girilen anahtarı kaydeder ve o sağlayıcının modellerini seçime açar."""
        provider = PROVIDERS[provider_name]
        key = key.strip()
        env_name = provider.api_key_env
        if not key or env_name is None:
            return CommandReply("Anahtar girilmedi; hiçbir şey değişmedi.")
        if any(ch.isspace() for ch in key):
            # Panoda anahtar yerine başka metin kalmış olabilir (ör. kopyalanan terminal çıktısı).
            return CommandReply(
                "Bu bir API anahtarına benzemiyor (içinde boşluk var); kaydedilmedi. "
                f"Anahtarı yeniden kopyalayıp `/connect {provider.name}` ile dene."
            )
        previous = load_keys().get(env_name)
        save_key(env_name, key)
        self._forget_models(provider)
        if key_source(env_name) == "env":
            text = f"Anahtar kaydedildi, ama {env_name} ortam değişkeni de tanımlı ve o geçerli."
            return CommandReply(text, picker=await self._model_picker(only=provider))
        # Model listesi anahtarla istenir: sağlayıcı reddederse anahtar hiç kaydedilmemiş olur.
        try:
            await self._models_of(provider)
        except LLMError as exc:
            if exc.rejects_key:
                if previous is None:
                    remove_key(env_name)
                else:
                    save_key(env_name, previous)
                self._forget_models(provider)
                return CommandReply(
                    f"`{provider.name}` anahtarı reddetti ({short(str(exc), 120)}); "
                    "kaydedilmedi. Anahtarı kontrol edip yeniden dene."
                )
        except Exception:
            pass  # ağ sorunu anahtarın suçu değil: kayıt kalır, listede "ulaşılamadı" görünür
        text = f"`{provider.name}` bağlandı; anahtar kaydedildi."
        return CommandReply(text, picker=await self._model_picker(only=provider))

    async def login(self, on_url: Callable[[str], None]) -> CommandReply:
        """ChatGPT'ye tarayıcıyla giriş; başarılıysa ChatGPT modelleri seçime açılır."""
        if self._login is None:
            return CommandReply("Bu oturumda giriş yapılamaz; terminalde `harness login`.")
        try:
            email = await self._login(on_url)
        except Exception as exc:  # zaman aşımı, iptal, ağ: sohbet sürer
            return CommandReply(f"Giriş başarısız: {exc}")
        chatgpt = PROVIDERS["chatgpt"]
        self._forget_models(chatgpt)
        text = "ChatGPT girişi tamam" + (f" ({email})." if email else ".")
        return CommandReply(text, picker=await self._model_picker(only=chatgpt))

    def _disconnect(self, argument: str) -> CommandReply:
        provider = PROVIDERS.get(argument.casefold()) if argument else None
        if provider is None:
            return CommandReply("Hangi sağlayıcı? `/disconnect <ad>` (liste: `/connect`).")
        self._forget_models(provider)
        if provider.api == "chatgpt":
            if not CHATGPT_CREDENTIALS.exists():
                return CommandReply("ChatGPT girişi zaten yok.")
            CHATGPT_CREDENTIALS.unlink()
            return CommandReply("ChatGPT girişi bu bilgisayardan silindi.")
        if provider.api_key_env is None:
            return CommandReply(f"`{provider.name}` yerel çalışır; silinecek anahtar yok.")
        removed = remove_key(provider.api_key_env)
        text = "Kayıtlı anahtar silindi." if removed else "Kayıtlı anahtar zaten yok."
        if os.environ.get(provider.api_key_env):
            text += f" {provider.api_key_env} ortam değişkeni hâlâ tanımlı."
        return CommandReply(text)

    # ---------------------------------------------------------------- anatomi

    def _anatomy(self, argument: str) -> CommandReply:
        base = self.anatomy.name.split("+")[0].split("-")[0]
        layers_on = [layer for layer in LAYERS if getattr(self.anatomy, layer)]
        # Paket değişince kullanıcının değiştirdiği katmanlar taşınır, paketin kendi
        # varsayılanları (ör. bash'in persistent_cwd'si) yeni paketten gelir.
        default = PRESETS.get(base, self.anatomy)
        changed = {
            layer for layer in LAYERS if getattr(self.anatomy, layer) != getattr(default, layer)
        }

        def carried(preset: Anatomy) -> set[str]:
            return {layer for layer in LAYERS if getattr(preset, layer) != (layer in changed)}

        if not argument:
            presets = tuple(
                Choice(
                    compose(a.name, carried(a)),
                    a.name,
                    ", ".join(a.tools) or "araç yok: model yalnız yazar",
                    group="Hazır paketler",
                    current=a.name == base,
                )
                for a in PRESETS.values()
            )
            toggles = tuple(
                Choice(
                    compose(base, set(layers_on) ^ {layer}),
                    f"{layer} · {'açık' if layer in layers_on else 'kapalı'}",
                    description,
                    group="Ek katmanlar (seçince açılır/kapanır)",
                    current=layer in layers_on,
                )
                for layer, description in LAYERS.items()
            )
            question = "Hangi katmanlar açık olsun?"
            return CommandReply(picker=Picker("anatomy", question, presets + toggles))
        try:
            picked = get_preset(argument.casefold())
        except ValueError as exc:
            return CommandReply(f"{exc} Liste için `/anatomy`.")
        if picked.name != self.anatomy.name:
            self.anatomy = picked
            self.tools = picked.tool_registry(self.root)
            # Araç listesi değişti: sohbet içi izinler taşınmaz, `--allow` yeniden uygulanır.
            self.permissions = self._new_permissions()
            self._rebuild()
        self._save()
        return CommandReply(
            f"Anatomi: **{self.anatomy.name}** · {len(self._tool_names())} araç · geçmiş korundu."
        )

    def _theme(self, argument: str) -> CommandReply:
        themes = available()
        if not argument:
            choices = tuple(
                Choice(name, name, palette.description, current=name == self.theme)
                for name, palette in themes.items()
            )
            hint = f"Renk teması (kişisel tema: {THEME_FILE})"
            return CommandReply(picker=Picker("theme", hint, choices))
        if argument not in themes:
            return CommandReply(f"`{argument}` adlı tema yok. Liste için `/theme`.")
        self.theme = argument
        self._save()
        return CommandReply(f"Tema: **{argument}**", theme=argument)

    def _panel(self, argument: str) -> CommandReply:
        if not argument:
            choices = tuple(
                Choice(name, name, detail, current=name == self.panel)
                for name, detail in PANEL_MODES.items()
            )
            return CommandReply(picker=Picker("panel", "Sağ panel (Ctrl+B dolaşır)", choices))
        name = view_name(argument, PANEL_MODES, "")
        if not name:
            return CommandReply(f"`{argument}` adlı panel yok: {', '.join(PANEL_MODES)}.")
        self.panel = name
        self._save()
        return CommandReply(f"Panel: **{name}**", display=True)

    def cycle_panel(self) -> str:
        """Sıradaki panel görünümü (tam ekranda Ctrl+B); seçim kalıcıdır."""
        names = list(PANEL_MODES)
        self.panel = names[(names.index(self.panel) + 1) % len(names)]
        self._save()
        return self.panel

    def _thinking(self, argument: str) -> CommandReply:
        if not argument:
            choices = tuple(
                Choice(name, name, detail, current=name == self.thinking)
                for name, detail in THINKING_MODES.items()
            )
            return CommandReply(picker=Picker("thinking", "Düşünce görünümü", choices))
        name = view_name(argument, THINKING_MODES, "")
        if not name:
            return CommandReply(f"`{argument}` adlı görünüm yok: {', '.join(THINKING_MODES)}.")
        self.thinking = name
        self._save()
        return CommandReply(f"Düşünce: **{name}**", display=True)

    def cycle_theme(self) -> str:
        """Sıradaki tema (tam ekranda kısayol); seçim kalıcıdır."""
        names = list(available())
        self.theme = (
            names[(names.index(self.theme) + 1) % len(names)] if self.theme in names else names[0]
        )
        self._save()
        return self.theme

    def _watch_panel(self, argument: str = "") -> str:
        """Canlı panel bu projenin kayıtlarını izler; en yeni kayıt (bu sohbet) kendisi açılır.

        `/watch <no>`: oturum listesindeki o kayıt panelde açılır (geçmişe bakmak için).
        """
        log: Path | None = None
        if argument:
            sessions = self._recent_sessions()
            if not argument.isdigit() or not 1 <= int(argument) <= len(sessions):
                return f"`{argument}` numaralı oturum yok. Liste için `/sessions`."
            log = sessions[int(argument) - 1].path
        if self._watch is None:
            self._watch = WatchServer(self.sessions_dir)
        try:
            url = self._watch.start()
        except OSError as exc:
            return f"Canlı panel açılamadı: {exc}"
        if log is not None:
            url = self._watch.url_for(log)
        if self._open_url is not None:
            self._open_url(url)
        if log is not None:
            return f"Panelde açıldı: {url}"
        return (
            f"Canlı panel: {url} · sohbet burada sürer, panel en yeni kayda (bu sohbete) "
            "kendiliğinden geçer; onaylar burada verilir."
        )

    # ----------------------------------------------------------- yeni komutlar

    def _events(self) -> list[Event]:
        try:
            return read_session(self.log.path) if self.log.path.exists() else []
        except (OSError, ValueError):
            return []

    def _copy(self) -> CommandReply:
        answers = [e.text for e in self._events() if isinstance(e, ModelReplied) and e.text.strip()]
        if not answers:
            return CommandReply("Kopyalanacak cevap yok: bu oturumda ajan henüz yazmadı.")
        return CommandReply(
            f"Son cevap panoya kopyalandı ({len(answers[-1])} karakter).", clipboard=answers[-1]
        )

    def _export(self, argument: str) -> CommandReply:
        """`/export` bu oturumu, `/export <no>` listedeki oturumu, `/export <dosya>` oraya yazar."""
        source = self.log.path
        if argument.isdigit():
            sessions = self._recent_sessions()
            if not 1 <= int(argument) <= len(sessions):
                return CommandReply(f"`{argument}` numaralı oturum yok. Liste için `/sessions`.")
            source, argument = sessions[int(argument) - 1].path, ""
        try:
            events = read_session(source) if source.exists() else []
        except (OSError, ValueError) as exc:
            return CommandReply(f"Kayıt okunamadı: {exc}")
        if not any(isinstance(e, TaskStarted) for e in events):
            return CommandReply("Dışa aktarılacak tur yok.")
        target = Path(argument).expanduser() if argument else source.with_suffix(".md")
        if not target.is_absolute():
            target = self.root / target
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(transcript_markdown(events), encoding="utf-8")
        except OSError as exc:
            return CommandReply(f"Yazılamadı: {exc}")
        return CommandReply(f"Dışa aktarıldı: `{target}`")

    def _memory(self) -> CommandReply:
        base = self.anatomy.name.split("+")[0].split("-")[0]
        layers_on = {layer for layer in LAYERS if getattr(self.anatomy, layer)}
        turning_on = "memory" not in layers_on
        reply = self._anatomy(compose(base, layers_on ^ {"memory"}))
        if not turning_on:
            return replace(reply, text="Hafıza kapandı · " + reply.text)
        problems: list[str] = []
        if ctx_command() is None:
            problems.append("`ctx` bulunamadı (agent-context kurulu değil)")
        if not (self.root / "context.yaml").is_file():
            problems.append("projede `context.yaml` yok (`ctx bootstrap --project <ad>`)")
        text = "Hafıza açıldı: görev başında not haritası, dosyaya dokununca ilgili notlar."
        if problems:
            text += " Ama çalışmayacak: " + "; ".join(problems) + "."
        return replace(reply, text=f"{text} · {reply.text}")

    async def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        """git'i ayrı iş parçacığında çalıştırır (yavaş depoda arayüz donmasın).

        `core.quotepath=off`: Türkçe/boşluklu dosya adları tırnaksız ve olduğu gibi gelir."""
        return await asyncio.to_thread(
            subprocess.run,
            ["git", "-c", "core.quotepath=off", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )

    def _compact(self) -> str:
        """Elle bağlam toplama: budama katmanının yaptığını şimdi, bir kez yapar."""
        history = self.agent.history
        before = estimate_tokens(history)
        pruned, results, chars = prune(history, PRUNE_KEEP_STEPS)
        if not results:
            return (
                f"Toplanacak eski araç sonucu yok: son {PRUNE_KEEP_STEPS} adımın sonuçları "
                "her zaman korunur."
            )
        history[:] = pruned
        after = estimate_tokens(pruned)
        summary = (
            f"/compact · {results} sonuç · {chars:,} karakter · ~{before:,} → ~{after:,} token"
        ).replace(",", ".")
        # Kayda girer: incelemede görünür. Devam edilen oturum yine tam geçmişle açılır.
        self.log.write(
            HarnessIntervened(step=0, layer="context_prune", action="prune", summary=summary)
        )
        return (
            f"{results} eski araç sonucu bağlamdan çıkarıldı "
            f"(~{before:,} → ~{after:,} token).".replace(",", ".")
            + " Model gerekirse dosyaları yeniden okur; bir sonraki çağrıda önbellek bir kez "
            "yeniden kurulur."
        )

    async def _undo(self) -> str:
        """Son değişiklikli turu geri alır ve bir sonraki mesajda modele söyler."""
        if self.checkpoints is None:
            return "Geri alma yalnız sohbette çalışır (tek görevlik koşuda görüntü alınmaz)."
        if self.checkpoints.broken is not None:
            return f"Geri alma bu oturumda kapalı: {self.checkpoints.broken}"
        try:
            restored = await asyncio.to_thread(self.checkpoints.undo)
        except CheckpointUnavailable as exc:
            return f"Geri alınamadı: {exc}"
        if restored is None:
            return "Geri alınacak değişiklik yok: bu oturumun turları dosya değiştirmedi."
        files = [*restored.rewritten, *restored.removed]
        shown = ", ".join(f"`{p}`" for p in files[:20]) + (
            f" … ve {len(files) - 20} dosya daha" if len(files) > 20 else ""
        )
        self.agent.notes.append(
            f'[harness] Kullanıcı /undo ile "{restored.label}" isteğinden sonraki dosya '
            f"değişikliklerini geri aldı. Şu dosyalar o istekten önceki hâlinde: {shown}. "
            "Bu dosyaların içeriğini hatırladığın gibi varsayma; gerekirse yeniden oku."
        )
        remaining = len(self.checkpoints)  # en fazla bu kadar; dosyaya dokunmayan turlar atlanır
        more = f" Daha geriye gitmek için yine `/undo` ({remaining} tur)." if remaining else ""
        return (
            f'"{restored.label}" turundan önceki hâle dönüldü: {restored.summary()}.\n\n'
            f"{shown}\n\nAjan bunu bir sonraki mesajınla öğrenecek.{more}"
        )

    async def _diff(self) -> str:
        try:
            stat = await self._git("diff", "--stat", "HEAD")
            if stat.returncode != 0:
                return "Burası bir git deposu değil ya da henüz commit yok; fark gösterilemiyor."
            body = (await self._git("diff", "HEAD")).stdout
            listing = await self._git("ls-files", "-z", "--others", "--exclude-standard")
            untracked = [p for p in listing.stdout.split("\0") if p]
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"git çalıştırılamadı: {exc}"
        if not stat.stdout.strip() and not untracked:
            return "Çalışma dizini temiz: son commit'ten beri değişiklik yok."
        parts: list[str] = []
        if stat.stdout.strip():
            parts.append(f"```\n{stat.stdout.rstrip()}\n```")
        if untracked:
            parts.append(
                "Yeni (izlenmeyen) dosyalar: " + ", ".join(f"`{p}`" for p in untracked[:30])
            )
        if body.strip():
            limit = 20_000
            shown = body if len(body) <= limit else body[:limit] + "\n… (kalanı kesildi)"
            parts.append(f"```diff\n{shown.rstrip()}\n```")
        return "\n\n".join(parts)

    def close(self) -> None:
        """Ön yüz kapanırken: canlı panel sunucusu durur."""
        if self._watch is not None:
            self._watch.close()

    # ----------------------------------------------------------------- oturum

    def _recent_sessions(self) -> list[SessionSummary]:
        if not self.sessions_dir.is_dir():
            return []
        return list_sessions(self.sessions_dir)[:SESSION_LIMIT]

    def _sessions_picker(
        self, kind: PickerKind, title: str, only: list[int] | None = None
    ) -> Picker | None:
        """Oturumlar gün gruplarıyla; satır = ilk görev (aranır), ayrıntı = saat, tur, model."""
        sessions = self._recent_sessions()
        if not sessions:
            return None
        today = datetime.now().date()
        choices: list[Choice] = []
        for i, s in enumerate(sessions, start=1):
            if only is not None and i not in only:
                continue
            days = (today - s.updated.date()).days
            group = (
                "Bugün"
                if days == 0
                else "Dün"
                if days == 1
                else "Bu hafta"
                if days < 7
                else "Daha eski"
            )
            # Grup günü söylüyor: bugün/dün için saat yeter; satır tek satıra sığsın.
            when = f"{s.updated:%H:%M}" if days < 2 else f"{s.updated:%d.%m %H:%M}"
            detail = [when, f"{s.turns} tur"]
            if s.model and s.model != self.model:
                detail.append(s.model)
            if s.turns > 1 and s.last_task != s.first_task:
                detail.append("son: " + short(s.last_task.replace("\n", " "), 30))
            choices.append(
                Choice(
                    str(i),
                    short(s.first_task.replace("\n", " "), 55),
                    " · ".join(detail),
                    group=group,
                    current=s.path == self.log.path,
                )
            )
        return Picker(kind, title, tuple(choices))

    def _sessions(self, argument: str) -> CommandReply:
        """Oturum gezgini: oturum seçilince ne yapılacağı sorulur."""
        if argument:  # "/sessions 3": o oturumun eylemleri
            sessions = self._recent_sessions()
            if not argument.isdigit() or not 1 <= int(argument) <= len(sessions):
                return CommandReply(f"`{argument}` numaralı oturum yok. Liste için `/sessions`.")
            s = sessions[int(argument) - 1]
            title = f"{short(s.first_task.replace(chr(10), ' '), 60)} · {s.updated:%d.%m %H:%M}"
            actions = (
                Choice(f"resume {argument}", "Devam et", "konuşma ekrana gelir, kaldığı yerden"),
                Choice(f"watch {argument}", "Panelde aç", "defter ve zaman çizelgesiyle incele"),
                Choice(f"export {argument}", "Dışa aktar", f"{s.path.with_suffix('.md').name}"),
            )
            return CommandReply(picker=Picker("session", title, actions))
        picker = self._sessions_picker("sessions", "Geçmiş oturumlar (yazarak ara)")
        if picker is None:
            return CommandReply("Bu projede kayıtlı oturum yok.")
        return CommandReply(picker=picker)

    def resume_latest(self) -> CommandReply:
        """`harness -c`: bu projenin en son oturumuna devam."""
        if not self._recent_sessions():
            return CommandReply("Bu projede devam edilecek oturum yok; yeni oturum açıldı.")
        return self._resume("1")

    def _resume(self, argument: str) -> CommandReply:
        sessions = self._recent_sessions()
        if not argument or not argument.isdigit():
            only = None
            if argument:  # "/resume indirim": görevlerinde geçen oturumlar
                words = argument.casefold().split()
                only = [
                    i
                    for i, s in enumerate(sessions, start=1)
                    if all(w in f"{s.first_task} {s.last_task}".casefold() for w in words)
                ]
                if len(only) == 1:
                    return self._resume(str(only[0]))
                if not only:
                    return CommandReply(f"“{argument}” geçen oturum yok. Liste için `/resume`.")
            picker = self._sessions_picker("resume", "Hangi oturuma devam edilsin?", only)
            if picker is None:
                return CommandReply("Bu projede kayıtlı oturum yok.")
            return CommandReply(picker=picker)
        if not 1 <= int(argument) <= len(sessions):
            return CommandReply(f"`{argument}` numaralı oturum yok. Liste için `/resume`.")
        path = sessions[int(argument) - 1].path
        try:
            events = read_session(path)
        except (OSError, ValueError) as exc:
            return CommandReply(f"Oturum okunamadı: {exc}")

        self.log = SessionLogger(path)
        self.permissions = self._new_permissions()
        self.checkpoints = self._new_checkpoints()
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


def transcript_markdown(events: list[Event]) -> str:
    """Oturumun okunur dökümü: görevler, ajanın yazdıkları ve araç çağrıları tek satırla."""
    lines: list[str] = []
    for event in events:
        if isinstance(event, TaskStarted):
            stamp = f"{event.timestamp.astimezone():%d.%m.%Y %H:%M}" if event.timestamp else ""
            meta = " · ".join(
                x for x in (stamp, event.model, event.anatomy and event.anatomy.name) if x
            )
            lines += [f"## Sen{f' · {meta}' if meta else ''}", "", event.task, ""]
        elif isinstance(event, ModelReplied) and event.text.strip():
            lines += [
                "## Ajan" if not event.tool_calls else "**Ajan:**",
                "",
                event.text.strip(),
                "",
            ]
        elif isinstance(event, ToolFinished):
            mark = "✗" if event.result.is_error else "✓"
            what = f"{event.call.name} · {event.summary}" if event.summary else event.call.name
            lines.append(f"- {mark} `{what}`" + (f" → {event.outcome}" if event.outcome else ""))
        elif isinstance(event, HarnessIntervened):
            lines.append(f"- ⚑ harness · {event.layer}: {event.summary}")
    return "\n".join(lines).strip() + "\n"


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

    def cache_share() -> str:
        pairs = [
            (e.usage.input_tokens, e.usage.cached_tokens) if e.usage else (None, None)
            for e in replies
        ]
        if not pairs or any(i is None or c is None for i, c in pairs):
            return total("cached_tokens")
        inputs = sum(i or 0 for i, _ in pairs)
        cached = sum(c or 0 for _, c in pairs)
        share = f" (%{100 * cached / inputs:.0f})" if inputs else ""
        return total("cached_tokens") + share

    seconds = sum(e.duration_seconds or 0 for e in replies)
    by_tool = Counter(e.call.name for e in tools)
    interventions = Counter(e.layer for e in events if isinstance(e, HarnessIntervened))
    errors = sum(e.result.is_error for e in tools)
    rows = [
        ("Tur / model çağrısı", f"{turns} / {len(replies)}"),
        ("Girdi tokenı", total("input_tokens")),
        ("  önbellekten", cache_share()),
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
    reading = read_stats(events)
    if reading.lines_read:
        rows.append(("Tekrar okunan satır", reread_text(reading)))
    work = work_stats(events)
    if work.steps:
        rows.extend(work_rows(work))
    if interventions:
        listed = ", ".join(f"{n} ×{c}" for n, c in interventions.most_common())
        rows.append(("Harness müdahalesi", f"{interventions.total()} ({listed})"))
    return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows)


def _named[T](items: list[T], argument: str, names: Callable[[T], tuple[str, ...]]) -> T | None:
    """Adla seçer, büyük/küçük harf ayırmadan."""
    wanted = argument.casefold()
    return next((i for i in items if wanted in (n.casefold() for n in names(i))), None)
