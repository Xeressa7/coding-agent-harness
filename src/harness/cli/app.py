"""Giriş noktası: komut satırı parametreleri, tek seferlik görev ve etkileşimli oturum (Typer)."""

import asyncio
import re
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.prompt import IntPrompt
from rich.table import Table
from rich.text import Text

from harness.cli.inspector import summary_items, write_report
from harness.cli.render import ask_permission, render
from harness.core.agent import Agent
from harness.core.anatomy import DEFAULT_PRESET, PRESETS, Anatomy, get_preset
from harness.core.config import Settings, find_project_root, load_settings, save_settings
from harness.core.events import (
    Event,
    ModelReplied,
    ModelStarted,
    ModelTextDelta,
    PermissionRequested,
    TaskStarted,
    ToolStarted,
)
from harness.core.permissions import PermissionManager
from harness.core.session_log import SessionLogger, read_session
from harness.llm.base import LLMClient
from harness.llm.chat_completions import ChatCompletionsLLM
from harness.llm.chatgpt_auth import AuthError, ChatGPTTokens, login
from harness.llm.responses import ModelInfo, ResponsesError, ResponsesLLM, list_models
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall

app = typer.Typer(help="LLM tabanlı coding agent harness'i.", no_args_is_help=True)
console = Console()

_PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "system.md"

DEMO_TASK = "Bu proje hangi Python sürümünü istiyor, bağımlılıkları neler?"

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


AnatomyOption = Annotated[
    str, typer.Option("--anatomy", help=f"Açık katmanlar: {', '.join(PRESETS)}")
]


def pick_anatomy(name: str) -> Anatomy:
    try:
        return get_preset(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--anatomy") from exc


def load_system_prompt() -> str:
    """system.md'yi okur; dosyanın başındaki açıklama yorumu modele gitmez."""
    text = _PROMPT_FILE.read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()


def start_session(model_label: str) -> Path:
    """Çalışma dizinini bulur; modeli ve dizini yazar: kullanıcı neyle çalıştığını bilmeli."""
    root = find_project_root(Path.cwd())
    console.print(f"[dim]Model: {model_label} · Çalışma dizini: {root}[/]")
    return root


def new_session_log(root: Path) -> SessionLogger:
    return SessionLogger(root / "sessions" / f"{datetime.now():%Y%m%d-%H%M%S-%f}.jsonl")


@app.command()
def demo(
    delay: Annotated[float, typer.Option(help="Olaylar arası bekleme (saniye)")] = 0.8,
    tui: Annotated[bool, typer.Option("--tui", help="Hazır senaryoyu tam ekran göster")] = False,
) -> None:
    """Sahte modelle hazır bir görevi çalıştırır: döngü, araç, hata ve kayıt bir arada."""
    root = start_session("sahte model (hazır senaryo)")
    anatomy = get_preset("files")  # senaryo read_file çağırır
    tools = anatomy.tool_registry(root)
    agent = Agent(
        llm=ScriptedLLM(DEMO_SCRIPT),
        tools=tools,
        system_prompt=load_system_prompt(),
        permissions=PermissionManager(root, tools),
        model_label="sahte model (hazır senaryo)",
        anatomy=anatomy,
    )
    log = new_session_log(root)
    if tui:
        from harness.cli.tui import HarnessApp

        screen = HarnessApp(
            agent, log, model_label="Sahte model · hazır senaryo", root=root, initial_task=DEMO_TASK
        )
        screen.run()
        return

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
                    choice = ask_permission(console, event.call)
                    if choice == "always":
                        agent.permissions.grant(event.call.name)
                    send = choice != "deny"
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


def fetch_models() -> list[ModelInfo]:
    async def run() -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=30) as http:
            return await list_models(ChatGPTTokens(http), http)

    return asyncio.run(run())


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


