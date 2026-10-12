"""Giriş noktası: komut satırı parametreleri, tek seferlik görev ve etkileşimli oturum (Typer)."""

import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from harness.cli.render import render
from harness.core.agent import Agent
from harness.core.session_log import SessionLogger, read_session
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


@app.command()
def demo(
    delay: Annotated[float, typer.Option(help="Olaylar arası bekleme (saniye)")] = 0.8,
) -> None:
    """Sahte modelle hazır bir görevi çalıştırır: döngü, araç, hata ve kayıt bir arada."""
    agent = Agent(
        llm=ScriptedLLM(DEMO_SCRIPT),
        tools=ToolRegistry([ReadFile(root=Path.cwd())]),
        system_prompt=load_system_prompt(),
    )
    log = SessionLogger(Path("sessions") / f"{datetime.now():%Y%m%d-%H%M%S}.jsonl")

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


def main() -> None:
    # Windows'ta çıktı boruya/dosyaya giderken Türkçe harfler bozulmasın.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    app()
