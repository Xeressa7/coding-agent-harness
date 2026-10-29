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
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from harness.cli.inspector import reread_text
from harness.cli.themes import THEME_FILE, available, palette_for
from harness.cli.watch import WatchServer
from harness.core.agent import Agent
from harness.core.anatomy import LAYERS, PRESETS, Anatomy, get_preset
from harness.core.events import (
    Event,
    HarnessIntervened,
    ModelReplied,
    TaskStarted,
    ToolFinished,
)
from harness.core.metrics import read_stats
from harness.core.permissions import Decision, PermissionManager
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

HELP = """\
| Komut | Ne yapar |
|---|---|
| `/model` | Model seçer; bağlı bütün sağlayıcıların modelleri tek listede |
| `/effort` | Düşünme seviyesi; `/effort varsayılan` modelin kendi seçimine döner |
| `/connect` | Sağlayıcı bağlar: ChatGPT'ye tarayıcıyla giriş ya da API anahtarı |
| `/disconnect <ad>` | Kayıtlı anahtarı ya da ChatGPT girişini siler |
| `/anatomy` | Açık katmanlar: paketler (raw, files, bash, full) ve ek katmanlar |
| `/status` | Model, effort, anatomi, proje ve kayıt |
| `/tools` | Açık araçlar ve izin kuralları |
| `/usage` | Bu oturumun token, süre ve araç toplamları (`/cost`) |
| `/new` | Yeni oturum: geçmiş sıfırlanır, yeni kayıt (`/clear`) |
| `/resume` | Bu projenin bir oturumuna kaldığı yerden devam eder |
| `/inspect` | Son HTML incelemesini açar |
| `/watch` | Canlı paneli tarayıcıda açar: sohbet burada, akış orada |
| `/theme` | Renk teması: koyu, açık, yüksek kontrast, renk körü dostu, kişisel |
| `/exit` | Çıkış (`/quit`) |

Argümansız yazılan seçim komutları liste açar. Doğrudan da seçilebilir:
`/model opencode-go/glm-5.3`, `/effort high`, `/anatomy bash`. Seçimler kalıcıdır."""

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
    "/sessions": "/resume",
}
DEFAULT_EFFORT_WORDS = {"varsayılan", "default", "none", "yok"}
SESSION_LIMIT = 10
# Sohbette bir mesajın en çok kaç model adımı süreceği. Keşif görevleri 20'yi aşıyor
# (2026-10-29 gerçek koşu: "repoyu incele" 20 adımda sonuca varmadan durdu).
CHAT_STEP_LIMIT = 50
STEP_LIMIT_HINT = "Adım sınırı doldu; geçmiş korundu: kaldığı yerden sürmesi için “devam” yaz."

PickerKind = Literal["model", "effort", "connect", "anatomy", "resume", "theme"]


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
    ) -> None:
        self.root = root
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
        self._watch: WatchServer | None = None
        self._models: dict[str, list[ModelInfo]] = {}
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
            max_steps=CHAT_STEP_LIMIT,
        )

    def _rebuild(self) -> None:
        self.agent = self._build_agent(self.agent.history[1:])

    def _save(self) -> None:
        if self._save_choice is not None:
            self._save_choice(self)

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
            case "/watch":
                return CommandReply(self._watch_panel())
            case "/theme":
                return self._theme(argument)
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
                self.permissions = PermissionManager(self.root, self.tools)
                self.agent = self._build_agent([])
                return CommandReply(f"Yeni oturum · {self.label}", reset=True)
            case "/resume":
                return self._resume(argument)
            case _:
                return CommandReply(f"Bilinmeyen komut: `{name}`. Liste için `/help`.")

    async def choose(self, kind: PickerKind, value: str) -> CommandReply:
        """Listeden seçilen satır. Listeden seçilen model, effort sorusunu da açar."""
        if kind == "model":
            return await self._select_model(value, ask_effort=True)
        return await self.run(f"/{kind} {value}")

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
        if not argument:
            presets = tuple(
                Choice(
                    a.name + "".join(f"+{x}" for x in layers_on if not getattr(a, x)),
                    a.name,
                    ", ".join(a.tools) or "araç yok: model yalnız yazar",
                    group="Hazır paketler",
                    current=a.name == base,
                )
                for a in PRESETS.values()
            )
            toggles = tuple(
                Choice(
                    base + "".join(f"+{x}" for x in LAYERS if (x in layers_on) != (x == layer)),
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
            # Araç listesi değişti: eski izinler yeni araçlara taşınmaz.
            self.permissions = PermissionManager(self.root, self.tools)
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

    def cycle_theme(self) -> str:
        """Sıradaki tema (tam ekranda kısayol); seçim kalıcıdır."""
        names = list(available())
        self.theme = (
            names[(names.index(self.theme) + 1) % len(names)] if self.theme in names else names[0]
        )
        self._save()
        return self.theme

    def _watch_panel(self) -> str:
        """Canlı panel bu projenin kayıtlarını izler; en yeni kayıt (bu sohbet) kendisi açılır."""
        if self._watch is None:
            self._watch = WatchServer(self.sessions_dir)
        try:
            url = self._watch.start()
        except OSError as exc:
            return f"Canlı panel açılamadı: {exc}"
        if self._open_url is not None:
            self._open_url(url)
        return (
            f"Canlı panel: {url}\n\nSohbet burada sürer; panel bu projenin kayıtlarını izler "
            "ve en yenisine (bu sohbete) kendiliğinden geçer. Onaylar burada verilir."
        )

    def close(self) -> None:
        """Ön yüz kapanırken: canlı panel sunucusu durur."""
        if self._watch is not None:
            self._watch.close()

    # ----------------------------------------------------------------- oturum

    def _recent_sessions(self) -> list[SessionSummary]:
        if not self.sessions_dir.is_dir():
            return []
        return list_sessions(self.sessions_dir)[:SESSION_LIMIT]

    def _sessions_picker(self) -> Picker | None:
        sessions = self._recent_sessions()
        if not sessions:
            return None
        choices: list[Choice] = []
        for i, s in enumerate(sessions, start=1):
            task = short(s.first_task.replace("\n", " "), 60)
            choices.append(
                Choice(
                    str(i),
                    f"{s.updated:%d.%m %H:%M}",
                    f"{s.turns} tur · {task}",
                    current=s.path == self.log.path,
                )
            )
        return Picker("resume", "Hangi oturuma devam edilsin?", tuple(choices))

    def resume_latest(self) -> CommandReply:
        """`harness -c`: bu projenin en son oturumuna devam."""
        if not self._recent_sessions():
            return CommandReply("Bu projede devam edilecek oturum yok; yeni oturum açıldı.")
        return self._resume("1")

    def _resume(self, argument: str) -> CommandReply:
        sessions = self._recent_sessions()
        if not argument:
            picker = self._sessions_picker()
            if picker is None:
                return CommandReply("Bu projede kayıtlı oturum yok.")
            return CommandReply(picker=picker)
        if not argument.isdigit() or not 1 <= int(argument) <= len(sessions):
            return CommandReply(f"`{argument}` numaralı oturum yok. Liste için `/resume`.")
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
    if interventions:
        listed = ", ".join(f"{n} ×{c}" for n, c in interventions.most_common())
        rows.append(("Harness müdahalesi", f"{interventions.total()} ({listed})"))
    return "| | |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows)


def _named[T](items: list[T], argument: str, names: Callable[[T], tuple[str, ...]]) -> T | None:
    """Adla seçer, büyük/küçük harf ayırmadan."""
    wanted = argument.casefold()
    return next((i for i in items if wanted in (n.casefold() for n in names(i))), None)