@app.command()
def model() -> None:
    """Model ve effort seçer; seçim kaydedilir, sonraki her görevde kullanılır."""
    settings = load_settings()
    try:
        models = fetch_models()
    except (AuthError, ResponsesError) as e:
        console.print(f"[red]Hata:[/] {e}")
        raise typer.Exit(1) from e

    slugs = [m.slug for m in models]
    current = slugs.index(settings.model) if settings.model in slugs else 0
    picked = models[
        choose(
            f"Modeller (şu an: {settings.model} · {settings.effort or 'varsayılan'})",
            [(m.display_name, m.description) for m in models],
            current,
        )
    ]

    effort = None
    if picked.efforts:
        names = [e.name for e in picked.efforts]
        # Aynı model yeniden seçildiyse eski effort, değilse modelin önerdiği varsayılan.
        keep = settings.effort if picked.slug == settings.model else None
        preferred = keep if keep in names else picked.default_effort
        rows = [
            (e.name, e.description + (" (önerilen)" if e.name == picked.default_effort else ""))
            for e in picked.efforts
        ]
        index = choose(
            f"{picked.display_name} ne kadar düşünsün? (effort)",
            rows,
            names.index(preferred) if preferred in names else 0,
        )
        effort = names[index]

    save_settings(Settings(model=picked.slug, effort=effort))
    console.print(f"[green]Kaydedildi:[/] {picked.display_name} · effort: {effort or 'varsayılan'}")


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Modele sorulacak soru")],
    model: Annotated[
        str | None, typer.Option(help="Bu görev için model (kayıtlı seçimi ezer)")
    ] = None,
    effort: Annotated[
        str | None, typer.Option(help="Bu görev için effort (kayıtlı seçimi ezer)")
    ] = None,
    anatomy_name: AnatomyOption = DEFAULT_PRESET,
) -> None:
    """Gerçek modele tek görev verir; ayrıntıları görev sonunda HTML'e kaydeder."""
    anatomy = pick_anatomy(anatomy_name)
    settings = load_settings()
    picked_model = model or settings.model
    picked_effort = effort or (settings.effort if picked_model == settings.model else None)
    root = start_session(f"{picked_model} · effort: {picked_effort or 'varsayılan'}")
    log = new_session_log(root)
    console.rule("[bold cyan]Sen", align="left")
    console.print(Text(question))

    async def run() -> None:
        async with httpx.AsyncClient(timeout=120) as http:
            tools = anatomy.tool_registry(root)
            agent = Agent(
                llm=ResponsesLLM(ChatGPTTokens(http), picked_model, http, picked_effort),
                tools=tools,
                system_prompt=load_system_prompt(),
                permissions=PermissionManager(root, tools),
                model_label=picked_model,
                effort=picked_effort,
                anatomy=anatomy,
            )
            await run_turn(agent, question, log)

    try:
        asyncio.run(run())
    except (AuthError, ResponsesError, httpx.HTTPError) as exc:
        console.print(Text(f"Hata: {exc}", style="red"))
        raise typer.Exit(1) from exc
    except KeyboardInterrupt as exc:
        console.print("Durduruldu.")
        raise typer.Exit(130) from exc


async def run_turn(agent: Agent, question: str, log: SessionLogger) -> Path | None:
    events: list[Event] = []
    report: Path | None = None
    streamed_text = ""
    stream_open = False
    try:
        with console.status("Model bekleniyor…") as status:
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
                if isinstance(event, ModelStarted):
                    streamed_text = ""
                    status.start()
                    status.update("Model bekleniyor…")
                elif isinstance(event, PermissionRequested):
                    status.stop()
                    choice = ask_permission(console, event.call)
                    if choice == "always":
                        agent.permissions.grant(event.call.name)
                    send = choice != "deny"
                elif isinstance(event, ToolStarted):
                    status.start()
                    status.update("Dosyalar inceleniyor…")
                elif isinstance(event, ModelTextDelta) and event.text:
                    status.stop()
                    if not stream_open:
                        console.rule("[bold magenta]Ajan", align="left")
                        stream_open = True
                    console.print(Text(event.text), end="", soft_wrap=True)
                    streamed_text += event.text
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
            items = summary_items(events)
            console.print(Text(" · ".join(f"{label}: {value}" for label, value in items)))
            try:
                report = write_report(log.path)
                console.print(Text(f"İnceleme: {report}"))
            except (OSError, ValueError) as exc:
                console.print(Text(f"Rapor oluşturulamadı: {exc}", style="yellow"))
            console.print(Text(f"Oturum kaydı: {log.path}"))
    return report


