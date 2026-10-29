"""Giriş noktası: komut satırı (Typer).

Alışılmış kodlama ajanı CLI'larının biçimi:

    harness                  etkileşimli sohbet (terminalde tam ekran, boruda sade)
    harness -p "soru"        tek seferlik görev; yalnız cevap yazılır (betiklerde kullanılır)
    harness -c               bu projenin son oturumuna devam
    harness -m sağlayıcı/model   bu koşu için başka model (ör. opencode-go/glm-5.3)
    harness models [sağlayıcı]   modeller ve effort seviyeleri
    harness sessions | login | logout | inspect | replay | demo

Sağlayıcı bağlama, model/effort/anatomi seçimi sohbetin içindedir (`/connect`,
`/model`, `/effort`, `/anatomy`) ve kalıcıdır. `ask` ve `chat` eski adlarıyla da çalışır.
"""

import asyncio
import re
import secrets
import sys
import threading
from collections.abc import Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from rich.theme import ThemeStackError

from harness.cli.commands import (
    DEFAULT_EFFORT_WORDS,
    STEP_LIMIT_HINT,
    ChatSession,
    Choice,
    CommandReply,
    Picker,
    is_command,
)
from harness.cli.eval_report import result_line, summary_table, task_table
from harness.cli.inspector import (
    code_blocks_summary,
    intervention_summary,
    summary_items,
    write_report,
)
from harness.cli.render import ask_permission, live_outcome, render
from harness.cli.themes import palette_for, rich_theme
from harness.cli.watch import WatchServer
from harness.core.agent import Agent
from harness.core.anatomy import DEFAULT_PRESET, PRESETS, Anatomy, get_preset
from harness.core.config import (
    DEFAULT_SESSIONS_HOME,
    HARNESS_HOME,
    find_project_root,
    load_settings,
    project_sessions_dir,
    save_settings,
)
from harness.core.evaluation import (
    TaskResult,
    Variant,
    find_tasks,
    read_results,
    run_matrix,
    summarize,
)
from harness.core.events import (
    CodeBlocksSaved,
    Event,
    HarnessIntervened,
    ModelReasoningDelta,
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
from harness.llm.base import LLMClient, LLMError, ModelInfo
from harness.llm.chatgpt_auth import DEFAULT_PATH as CHATGPT_CREDENTIALS
from harness.llm.chatgpt_auth import AuthError, login
from harness.llm.providers import (
    PROVIDERS,
    Provider,
    ProviderError,
    get_provider,
    list_models,
    make_llm,
    readiness,
)
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall
from harness.tools.base import short

app = typer.Typer(
    help="Şeffaf kodlama ajanı harness'i: içi görünen, katmanları açılıp kapanan ajan.",
    no_args_is_help=False,
)
console = Console(theme=rich_theme(palette_for(None)))
errors = Console(stderr=True, theme=rich_theme(palette_for(None)))


def use_theme(name: str | None) -> None:
    """Sade akışın renkleri; NO_COLOR tanımlıysa Rich zaten renksiz yazar."""
    for out in (console, errors):
        with suppress(ThemeStackError):
            out.pop_theme()  # önceki seçim; temel tema yerinde kalır
        out.push_theme(rich_theme(palette_for(name)))


_PROMPT_DIR = Path(__file__).parent.parent / "prompts"
_PROMPT_FILES = {"agent": "system.md", "raw": "raw.md"}
SESSIONS_HOME = DEFAULT_SESSIONS_HOME
EVALS_HOME = HARNESS_HOME / "evals"
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

ModelOption = Annotated[
    str | None,
    typer.Option(
        "--model",
        "-m",
        help="Bu koşunun modeli: sağlayıcı/model (ör. opencode-go/glm-5.3) ya da yalnız model",
    ),
]
EffortOption = Annotated[str | None, typer.Option("--effort", help="Bu koşunun düşünme seviyesi")]
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

    @property
    def label(self) -> str:
        return f"{self.provider.name} · {self.model} · effort: {self.effort or 'varsayılan'}"


def resolve_config(
    model: str | None,
    effort: str | None,
    anatomy: str | None,
) -> RunConfig:
    """Bayrak verilmeyen her seçim kayıttan gelir; bayraklar yalnız bu koşu içindir.

    `-m` sağlayıcı adıyla başlıyorsa (`opencode-go/glm-5.3`) sağlayıcı da değişir;
    OpenRouter'ın kendi `/`'lu adları için tam yol yazılır: `openrouter/openai/gpt-oss-120b`.
    """
    settings = load_settings()
    provider_name, model_name = settings.provider, settings.model
    if model:
        head, separator, rest = model.partition("/")
        if separator and head in PROVIDERS and rest:
            provider_name, model_name = head, rest
        else:
            model_name = model
    try:
        picked_provider = get_provider(provider_name)
    except ProviderError as exc:
        raise typer.BadParameter(str(exc), param_hint="--model") from exc
    if effort:
        picked_effort = None if effort.casefold() in DEFAULT_EFFORT_WORDS else effort
    elif (provider_name, model_name) == (settings.provider, settings.model):
        picked_effort = settings.effort
    else:
        picked_effort = None  # başka model: kayıtlı effort onda olmayabilir
    return RunConfig(
        provider=picked_provider,
        model=model_name,
        effort=picked_effort,
        anatomy=pick_anatomy(anatomy or settings.anatomy or DEFAULT_PRESET),
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
    console.print(Text(f"{label} · Çalışma dizini: {root}", style="h.muted"))
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
        save_choice=remember,
        login=chatgpt_login,
        open_url=open_in_browser,
        theme=load_settings().theme,
    )


def open_in_browser(url: str) -> None:
    typer.launch(url)


def remember(session: ChatSession) -> None:
    """Sohbette yapılan seçim kalıcıdır: bir sonraki `harness` aynı seçimlerle açılır."""
    save_settings(
        load_settings().model_copy(
            update={
                "provider": session.provider.name,
                "model": session.model,
                "effort": session.effort,
                "anatomy": session.anatomy.name,
                "theme": session.theme,
            }
        )
    )


async def chatgpt_login(on_url: Callable[[str], None]) -> str | None:
    """Tarayıcı girişi yerel sunucuda bekler; sohbet donmasın diye ayrı iş parçacığında."""
    credentials = await asyncio.to_thread(login, on_url=on_url)
    return credentials.email


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
        errors.print(Text(f"Hata: {exc}", style="h.error"))
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
    use_theme(load_settings().theme)
    if ctx.invoked_subcommand is not None:
        return
    config = resolve_config(model, effort, anatomy_name)
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
        console.rule("[h.user]Sen", align="left")
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
    thought_open = False  # düşünce özeti akıyor; sonraki çıktı yeni satırdan başlar
    thought_seen = False
    finish: TaskFinished | None = None

    def close_thought() -> None:
        nonlocal thought_open
        if thought_open:
            console.print()
            thought_open = False

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
                if not quiet and not isinstance(event, ModelReasoningDelta):
                    close_thought()
                if isinstance(event, PermissionRequested):
                    status.stop()
                    send = _permission(agent, event, interactive=not quiet)
                elif quiet:
                    if isinstance(event, ModelReplied) and not event.tool_calls and event.text:
                        console.print(event.text, markup=False, highlight=False, soft_wrap=True)
                elif isinstance(event, ModelStarted):
                    streamed_text = ""
                    thought_seen = False
                    status.start()
                    status.update("Model bekleniyor…")
                elif isinstance(event, ToolStarted):
                    status.start()
                    status.update(f"{event.call.name} çalışıyor…")
                elif isinstance(event, ToolFinished):
                    # Araç çıktısı gizli kalır; ne yapıldığı ve sonucu tek satırda görünür.
                    status.stop()
                    console.print(tool_trace(event), soft_wrap=True)
                elif isinstance(event, ModelReasoningDelta) and event.text:
                    status.stop()
                    if not thought_open:
                        console.print(Text("düşünce · ", style="h.muted"), end="")
                        thought_open = thought_seen = True
                    console.print(Text(event.text, style="h.thought"), end="", soft_wrap=True)
                elif isinstance(event, ModelTextDelta) and event.text:
                    status.stop()
                    if not stream_open:
                        console.rule("[h.agent]Ajan", align="left")
                        stream_open = True
                    console.print(Text(event.text), end="", soft_wrap=True)
                    streamed_text += event.text
                elif isinstance(event, CodeBlocksSaved):
                    status.stop()
                    console.print(Text(code_blocks_summary(event), style="h.muted"))
                elif isinstance(event, HarnessIntervened):
                    status.stop()
                    console.print(
                        Text(f"harness · {intervention_summary(event)}", style="h.harness")
                    )
                elif isinstance(event, ModelReplied):
                    if event.reasoning and not thought_seen:
                        # Akış vermeyen adaptör: özet tamamlanmış yanıtla gelir.
                        status.stop()
                        console.print(Text("düşünce · ", style="h.muted"), end="")
                        console.print(Text(event.reasoning, style="h.thought"), soft_wrap=True)
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
                        console.rule("[h.agent]Ajan", align="left")
                        console.print(Markdown(event.text))
    finally:
        if stream_open:
            console.print()
            console.print("[yellow]Yanıt tamamlanmadı.[/]")
        if events:
            report = _after_turn(events, log, quiet)
    return TurnOutcome(finish, report)


def tool_trace(event: ToolFinished) -> Text:
    return Text(f"  {live_outcome(event)}", style="h.warn" if event.result.is_error else "h.muted")


def _permission(agent: Agent, event: PermissionRequested, *, interactive: bool) -> bool:
    """İzin sorusu. Soru sorulamayan modda (-p) güvenli taraf seçilir: reddedilir."""
    if not interactive:
        errors.print(
            Text(
                f"İzin gerekiyor, reddedildi: {event.call.name} "
                f"(önceden izin için: --allow {event.call.name})",
                style="h.warn",
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
        if any(isinstance(e, TaskFinished) and e.reason == "step_limit" for e in events):
            console.print(Text(STEP_LIMIT_HINT, style="h.warn"))
    try:
        report = write_report(log.path)
    except (OSError, ValueError) as exc:
        errors.print(Text(f"Rapor oluşturulamadı: {exc}", style="h.warn"))
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
            errors.print(Text(f"Hata: {exc}", style="h.error"))
            raise typer.Exit(1) from exc
        if log_path.exists():
            console.print(Text(f"Oturum kaydı: {log_path}"))
        return
    root = start_session(config.label)
    console.print("/model: model seç · /connect: sağlayıcı bağla · /help: komutlar · /exit: çıkış")
    console.print("Kayıt ve rapor, istemler ve dosya içerikleri gibi hassas veri içerebilir.")

    async def run() -> None:
        async with httpx.AsyncClient(timeout=120) as http:
            session = chat_session(root, config, http)
            grant(session, allow)
            if resume:
                show_reply(session.resume_latest())
            try:
                await plain_loop(session)
            finally:
                session.close()

    run_async(run)


async def plain_loop(session: ChatSession) -> None:
    report: Path | None = None
    while True:
        try:
            console.rule("[h.user]Sen", align="left")
            question = console.input("[h.user]> [/]").strip()
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
            step: CommandReply | None = reply
            while step is not None:  # liste seçimi bir sonrakini açabilir (model → effort)
                if step.reset:
                    report = None
                show_reply(step)
                step = await next_step(session, step)
            continue
        console.print()
        report = (await run_turn(session.agent, question, session.log)).report


async def next_step(session: ChatSession, reply: CommandReply) -> CommandReply | None:
    """Sade akışta komutun sıradaki adımı: liste numarayla seçilir, anahtar gizli girilir."""
    try:
        if reply.picker is not None:
            choice = pick(reply.picker)
            if choice is None:
                return None
            return await session.choose(choice.kind or reply.picker.kind, choice.value)
        if reply.key is not None:
            console.print(Text(reply.key.prompt, style="h.muted"))
            # Terminalde yazılan görünmez; boruda (test, betik) zaten yankı yoktur.
            key = console.input("Anahtar (boş: vazgeç): ", password=sys.stdin.isatty())
            return await session.save_key(reply.key.provider, key) if key.strip() else None
        if reply.login:
            return await session.login(
                on_url=lambda url: console.print(
                    f"Tarayıcı açılamadı; bu adresi tarayıcıya yapıştır:\n{url}"
                )
            )
    except EOFError:
        return None
    return None


def pick(picker: Picker) -> Choice | None:
    """Listeyi numaralı tablo olarak gösterir; seçilemeyen satırlar numarasızdır."""
    table = Table(title=picker.title, title_justify="left", show_header=False, box=None)
    numbered: list[Choice] = []
    group = ""
    for choice in picker.choices:
        if choice.group and choice.group != group:
            group = choice.group
            table.add_row("", Text(group, style="bold"), "")
        number = ""
        if choice.enabled:
            numbered.append(choice)
            number = str(len(numbered))
        mark = "●" if choice.current else " "
        table.add_row(f"{mark} {number}", Text(choice.label), Text(choice.detail, style="h.muted"))
    console.print(table)
    while True:
        answer = console.input("Numara (boş: vazgeç): ").strip()
        if not answer:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(numbered):
            return numbered[int(answer) - 1]
        console.print("Listede böyle bir numara yok.")


def show_reply(reply: CommandReply) -> None:
    if reply.theme is not None:
        use_theme(reply.theme)
    for role, text in reply.transcript:
        console.rule(f"[{'h.user' if role == 'Sen' else 'h.agent'}]{role}", align="left")
        console.print(Markdown(text) if role == "Ajan" else Text(text))
    if reply.text:
        if reply.transcript:
            console.rule(style="h.muted")
        console.print(Markdown(reply.text))


@app.command(hidden=True)
def chat(
    model: ModelOption = None,
    effort: EffortOption = None,
    anatomy_name: AnatomyOption = None,
    allow: AllowOption = None,
    plain: PlainOption = False,
    continue_: ContinueOption = False,
) -> None:
    """Eski ad: `harness` ile aynı (etkileşimli sohbet)."""
    config = resolve_config(model, effort, anatomy_name)
    run_chat(config, plain=plain, resume=continue_, allow=allow)


@app.command(hidden=True)
def ask(
    question: Annotated[str, typer.Argument(help="Modele sorulacak soru")],
    model: ModelOption = None,
    effort: EffortOption = None,
    anatomy_name: AnatomyOption = None,
    allow: AllowOption = None,
) -> None:
    """Eski ad: tek görev, konuşma biçiminde (-p'nin süslü hâli)."""
    config = resolve_config(model, effort, anatomy_name)
    run_once(question, config, quiet=False, allow=allow)


# ---------------------------------------------------------------- bilgi


def fetch_models(provider: Provider) -> list[ModelInfo]:
    async def run() -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=30) as http:
            return await list_models(provider, http)

    return run_async(run)


@app.command()
def models(
    provider_name: Annotated[
        str | None, typer.Argument(help="Sağlayıcı (verilmezse kayıtlı olan)")
    ] = None,
) -> None:
    """Sağlayıcının sunduğu modeller ve effort seviyeleri (betikler için; sohbette /model)."""
    settings = load_settings()
    try:
        current = get_provider(provider_name or settings.provider)
    except ProviderError as exc:
        raise typer.BadParameter(str(exc), param_hint="sağlayıcı") from exc
    table = Table(title=f"{current.name} modelleri", title_justify="left")
    table.add_column("Model")
    table.add_column("Ad")
    table.add_column("Effort")
    found = fetch_models(current)
    notes = any(m.description for m in found)
    if notes:
        table.add_column("Not")  # ör. OpenCode'da modelin API biçimi, "ücretsiz"
    for m in found:
        mark = "● " if m.slug == settings.model and current.name == settings.provider else ""
        row = [f"{mark}{current.name}/{m.slug}", m.display_name]
        row.append(", ".join(e.name for e in m.efforts) or "—")
        table.add_row(*row, *([short(m.description, 40)] if notes else []))
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
            console.print(Text("Yanıt tamamlanmadı.", style="h.warn"))
            partial_step = None

    for event in events:
        if isinstance(event, TaskStarted):
            close_partial()
            turn_index += 1
        if isinstance(event, ModelReasoningDelta):
            continue  # düşüncenin tamamı ModelReplied ile çizilir
        if isinstance(event, ModelTextDelta):
            key = (turn_index, event.step)
            if partial_step is not None and partial_step != key:
                close_partial()
            if key in completed_steps or not event.text:
                continue
            if partial_step is None:
                console.rule(
                    f"[h.warn.strong]Ajan · adım {event.step} · kısmi yanıt[/]", align="left"
                )
                partial_step = key
            console.print(Text(event.text), end="", soft_wrap=True)
            continue
        close_partial()
        render(console, event)
    close_partial()


# ---------------------------------------------------------------- deney


def show_eval(results: list[TaskResult]) -> None:
    console.print()
    console.print(summary_table(summarize(results)))
    if len({r.task_id for r in results}) > 1 or len({r.variant for r in results}) > 1:
        console.print(task_table(results))


@app.command(name="eval")
def eval_command(
    tasks_dir: Annotated[
        Path, typer.Argument(help="Görev klasörü (her alt klasör: task.md, repo/, tests/)")
    ] = Path("tasks"),
    models: Annotated[
        list[str] | None,
        typer.Option("--model", "-m", help="Varyant modeli: sağlayıcı/model (tekrarlanabilir)"),
    ] = None,
    anatomies: Annotated[
        list[str] | None,
        typer.Option(
            "--anatomy", help=f"Varyant anatomisi (tekrarlanabilir): {', '.join(PRESETS)}"
        ),
    ] = None,
    effort: EffortOption = None,
    repeat: Annotated[int, typer.Option("--repeat", "-n", min=1, help="Her koşu kaç kez")] = 1,
    max_steps: Annotated[
        int, typer.Option("--max-steps", min=1, help="Koşu başı adım sınırı")
    ] = 20,
    only: Annotated[
        list[str] | None, typer.Option("--only", help="Yalnız adı bununla başlayan görevler")
    ] = None,
    report: Annotated[
        Path | None, typer.Option("--report", help="Koşmadan, var olan deneyin özetini göster")
    ] = None,
    watch_live: Annotated[
        bool, typer.Option("--watch", help="Koşuları canlı panelde (tarayıcı) izle")
    ] = False,
) -> None:
    """Deney: görevler × (model × anatomi) × tekrar; gizli testlerle puanlanır.

    Örnek: harness eval -m opencode-go/glm-5.3 --anatomy files --anatomy bash -n 3
    """
    if report is not None:
        try:
            show_eval(read_results(report))
        except (OSError, ValueError, TypeError) as exc:
            console.print(Text(f"Deney okunamadı: {exc}", style="h.error"))
            raise typer.Exit(1) from exc
        return
    if not tasks_dir.is_dir():
        raise typer.BadParameter(f"{tasks_dir} klasör değil.", param_hint="görev klasörü")
    tasks = find_tasks(tasks_dir, only or ())
    if not tasks:
        raise typer.BadParameter(f"{tasks_dir} altında task.md içeren görev yok.")
    configs = [
        resolve_config(model, effort, anatomy)
        for model in (models or [None])
        for anatomy in (anatomies or [None])
    ]
    for config in configs:
        ready, why = readiness(config.provider)
        if not ready:
            console.print(Text(f"{config.provider.name} hazır değil: {why}", style="h.error"))
            raise typer.Exit(1)
    out_dir = EVALS_HOME / f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
    total = len(tasks) * len(configs) * repeat
    console.print(
        Text(
            f"{len(tasks)} görev × {len(configs)} varyant × {repeat} tekrar = {total} koşu · "
            f"kayıt: {out_dir}",
            style="h.muted",
        )
    )

    panel = WatchServer(out_dir) if watch_live else None
    if panel is not None:
        url = panel.start()
        console.print(Text(f"Canlı panel: {url}", style="h.muted"))
        open_in_browser(url)

    async def run() -> list[TaskResult]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15)) as http:
            variants = [
                Variant(
                    label=f"{c.provider.name}/{c.model} · {c.anatomy.name}",
                    anatomy=c.anatomy,
                    make_llm=lambda c=c: make_llm(c.provider, c.model, c.effort, http),
                    system_prompt=load_system_prompt(c.anatomy),
                    provider=c.provider.name,
                    model=c.model,
                    effort=c.effort,
                )
                for c in configs
            ]
            return await run_matrix(
                tasks,
                variants,
                out_dir,
                repeats=repeat,
                max_steps=max_steps,
                on_result=lambda r: console.print(result_line(r)),
            )

    try:
        results = run_async(run)
    except KeyboardInterrupt:
        console.print(Text("Deney yarıda kesildi; biten koşular kayıtlı.", style="h.warn"))
        results = read_results(out_dir) if (out_dir / "results.jsonl").exists() else []
    if results:
        show_eval(results)
    console.print(
        Text(f"Sonuçlar: {out_dir} · yeniden özet: harness eval --report <klasör>", style="h.muted")
    )


