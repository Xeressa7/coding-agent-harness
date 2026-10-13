"""Giriş noktası: komut satırı parametreleri, tek seferlik görev ve etkileşimli oturum (Typer)."""

import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.prompt import IntPrompt
from rich.table import Table

from harness.cli.render import render
from harness.core.agent import Agent
from harness.core.config import Settings, find_project_root, load_settings, save_settings
from harness.core.session_log import SessionLogger, read_session
from harness.llm.chatgpt_auth import AuthError, ChatGPTTokens, login
from harness.llm.responses import ModelInfo, ResponsesError, ResponsesLLM, list_models
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall
from harness.tools.base import ToolRegistry
from harness.tools.read_file import ReadFile

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
    return SessionLogger(root / "sessions" / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl")


@app.command()
def demo(
    delay: Annotated[float, typer.Option(help="Olaylar arası bekleme (saniye)")] = 0.8,
) -> None:
    """Sahte modelle hazır bir görevi çalıştırır: döngü, araç, hata ve kayıt bir arada."""
    root = start_session("sahte model (hazır senaryo)")
    agent = Agent(
        llm=ScriptedLLM(DEMO_SCRIPT),
        tools=ToolRegistry([ReadFile(root=root)]),
        system_prompt=load_system_prompt(),
    )
    log = new_session_log(root)

    async def run() -> None:
        async for event in agent.run(DEMO_TASK):
            log.write(event)
            render(console, event)
            await asyncio.sleep(delay)

    asyncio.run(run())
    console.print(
        f"\nOturum kaydı: [bold]{log.path}[/]  (tekrar izlemek için: harness replay {log.path})"
    )


@app.command()
def replay(path: Annotated[Path, typer.Argument(help="Oturum kaydı (.jsonl)")]) -> None:
    """Kaydedilmiş bir oturumu, canlıdaki aynı ekranla yeniden oynatır."""
    for event in read_session(path):
        render(console, event)


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
) -> None:
    """Gerçek modele (ChatGPT planı) bir görev verir; model projede dosya okuyabilir."""
    settings = load_settings()
    model = model or settings.model
    effort = effort or (settings.effort if model == settings.model else None)

    async def run() -> None:
        async with httpx.AsyncClient(timeout=120) as http:
            agent = Agent(
                llm=ResponsesLLM(ChatGPTTokens(http), model, http, effort),
                tools=ToolRegistry([ReadFile(root=root)]),
                system_prompt=load_system_prompt(),
            )
            with console.status(f"{model} düşünüyor…"):
                async for event in agent.run(question):
                    log.write(event)
                    render(console, event)

    root = start_session(f"{model} · effort: {effort or 'varsayılan'}")
    log = new_session_log(root)
    try:
        asyncio.run(run())
    except (AuthError, ResponsesError) as e:
        console.print(f"[red]Hata:[/] {e}")
        raise typer.Exit(1) from e
    console.print(f"\nOturum kaydı: [bold]{log.path}[/]")


def main() -> None:
    # Windows'ta çıktı boruya/dosyaya giderken Türkçe harfler bozulmasın.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    app()
