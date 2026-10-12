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

from harness.cli.render import render
from harness.core.agent import Agent
from harness.core.config import find_project_root
from harness.core.session_log import SessionLogger, read_session
from harness.llm.chatgpt_auth import AuthError, ChatGPTTokens, login
from harness.llm.responses import ResponsesError, ResponsesLLM, list_models
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall
from harness.tools.base import ToolRegistry
from harness.tools.read_file import ReadFile

app = typer.Typer(help="LLM tabanlı coding agent harness'i.", no_args_is_help=True)
console = Console()

_PROMPT_FILE = Path(__file__).parent.parent / "prompts" / "system.md"

DEFAULT_MODEL = "gpt-6-luna"  # küçük, kotayı az tüketen geliştirme modeli

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


def start_session() -> Path:
    """Çalışma dizinini bulur ve ekrana yazar: kullanıcı ajanın neyi gördüğünü bilmeli."""
    root = find_project_root(Path.cwd())
    console.print(f"[dim]Çalışma dizini: {root}[/]")
    return root


def new_session_log(root: Path) -> SessionLogger:
    return SessionLogger(root / "sessions" / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl")


@app.command()
def demo(
    delay: Annotated[float, typer.Option(help="Olaylar arası bekleme (saniye)")] = 0.8,
) -> None:
    """Sahte modelle hazır bir görevi çalıştırır: döngü, araç, hata ve kayıt bir arada."""
    root = start_session()
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


@app.command()
def models() -> None:
    """Giriş yapılan hesabın kullanabildiği modelleri listeler."""

    async def run() -> list[str]:
        async with httpx.AsyncClient(timeout=30) as http:
            return await list_models(ChatGPTTokens(http), http)

    try:
        names = asyncio.run(run())
    except (AuthError, ResponsesError) as e:
        console.print(f"[red]Hata:[/] {e}")
        raise typer.Exit(1) from e
    for name in names:
        console.print(name)


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="Modele sorulacak soru")],
    model: Annotated[str, typer.Option(envvar="HARNESS_MODEL", help="Model adı")] = DEFAULT_MODEL,
) -> None:
    """Gerçek modele (ChatGPT planı) bir görev verir; model projede dosya okuyabilir."""

    async def run() -> None:
        async with httpx.AsyncClient(timeout=120) as http:
            agent = Agent(
                llm=ResponsesLLM(ChatGPTTokens(http), model, http),
                tools=ToolRegistry([ReadFile(root=root)]),
                system_prompt=load_system_prompt(),
            )
            with console.status(f"{model} düşünüyor…"):
                async for event in agent.run(question):
                    log.write(event)
                    render(console, event)

    root = start_session()
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
