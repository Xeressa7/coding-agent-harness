"""Giriş noktası: komut satırı (Typer).

Alışılmış kodlama ajanı CLI'larının biçimi:

    harness                  etkileşimli sohbet (terminalde tam ekran, boruda sade)
    harness -p "soru"        tek seferlik görev; yalnız cevap yazılır (betiklerde kullanılır)
    harness -c               bu projenin son oturumuna devam
    harness provider|model|effort|anatomy [ad]   kalıcı seçimler, her biri ayrı
    harness status|providers|models|sessions     bilgi
    harness login|logout     ChatGPT planı girişi
    harness inspect|replay|demo                  inceleme ve eğitim

`ask` ve `chat` eski adlarıyla da çalışır.
"""

import asyncio
import os
import re
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import IntPrompt
from rich.table import Table
from rich.text import Text

from harness.cli.commands import (
    DEFAULT_EFFORT_WORDS,
    ChatSession,
    CommandReply,
    is_command,
)
from harness.cli.inspector import code_blocks_summary, summary_items, write_report
from harness.cli.render import ask_permission, live_outcome, render
from harness.core.agent import Agent
from harness.core.anatomy import DEFAULT_PRESET, PRESETS, Anatomy, get_preset
from harness.core.config import (
    DEFAULT_SESSIONS_HOME,
    Settings,
    find_project_root,
    load_settings,
    project_sessions_dir,
    save_settings,
)
from harness.core.events import (
    CodeBlocksSaved,
    Event,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    PermissionRequested,
    TaskFinished,
    TaskStarted,
    ToolFinished,
    ToolStarted,
)
from harness.core.session_log import SessionLogger, list_sessions, read_session
from harness.llm.base import LLMError, ModelInfo
from harness.llm.chatgpt_auth import DEFAULT_PATH as CHATGPT_CREDENTIALS
from harness.llm.chatgpt_auth import AuthError, login
from harness.llm.providers import (
    PROVIDERS,
    Provider,
    ProviderError,
    get_provider,
    list_models,
    make_llm,
    reachable,
    readiness,
)
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall

app = typer.Typer(
    help="Şeffaf kodlama ajanı harness'i: içi görünen, katmanları açılıp kapanan ajan.",
    no_args_is_help=False,
)
console = Console()
errors = Console(stderr=True)

_PROMPT_DIR = Path(__file__).parent.parent / "prompts"
_PROMPT_FILES = {"agent": "system.md", "raw": "raw.md"}
SESSIONS_HOME = DEFAULT_SESSIONS_HOME
# Ağ, giriş ya da sağlayıcı hatası: kullanıcıya gösterilir, yığın dökümü basılmaz.
FAILURES = (AuthError, LLMError, httpx.HTTPError)

DEMO_TASK = "Bu proje hangi Python sürümünü istiyor, bağımlılıkları neler?"
DEMO_PROVIDER = Provider(
    name="demo", label="Sahte model (hazır senaryo)", api="chat_completions", default_model="sahte"
)

# Sahte modelin senaryosu: önce yanlış dosya adı ister, hatayı görüp düzeltir,
# sonra okuduğu dosyaya dayanarak cevap verir.
DEMO_SCRIPT = [
    AssistantMessage(
        text="Önce proje ayarlarına bakayım.",
        tool_calls=(ToolCall(id="c1", name="read_file", arguments='{"path": "pyproject.yaml"}'),),
    ),
    AssistantMessage(
        text="Dosya yokmuş; Python projelerinde bu dosya .toml uzantılıdır.",
        tool_calls=(
            ToolCall(
                id="c2",
                name="read_file",
                arguments='{"path": "pyproject.toml", "start_line": 1, "end_line": 17}',
            ),
        ),
    ),
    AssistantMessage(
        text=(
            'Proje Python 3.12 veya üstünü istiyor (requires-python = ">=3.12"). '
            "Çalışma bağımlılıkları: httpx (HTTP), pydantic (veri doğrulama), "
            "rich (terminal çıktısı) ve typer (komut satırı)."
        )
    ),
]


# ---------------------------------------------------------------- ortak seçenekler