@app.command(name="watch")
def watch_command(
    folder: Annotated[
        Path | None,
        typer.Argument(
            help="Kayıt klasörü ya da tek kayıt (.jsonl: haritada tekrar oynatılır); "
            "verilmezse bu projenin oturumları"
        ),
    ] = None,
    evals: Annotated[bool, typer.Option("--evals", help="Deney kayıtlarını izle")] = False,
    port: Annotated[int, typer.Option(help="Yerel port (0: boş bir port seçilir)")] = 0,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Tarayıcıda aç")] = True,
) -> None:
    """Canlı panel: kayıtları tarayıcıda akarken gösterir; en yeni kayda kendiliğinden geçer.

    Tek bir kayıt verilirse o kayıt haritada açılır: video gibi geri sarılır.
    """
    log = folder if folder is not None and folder.is_file() else None
    if log is not None and log.suffix != ".jsonl":
        raise typer.BadParameter(f"{log} bir oturum kaydı (.jsonl) değil.", param_hint="kayıt")
    target = (
        log.parent
        if log is not None
        else folder or (EVALS_HOME if evals else sessions_dir(find_project_root(Path.cwd())))
    )
    panel = WatchServer(target)
    try:
        panel.start(port)
    except OSError as exc:
        console.print(Text(f"Canlı panel açılamadı: {exc}", style="h.error"))
        raise typer.Exit(1) from exc
    url = panel.url_for(log) + "&view=map" if log is not None else panel.url_for(None)
    console.print(f"Canlı panel: {url} · izlenen: {target} · kapatmak için Ctrl+C")
    if open_browser:
        open_in_browser(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        panel.close()


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Dinlenecek adres")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Dinlenecek port")] = 8765,
    demo: Annotated[
        bool, typer.Option("--demo", help="Sahte modelle, giriş gerektirmeden dener")
    ] = False,
    model: ModelOption = None,
    effort: EffortOption = None,
    anatomy_name: AnatomyOption = None,
    ollama: Annotated[
        bool, typer.Option("--ollama", hidden=True, help="Eski ad: -m ollama/<model> ile aynı")
    ] = False,
) -> None:
    """Tarayıcıda sohbet: tek sayfa + WebSocket, aynı çekirdek döngüsü üzerinden.

    Sağlayıcı ve model sohbetteki gibi seçilir (-m sağlayıcı/model, verilmezse kayıtlı
    seçim). Her bağlantı bu projenin oturum kaydına yazılır: canlı panel ve harita
    tarayıcı sohbetlerini de gösterir.
    """
    if ollama and not (model or "").startswith("ollama/"):
        model = f"ollama/{model or 'llama3.2'}"
    config = resolve_config(model, effort, anatomy_name)
    if not demo:
        ready, why = readiness(config.provider)
        if not ready:
            console.print(Text(f"{config.provider.name} hazır değil: {why}", style="h.error"))
            raise typer.Exit(1)
    root = start_session("web · sahte model" if demo else f"web · {config.label}")
    prompt = load_system_prompt(config.anatomy)

    async def run() -> None:
        import uvicorn

        from harness.cli.web import create_app

        async with httpx.AsyncClient(timeout=120) as http:

            def llm_factory() -> LLMClient:
                if demo:
                    return ScriptedLLM(DEMO_SCRIPT)
                return make_llm(config.provider, config.model, config.effort, http)

            web_app = create_app(
                config.anatomy,
                root,
                prompt,
                llm_factory,
                log_dir=sessions_dir(root),
                provider=DEMO_PROVIDER.name if demo else config.provider.name,
                model=None if demo else config.model,
                effort=None if demo else config.effort,
            )
            console.print(Text.assemble(("Tarayıcıda aç: ", "h.ok"), f"http://{host}:{port}"))
            uvicorn_config = uvicorn.Config(web_app, host=host, port=port, log_level="warning")
            await uvicorn.Server(uvicorn_config).serve()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("Durduruldu.")


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
        console.print(Text(f"İnceleme açılamadı: {exc}", style="h.error"))
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