@app.command()
def chat(
    model: Annotated[str | None, typer.Option(help="Bu sohbet için model")] = None,
    effort: Annotated[str | None, typer.Option(help="Bu sohbet için effort")] = None,
    plain: Annotated[bool, typer.Option("--plain", help="Tam ekran yerine sade çıktı")] = False,
    anatomy_name: AnatomyOption = DEFAULT_PRESET,
) -> None:
    """Tam ekran sohbet; --plain veya boruda sade çıktı. /exit çıkar, /inspect raporu açar."""
    anatomy = pick_anatomy(anatomy_name)
    settings = load_settings()
    picked_model = model or settings.model
    picked_effort = effort or (settings.effort if picked_model == settings.model else None)
    if not plain and sys.stdin.isatty() and sys.stdout.isatty():
        from harness.cli.tui import run_tui

        root = find_project_root(Path.cwd())
        log = new_session_log(root)
        run_tui(root, picked_model, picked_effort, load_system_prompt(), log, anatomy)
        if log.path.exists():
            console.print(Text(f"Oturum kaydı: {log.path}"))
        return
    root = start_session(f"{picked_model} · effort: {picked_effort or 'varsayılan'}")
    log = new_session_log(root)
    console.print("/exit: çıkış · /inspect: son rapor · Ctrl+C: durdur")
    console.print("Kayıt ve rapor, istemler ve dosya içerikleri gibi hassas veri içerebilir.")

    async def run() -> None:
        async with httpx.AsyncClient(timeout=120) as http:
            tools = anatomy.tool_registry(root)
            agent = Agent(
                llm=ResponsesLLM(ChatGPTTokens(http), picked_model, http, picked_effort),
                tools=tools,
                system_prompt=load_system_prompt(),
                permissions=PermissionManager(root, tools),
                model_label=picked_model,
                effort=picked_effort,
                anatomy=anatomy,
            )
            report: Path | None = None
            while True:
                try:
                    console.rule("[bold cyan]Sen", align="left")
                    question = console.input("[bold cyan]> [/]").strip()
                except EOFError:
                    break
                if question == "/exit":
                    break
                if question == "/inspect":
                    if report:
                        typer.launch(report.resolve().as_uri())
                    else:
                        console.print("Henüz rapor yok.")
                    continue
                if not question:
                    continue
                console.print()
                report = await run_turn(agent, question, log)

    try:
        asyncio.run(run())
    except (AuthError, ResponsesError, httpx.HTTPError) as exc:
        # Eksik araç sonuçlarıyla yeni model isteği gönderilmez.
        console.print(Text(f"Hata: {exc}. Sohbet sonlandırıldı; kayıt korundu.", style="red"))
        raise typer.Exit(1) from exc
    except KeyboardInterrupt as exc:
        console.print("Durduruldu.")
        raise typer.Exit(130) from exc


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Dinlenecek adres")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Dinlenecek port")] = 8765,
    demo: Annotated[
        bool, typer.Option("--demo", help="Sahte modelle, giriş gerektirmeden dener")
    ] = False,
    ollama: Annotated[
        bool, typer.Option("--ollama", help="ChatGPT yerine yerel Ollama/uyumlu sunucu kullanır")
    ] = False,
    base_url: Annotated[
        str, typer.Option(help="--ollama ile: sunucunun taban URL'si")
    ] = "http://localhost:11434/v1",
    model: Annotated[
        str | None, typer.Option(help="Model adı (--ollama ile zorunlu, yoksa kayıtlı seçim)")
    ] = None,
    effort: Annotated[str | None, typer.Option(help="ChatGPT effort seviyesi")] = None,
    anatomy_name: AnatomyOption = DEFAULT_PRESET,
) -> None:
    """Tarayıcıda sohbet: tek sayfa + WebSocket, aynı çekirdek döngüsü üzerinden."""
    anatomy = pick_anatomy(anatomy_name)
    label = "web · sahte model" if demo else "web · " + ("Ollama" if ollama else "ChatGPT")
    root = start_session(label)
    prompt = load_system_prompt()

    async def run() -> None:
        import uvicorn

        from harness.cli.web import create_app

        async with httpx.AsyncClient(timeout=120) as http:
            llm_factory: Callable[[], LLMClient]
            if demo:

                def llm_factory() -> LLMClient:
                    return ScriptedLLM(DEMO_SCRIPT)
            elif ollama:
                picked_model = model or "llama3.2"

                def llm_factory() -> LLMClient:
                    return ChatCompletionsLLM(base_url, picked_model, http)
            else:
                settings = load_settings()
                picked_model = model or settings.model
                picked_effort = effort or (
                    settings.effort if picked_model == settings.model else None
                )

                def llm_factory() -> LLMClient:
                    return ResponsesLLM(ChatGPTTokens(http), picked_model, http, picked_effort)

            web_app = create_app(anatomy, root, prompt, llm_factory)
            console.print(f"[green]Tarayıcıda aç:[/] http://{host}:{port}")
            config = uvicorn.Config(web_app, host=host, port=port, log_level="warning")
            await uvicorn.Server(config).serve()

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