ProviderOption = Annotated[
    str | None,
    typer.Option("--provider", help=f"Sağlayıcı: {', '.join(PROVIDERS)} (kayıtlıyı ezer)"),
]
ModelOption = Annotated[str | None, typer.Option("--model", "-m", help="Model (kayıtlıyı ezer)")]
EffortOption = Annotated[
    str | None, typer.Option("--effort", help="Düşünme seviyesi (kayıtlıyı ezer)")
]
AnatomyOption = Annotated[
    str | None, typer.Option("--anatomy", help=f"Açık katmanlar: {', '.join(PRESETS)}")
]
PlainOption = Annotated[bool, typer.Option("--plain", help="Tam ekran yerine sade çıktı")]
ContinueOption = Annotated[
    bool, typer.Option("--continue", "-c", help="Bu projenin son oturumuna devam et")
]
AllowOption = Annotated[
    list[str] | None,
    typer.Option("--allow", help="Sormadan çalışacak araç (tekrarlanabilir); 'all' hepsini açar"),
]


@dataclass(frozen=True)
class RunConfig:
    """Bu koşunun seçimleri: kayıtlı ayarlar + komut satırı bayrakları."""

    provider: Provider
    model: str
    effort: str | None
    anatomy: Anatomy
    remembered: dict[str, str]

    @property
    def label(self) -> str:
        return f"{self.provider.name} · {self.model} · effort: {self.effort or 'varsayılan'}"


def resolve_config(
    provider: str | None,
    model: str | None,
    effort: str | None,
    anatomy: str | None,
) -> RunConfig:
    """Bayrak verilmeyen her seçim kayıttan gelir; sağlayıcı değişince model de ona uyar."""
    settings = load_settings()
    try:
        picked_provider = get_provider(provider or settings.provider)
    except ProviderError as exc:
        raise typer.BadParameter(str(exc), param_hint="--provider") from exc
    same_provider = picked_provider.name == settings.provider
    if model:
        picked_model = model
    elif same_provider:
        picked_model = settings.model
    else:
        picked_model = settings.models.get(picked_provider.name, picked_provider.default_model)
    if effort:
        picked_effort = None if effort.casefold() in DEFAULT_EFFORT_WORDS else effort
    elif same_provider and picked_model == settings.model:
        picked_effort = settings.effort
    else:
        picked_effort = None
    return RunConfig(
        provider=picked_provider,
        model=picked_model,
        effort=picked_effort,
        anatomy=pick_anatomy(anatomy or settings.anatomy or DEFAULT_PRESET),
        remembered=dict(settings.models),
    )


def pick_anatomy(name: str) -> Anatomy:
    try:
        return get_preset(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--anatomy") from exc


def load_system_prompt(anatomy: Anatomy) -> str:
    """Anatominin talimat dosyasını okur; dosyanın başındaki açıklama yorumu modele gitmez."""
    text = (_PROMPT_DIR / _PROMPT_FILES[anatomy.prompt]).read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def start_session(label: str) -> Path:
    """Çalışma dizinini bulur; neyle ve nerede çalışıldığını yazar."""
    root = find_project_root(Path.cwd())
    console.print(Text(f"{label} · Çalışma dizini: {root}", style="dim"))
    return root


def sessions_dir(root: Path) -> Path:
    return project_sessions_dir(root, SESSIONS_HOME)


def chat_session(root: Path, config: RunConfig, http: httpx.AsyncClient) -> ChatSession:
    """Gerçek modelle sohbet; model listesi ve istemci aynı HTTP bağlantısını paylaşır."""
    return ChatSession(
        root=root,
        anatomy=config.anatomy,
        provider=config.provider,
        model=config.model,
        effort=config.effort,
        make_llm=lambda provider, model, effort: make_llm(provider, model, effort, http),
        load_models=lambda provider: list_models(provider, http),
        load_prompt=load_system_prompt,
        sessions_dir=sessions_dir(root),
        remembered_models=config.remembered,
    )


def grant(session: ChatSession, allow: list[str] | None) -> None:
    """`--allow`: verilen araçlar bu oturumda sormadan çalışır (yol denetimi yine geçerli)."""
    names = [spec.name for spec in session.tools.specs()]
    for name in allow or []:
        if name == "all":
            for known in names:
                session.permissions.grant(known)
        elif name in names:
            session.permissions.grant(name)
        else:
            raise typer.BadParameter(
                f"'{name}' bu anatomide yok. Var olanlar: {', '.join(names) or '—'}",
                param_hint="--allow",
            )


def run_async[T](make: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Async işi çalıştırır; bilinen hatayı tek satırla yazıp çıkış koduyla bitirir."""
    try:
        return asyncio.run(make())
    except FAILURES as exc:
        errors.print(Text(f"Hata: {exc}", style="red"))
        raise typer.Exit(1) from exc
    except KeyboardInterrupt as exc:
        errors.print("Durduruldu.")
        raise typer.Exit(130) from exc


def version_callback(value: bool) -> None:
    if value:
        try:
            console.print(f"harness {version('harness')}")
        except PackageNotFoundError:
            console.print("harness (kurulu sürüm bilinmiyor)")
        raise typer.Exit()


# ---------------------------------------------------------------- kök: sohbet / -p


@app.callback(invoke_without_command=True)
def root_command(
    ctx: typer.Context,
    prompt: Annotated[
        str | None,
        typer.Option("--print", "-p", help="Tek görev çalıştır, yalnız cevabı yaz ve çık"),
    ] = None,
    continue_: ContinueOption = False,
    provider: ProviderOption = None,
    model: ModelOption = None,
    effort: EffortOption = None,
    anatomy_name: AnatomyOption = None,
    allow: AllowOption = None,
    plain: PlainOption = False,
    show_version: Annotated[
        bool,
        typer.Option(
            "--version", "-V", callback=version_callback, is_eager=True, help="Sürümü yaz"
        ),
    ] = False,
) -> None:
    """Komutsuz çağrı sohbet açar; -p ile tek seferlik görev çalıştırır."""
    if ctx.invoked_subcommand is not None:
        return
    config = resolve_config(provider, model, effort, anatomy_name)
    if prompt is not None:
        run_once(prompt, config, quiet=True, resume=continue_, allow=allow)
    else:
        run_chat(config, plain=plain, resume=continue_, allow=allow)


def run_once(
    question: str,
    config: RunConfig,
    *,
    quiet: bool,
    resume: bool = False,
    allow: list[str] | None = None,
) -> None:
    """Tek görev. `quiet` (-p): cevap stdout'a, geri kalan her şey stderr'e."""
    root = find_project_root(Path.cwd()) if quiet else start_session(config.label)
    if not quiet:
        console.rule("[bold cyan]Sen", align="left")
        console.print(Text(question))

    async def run() -> TurnOutcome:
        async with httpx.AsyncClient(timeout=120) as http:
            session = chat_session(root, config, http)
            if resume:
                session.resume_latest()
            grant(session, allow)
            return await run_turn(session.agent, question, session.log, quiet=quiet)

    finish = run_async(run).finish
    if finish is not None and finish.reason in {"error", "cancelled"}:
        raise typer.Exit(1)


# ---------------------------------------------------------------- tur


@dataclass(frozen=True)
class TurnOutcome:
    finish: TaskFinished | None  # None: tur bitiş olayı yazılmadan kesildi
    report: Path | None


async def run_turn(
    agent: Agent, question: str, log: SessionLogger, *, quiet: bool = False
) -> TurnOutcome:
    """Bir turu çalıştırır, olayları kaydeder. Sade akışta konuşma, -p'de yalnız cevap yazar."""
    events: list[Event] = []
    report: Path | None = None
    streamed_text = ""
    stream_open = False
    finish: TaskFinished | None = None
    try:
        with console.status("Model bekleniyor…") as status:
            if quiet:
                status.stop()
            stream = agent.run(question)
            send: bool | None = None
            while True:
                try:
                    event = await stream.asend(send)
                except StopAsyncIteration:
                    break
                send = None
                log.write(event)
                events.append(event)
                if isinstance(event, TaskFinished):
                    finish = event
                if isinstance(event, PermissionRequested):
                    status.stop()
                    send = _permission(agent, event, interactive=not quiet)
                elif quiet:
                    if isinstance(event, ModelReplied) and not event.tool_calls and event.text:
                        console.print(event.text, markup=False, highlight=False, soft_wrap=True)
                elif isinstance(event, ModelStarted):
                    streamed_text = ""
                    status.start()
                    status.update("Model bekleniyor…")
                elif isinstance(event, ToolStarted):
                    status.start()
                    status.update(f"{event.call.name} çalışıyor…")
                elif isinstance(event, ToolFinished):
                    # Araç çıktısı gizli kalır; ne yapıldığı ve sonucu tek satırda görünür.
                    status.stop()
                    console.print(tool_trace(event), soft_wrap=True)
                elif isinstance(event, ModelTextDelta) and event.text:
                    status.stop()
                    if not stream_open:
                        console.rule("[bold magenta]Ajan", align="left")
                        stream_open = True
                    console.print(Text(event.text), end="", soft_wrap=True)
                    streamed_text += event.text
                elif isinstance(event, CodeBlocksSaved):
                    status.stop()
                    console.print(Text(code_blocks_summary(event), style="dim"))
                elif isinstance(event, ModelReplied):
                    if stream_open:
                        # Bazı sağlayıcılar son parçayı yalnız tamamlanmış mesajda verir.
                        if event.text.startswith(streamed_text):
                            console.print(
                                Text(event.text[len(streamed_text) :]), end="", soft_wrap=True
                            )
                        elif event.text:
                            console.print()
                            console.print("[dim]Tamamlanmış yanıt:[/]")
                            console.print(Markdown(event.text))
                        console.print()
                        stream_open = False
                    elif not event.tool_calls and event.text:
                        status.stop()
                        console.rule("[bold magenta]Ajan", align="left")
                        console.print(Markdown(event.text))
    finally:
        if stream_open:
            console.print()
            console.print("[yellow]Yanıt tamamlanmadı.[/]")
        if events:
            report = _after_turn(events, log, quiet)
    return TurnOutcome(finish, report)


def tool_trace(event: ToolFinished) -> Text:
    return Text(f"  {live_outcome(event)}", style="yellow" if event.result.is_error else "dim")


def _permission(agent: Agent, event: PermissionRequested, *, interactive: bool) -> bool:
    """İzin sorusu. Soru sorulamayan modda (-p) güvenli taraf seçilir: reddedilir."""
    if not interactive:
        errors.print(
            Text(
                f"İzin gerekiyor, reddedildi: {event.call.name} "
                f"(önceden izin için: --allow {event.call.name})",
                style="yellow",
            )
        )
        return False
    try:
        choice = ask_permission(console, event.call)
    except EOFError:
        return False
    if choice == "always":
        agent.permissions.grant(event.call.name)
    return choice != "deny"


def _after_turn(events: list[Event], log: SessionLogger, quiet: bool) -> Path | None:
    if not quiet:
        items = summary_items(events)
        console.print(Text(" · ".join(f"{label}: {value}" for label, value in items)))
    try:
        report = write_report(log.path)
    except (OSError, ValueError) as exc:
        errors.print(Text(f"Rapor oluşturulamadı: {exc}", style="yellow"))
        return None
    if not quiet:
        console.print(Text(f"İnceleme: {report}"))
        console.print(Text(f"Oturum kaydı: {log.path}"))
    return report


# ---------------------------------------------------------------- sohbet


def run_chat(
    config: RunConfig,
    *,
    plain: bool,
    resume: bool,
    allow: list[str] | None = None,
) -> None:
    """Terminal etkileşimliyse tam ekran; değilse (boru, --plain) sade akış."""
    if not plain and sys.stdin.isatty() and sys.stdout.isatty():
        from harness.cli.tui import run_tui

        root = find_project_root(Path.cwd())

        def make(http: httpx.AsyncClient) -> ChatSession:
            session = chat_session(root, config, http)
            grant(session, allow)
            return session

        try:
            log_path = run_tui(make, resume=resume)
        except FAILURES as exc:
            errors.print(Text(f"Hata: {exc}", style="red"))
            raise typer.Exit(1) from exc
        if log_path.exists():
            console.print(Text(f"Oturum kaydı: {log_path}"))
        return
    root = start_session(config.label)
    console.print("/help: komutlar · /exit: çıkış · Ctrl+C: durdur")
    console.print("Kayıt ve rapor, istemler ve dosya içerikleri gibi hassas veri içerebilir.")

    async def run() -> None:
        async with httpx.AsyncClient(timeout=120) as http:
            session = chat_session(root, config, http)
            grant(session, allow)
            if resume:
                show_reply(session.resume_latest())
            await plain_loop(session)

    run_async(run)


async def plain_loop(session: ChatSession) -> None:
    report: Path | None = None
    while True:
        try:
            console.rule("[bold cyan]Sen", align="left")
            question = console.input("[bold cyan]> [/]").strip()
        except EOFError:
            break
        if not question:
            continue
        if is_command(question):
            reply = await session.run(question)
            if reply.exit:
                break
            if reply.inspect:
                if report:
                    typer.launch(report.resolve().as_uri())
                else:
                    console.print("Henüz rapor yok.")
            if reply.reset:
                report = None
            show_reply(reply)
            continue
        console.print()
        report = (await run_turn(session.agent, question, session.log)).report


def show_reply(reply: CommandReply) -> None:
    for role, text in reply.transcript:
        console.rule(f"[bold {'cyan' if role == 'Sen' else 'magenta'}]{role}", align="left")
        console.print(Markdown(text) if role == "Ajan" else Text(text))
    if reply.text:
        if reply.transcript:
            console.rule(style="dim")
        console.print(Markdown(reply.text))


@app.command(hidden=True)
def chat(
    provider: ProviderOption = None,
    model: ModelOption = None,
    effort: EffortOption = None,
    anatomy_name: AnatomyOption = None,
    allow: AllowOption = None,
    plain: PlainOption = False,
    continue_: ContinueOption = False,
) -> None:
    """Eski ad: `harness` ile aynı (etkileşimli sohbet)."""
    config = resolve_config(provider, model, effort, anatomy_name)
    run_chat(config, plain=plain, resume=continue_, allow=allow)


@app.command(hidden=True)
def ask(
    question: Annotated[str, typer.Argument(help="Modele sorulacak soru")],
    provider: ProviderOption = None,
    model: ModelOption = None,
    effort: EffortOption = None,
    anatomy_name: AnatomyOption = None,
    allow: AllowOption = None,
) -> None:
    """Eski ad: tek görev, konuşma biçiminde (-p'nin süslü hâli)."""
    config = resolve_config(provider, model, effort, anatomy_name)
    run_once(question, config, quiet=False, allow=allow)


# ---------------------------------------------------------------- kalıcı seçimler


def choose(title: str, rows: list[tuple[str, str]], default: int) -> int:
    """Numaralı bir liste gösterir, kullanıcının seçtiği satırın sırasını (0'dan) döner."""
    table = Table(title=title, title_justify="left", show_header=False, box=None)
    for number, (name, description) in enumerate(rows, start=1):
        marker = "●" if number - 1 == default else " "
        table.add_row(f"{marker} {number}", f"[bold]{name}[/]", f"[dim]{description}[/]")
    console.print(table)
    choices = [str(number) for number in range(1, len(rows) + 1)]
    number = IntPrompt.ask(
        "Numara", choices=choices, show_choices=False, default=default + 1, console=console
    )
    return number - 1


def fetch_models(provider: Provider) -> list[ModelInfo]:
    async def run() -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=30) as http:
            return await list_models(provider, http)

    return run_async(run)


def _pick[T](items: list[T], argument: str, names: Callable[[T], tuple[str, ...]]) -> T | None:
    if argument.isdigit():
        index = int(argument) - 1
        return items[index] if 0 <= index < len(items) else None
    wanted = argument.casefold()
    return next((i for i in items if wanted in (n.casefold() for n in names(i))), None)


def _current() -> tuple[Settings, Provider]:
    settings = load_settings()
    try:
        return settings, get_provider(settings.provider)
    except ProviderError as exc:
        errors.print(Text(f"Kayıtlı sağlayıcı geçersiz: {exc}", style="red"))
        raise typer.Exit(1) from exc


@app.command()
def provider(
    name: Annotated[str | None, typer.Argument(help="Sağlayıcı adı ya da numarası")] = None,
) -> None:
    """Varsayılan sağlayıcıyı seçer; model o sağlayıcıda son kullanılana döner."""
    settings, current = _current()
    providers = list(PROVIDERS.values())
    if name is None:
        rows = [(p.name, f"{p.label} · {readiness(p)[1]}") for p in providers]
        picked = providers[choose("Sağlayıcılar", rows, providers.index(current))]
    else:
        found = _pick(providers, name, lambda p: (p.name,))
        if found is None:
            errors.print(Text(f"'{name}' adlı sağlayıcı yok: {', '.join(PROVIDERS)}", style="red"))
            raise typer.Exit(1)
        picked = found
    models = {**settings.models, current.name: settings.model}
    model_name = models.get(picked.name, picked.default_model)
    effort = settings.effort if picked.name == current.name else None
    save_settings(
        settings.model_copy(
            update={
                "provider": picked.name,
                "model": model_name,
                "effort": effort,
                "models": models,
            }
        )
    )
    ready, detail = readiness(picked)
    console.print(f"[green]Kaydedildi:[/] {picked.name} · {model_name}")
    if not ready:
        console.print(Text(f"Uyarı: {detail}", style="yellow"))


@app.command()
def model(
    name: Annotated[str | None, typer.Argument(help="Model adı ya da numarası")] = None,
) -> None:
    """Varsayılan modeli seçer (yalnız model; düşünme seviyesi `harness effort`)."""
    settings, current = _current()
    models = fetch_models(current)
    if not models:
        errors.print(Text(f"{current.name} model listesi boş.", style="red"))
        raise typer.Exit(1)
    if name is None:
        slugs = [m.slug for m in models]
        index = slugs.index(settings.model) if settings.model in slugs else 0
        picked = models[
            choose(
                f"{current.name} modelleri (şu an: {settings.model})",
                [(m.display_name, m.description or m.slug) for m in models],
                index,
            )
        ]
    else:
        found = _pick(models, name, lambda m: (m.slug, m.display_name))
        if found is None:
            errors.print(Text(f"'{name}' adlı model yok. Liste: harness models", style="red"))
            raise typer.Exit(1)
        picked = found
    # Aynı effort yeni modelde varsa kalır; yoksa modelin kendi varsayılanına dönülür.
    keep = settings.effort in {e.name for e in picked.efforts}
    effort = settings.effort if keep else None
    save_settings(
        settings.model_copy(
            update={
                "model": picked.slug,
                "effort": effort,
                "models": {**settings.models, current.name: picked.slug},
            }
        )
    )
    console.print(
        f"[green]Kaydedildi:[/] {current.name} · {picked.slug} · effort: {effort or 'varsayılan'}"
    )


@app.command()
def effort(
    level: Annotated[
        str | None, typer.Argument(help="Seviye adı, numarası ya da 'varsayılan'")
    ] = None,
) -> None:
    """Varsayılan düşünme seviyesini (effort) seçer; model değişmez."""
    settings, current = _current()
    if level is not None and level.casefold() in DEFAULT_EFFORT_WORDS:
        save_settings(settings.model_copy(update={"effort": None}))
        console.print(f"[green]Kaydedildi:[/] {settings.model} · effort: varsayılan")
        return
    info = next((m for m in fetch_models(current) if m.slug == settings.model), None)
    if info is None or not info.efforts:
        errors.print(Text(f"{settings.model} için effort seviyeleri bilinmiyor.", style="red"))
        raise typer.Exit(1)
    names = [e.name for e in info.efforts]
    if level is None:
        rows = [("varsayılan", f"modelin kendi seçimi ({info.default_effort or '—'})")] + [
            (e.name, e.description + (" (önerilen)" if e.name == info.default_effort else ""))
            for e in info.efforts
        ]
        current_index = names.index(settings.effort) + 1 if settings.effort in names else 0
        index = choose(f"{info.display_name} ne kadar düşünsün?", rows, current_index)
        picked = None if index == 0 else names[index - 1]
    else:
        found = _pick(info.efforts, level, lambda e: (e.name,))
        if found is None:
            errors.print(Text(f"'{level}' yok. Seviyeler: {', '.join(names)}", style="red"))
            raise typer.Exit(1)
        picked = found.name
    save_settings(settings.model_copy(update={"effort": picked}))
    console.print(f"[green]Kaydedildi:[/] {settings.model} · effort: {picked or 'varsayılan'}")


@app.command()
def anatomy(
    name: Annotated[str | None, typer.Argument(help="Anatomi: raw, files, bash")] = None,
) -> None:
    """Varsayılan anatomiyi (açık katmanları) seçer."""
    settings = load_settings()
    presets = list(PRESETS.values())
    current = settings.anatomy or DEFAULT_PRESET
    if name is None:
        rows = [(a.name, ", ".join(a.tools) or "araç yok (seviye 0)") for a in presets]
        index = [a.name for a in presets].index(current) if current in PRESETS else 0
        picked = presets[choose("Anatomiler", rows, index)].name
    else:
        picked = pick_anatomy(name).name
    save_settings(settings.model_copy(update={"anatomy": picked}))
    console.print(f"[green]Kaydedildi:[/] anatomi: {picked}")


# ---------------------------------------------------------------- bilgi


@app.command()
def status() -> None:
    """Kayıtlı seçimler, sağlayıcıların hazırlığı, proje ve kayıt klasörü."""
    settings, current = _current()
    root = find_project_root(Path.cwd())
    table = Table(show_header=False, box=None)
    table.add_row("Sağlayıcı", f"{current.name} — {current.label}")
    table.add_row("Model", settings.model)
    table.add_row("Effort", settings.effort or "varsayılan")
    table.add_row("Anatomi", settings.anatomy or DEFAULT_PRESET)
    table.add_row("Proje", str(root))
    table.add_row("Kayıtlar", str(sessions_dir(root)))
    console.print(table)
    console.print()
    providers_command()


@app.command(name="providers")
def providers_command() -> None:
    """Sağlayıcılar ve hazır olup olmadıkları (anahtar/giriş)."""
    current = load_settings().provider
    local = [p for p in PROVIDERS.values() if p.local]

    async def probe() -> dict[str, bool]:
        async with httpx.AsyncClient() as http:
            results = await asyncio.gather(*(reachable(p, http) for p in local))
        return {p.name: ok for p, ok in zip(local, results, strict=True)}

    alive = asyncio.run(probe())
    table = Table(title="Sağlayıcılar", title_justify="left")
    table.add_column("")
    table.add_column("Ad")
    table.add_column("Sağlayıcı")
    table.add_column("Varsayılan model")
    table.add_column("Durum")
    for p in PROVIDERS.values():
        ready, detail = readiness(p)
        if p.local:
            ready = alive[p.name]
            detail = f"{'çalışıyor' if ready else 'kapalı'}: {p.url(os.environ)}"
        table.add_row(
            "●" if p.name == current else "",
            p.name,
            p.label,
            p.default_model,
            Text(("✓ " if ready else "✗ ") + detail, style="green" if ready else "yellow"),
        )
    console.print(table)


@app.command()
def models(
    provider_name: Annotated[
        str | None, typer.Option("--provider", help="Hangi sağlayıcının modelleri")
    ] = None,
) -> None:
    """Sağlayıcının sunduğu modeller ve effort seviyeleri."""
    settings, current = _current()
    if provider_name:
        try:
            current = get_provider(provider_name)
        except ProviderError as exc:
            raise typer.BadParameter(str(exc), param_hint="--provider") from exc
    table = Table(title=f"{current.name} modelleri", title_justify="left")
    table.add_column("No")
    table.add_column("Ad")
    table.add_column("Model")
    table.add_column("Effort")
    for i, m in enumerate(fetch_models(current), start=1):
        mark = "●" if m.slug == settings.model and current.name == settings.provider else ""
        table.add_row(
            f"{mark} {i}",
            m.slug,
            m.display_name,
            ", ".join(e.name for e in m.efforts) or "—",
        )
    console.print(table)


@app.command()
def sessions() -> None:
    """Bu projenin oturumları; son oturuma devam: harness -c."""
    root = find_project_root(Path.cwd())
    folder = sessions_dir(root)
    found = list_sessions(folder) if folder.is_dir() else []
    if not found:
        console.print("Bu projede kayıtlı oturum yok.")
        return
    table = Table(title=f"Oturumlar · {root}", title_justify="left")
    table.add_column("No")
    table.add_column("Son kullanım")
    table.add_column("Tur")
    table.add_column("İlk görev")
    for i, s in enumerate(found[:20], start=1):
        task = s.first_task.replace("\n", " ")
        table.add_row(str(i), f"{s.updated:%d.%m %H:%M}", str(s.turns), task[:70])
    console.print(table)
    console.print(Text("Son oturuma devam: harness -c · ayrıntı: harness inspect <kayıt>"))


# ---------------------------------------------------------------- giriş


@app.command(name="login")
def login_command() -> None:
    """ChatGPT hesabıyla giriş: tarayıcı açılır, izin verilince belirteç kaydedilir."""
    console.print("Tarayıcıda ChatGPT girişi açılıyor…")
    try:
        credentials = login(
            on_url=lambda url: console.print(
                f"Tarayıcı açılamadı; bu adresi tarayıcıya yapıştırın:\n{url}\n"
            )
        )
    except AuthError as e:
        console.print(f"[red]Giriş başarısız:[/] {e}")
        raise typer.Exit(1) from e
    console.print(f"[green]Giriş tamam.[/] Hesap: {credentials.email or '(e-posta yok)'}")


@app.command()
def logout() -> None:
    """ChatGPT girişini bu bilgisayardan siler."""
    if CHATGPT_CREDENTIALS.exists():
        CHATGPT_CREDENTIALS.unlink()
        console.print("Çıkış yapıldı; giriş bilgisi silindi.")
    else:
        console.print("Zaten giriş yok.")


# ---------------------------------------------------------------- inceleme ve eğitim


@app.command()
def demo(
    delay: Annotated[float, typer.Option(help="Olaylar arası bekleme (saniye)")] = 0.8,
    tui: Annotated[bool, typer.Option("--tui", help="Hazır senaryoyu tam ekran göster")] = False,
) -> None:
    """Sahte modelle hazır bir görevi çalıştırır: döngü, araç, hata ve kayıt bir arada."""
    root = start_session(DEMO_PROVIDER.label)

    async def no_models(provider: Provider) -> list[ModelInfo]:
        raise RuntimeError("demo sahte modelle çalışır")

    session = ChatSession(
        root=root,
        anatomy=get_preset("files"),  # senaryo read_file çağırır
        provider=DEMO_PROVIDER,
        model=DEMO_PROVIDER.label,
        effort=None,
        make_llm=lambda provider, model, effort: ScriptedLLM(DEMO_SCRIPT),
        load_models=no_models,
        load_prompt=load_system_prompt,
        sessions_dir=sessions_dir(root),
    )
    if tui:
        from harness.cli.tui import HarnessApp

        HarnessApp(session, initial_task=DEMO_TASK).run()
        return
    agent, log = session.agent, session.log

    async def run() -> None:
        stream = agent.run(DEMO_TASK)
        send: bool | None = None
        try:
            while True:
                event = await stream.asend(send)
                log.write(event)
                render(console, event)
                send = None
                if isinstance(event, PermissionRequested):
                    send = _permission(agent, event, interactive=True)
                await asyncio.sleep(delay)
        except StopAsyncIteration:
            pass

    asyncio.run(run())
    console.print(Text(f"\nOturum kaydı: {log.path}"))
    console.print(Text(f"İnceleme: {write_report(log.path)}"))


@app.command()
def replay(path: Annotated[Path, typer.Argument(help="Oturum kaydı (.jsonl)")]) -> None:
    """Kaydedilmiş bir oturumu, canlıdaki aynı ekranla yeniden oynatır."""
    events = read_session(path)
    completed_steps: set[tuple[int, int]] = set()
    turn_index = 0
    for event in events:
        if isinstance(event, TaskStarted):
            turn_index += 1
        elif isinstance(event, ModelReplied):
            completed_steps.add((turn_index, event.step))

    turn_index = 0
    partial_step: tuple[int, int] | None = None

    def close_partial() -> None:
        nonlocal partial_step
        if partial_step is not None:
            console.print()
            console.print(Text("Yanıt tamamlanmadı.", style="yellow"))
            partial_step = None

    for event in events:
        if isinstance(event, TaskStarted):
            close_partial()
            turn_index += 1
        if isinstance(event, ModelTextDelta):
            key = (turn_index, event.step)
            if partial_step is not None and partial_step != key:
                close_partial()
            if key in completed_steps or not event.text:
                continue
            if partial_step is None:
                console.rule(
                    f"[bold yellow]Ajan · adım {event.step} · kısmi yanıt[/]", align="left"
                )
                partial_step = key
            console.print(Text(event.text), end="", soft_wrap=True)
            continue
        close_partial()
        render(console, event)
    close_partial()


@app.command(name="inspect")
def inspect_session(
    path: Annotated[Path, typer.Argument(help="İncelenecek oturum kaydı (.jsonl)")],
    output: Annotated[Path | None, typer.Option(help="Yeni HTML dosyası (var olanı ezmez)")] = None,
    open_browser: Annotated[bool, typer.Option("--open", help="Tarayıcıda da aç")] = False,
) -> None:
    """Eski veya yeni bir JSONL kaydından çevrimdışı inceleme ekranı üretir."""
    try:
        report = write_report(path, output)
    except (OSError, ValueError) as exc:
        console.print(Text(f"İnceleme açılamadı: {exc}", style="red"))
        raise typer.Exit(1) from exc
    console.print(Text(f"İnceleme: {report}"))
    if open_browser:
        typer.launch(report.resolve().as_uri())


def main() -> None:
    # Windows'ta çıktı boruya/dosyaya giderken Türkçe harfler bozulmasın.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    app()
