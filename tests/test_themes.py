"""Renk temaları: hazır paletler, kişisel tema dosyası, /theme ve stil adlarının tamlığı."""

import re
from pathlib import Path

import pytest
from rich.console import Console

from harness.cli import themes
from harness.cli.commands import ChatSession
from harness.core.anatomy import get_preset
from harness.llm.base import ModelInfo
from harness.llm.providers import get_provider
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage

CLI = Path(themes.__file__).parent


def test_every_style_name_used_in_the_cli_is_defined_by_the_theme() -> None:
    # Tanımsız ad Rich'te ancak yazdırılırken patlar; burada hepsi önceden denetlenir.
    used: set[str] = set()
    for source in ("app.py", "render.py", "eval_report.py"):
        used |= set(re.findall(r"\bh\.[a-z]+(?:\.[a-z]+)?\b", (CLI / source).read_text("utf-8")))
    defined = set(themes.rich_theme(themes.PALETTES["koyu"]).styles)
    assert used and used <= defined, used - defined
    console = Console(theme=themes.rich_theme(themes.PALETTES["renk-körü"]), record=True)
    for name in sorted(used):
        console.print(f"[{name}]örnek[/]")


def test_custom_file_overrides_only_valid_colors_on_a_base(theme_file: Path) -> None:
    assert themes.load_custom() is None and themes.CUSTOM not in themes.available()
    theme_file.write_text(
        'base = "açık"\n[colors]\nprimary = "#ff8a3d"\nerror = "kırmızı"\nyok = "#000"\n',
        encoding="utf-8",
    )
    custom = themes.load_custom()
    assert custom is not None and custom.name == themes.CUSTOM
    assert custom.primary == "#ff8a3d"
    assert custom.error == themes.PALETTES["açık"].error  # geçersiz renk yok sayılır
    assert not custom.dark and themes.palette_for(themes.CUSTOM) == custom
    theme_file.write_text("[bozuk", encoding="utf-8")
    assert themes.load_custom() is None  # bozuk dosya sohbeti düşürmez


def test_unknown_names_fall_back_to_the_default_palette() -> None:
    assert themes.palette_for(None).name == themes.DEFAULT_THEME
    assert themes.palette_for("silinmiş-tema").name == themes.DEFAULT_THEME
    textual = themes.textual_theme(themes.PALETTES["yüksek-kontrast"])
    assert textual.name == "harness-yüksek-kontrast" and textual.background == "#000000"


@pytest.mark.anyio
async def test_theme_command_lists_picks_saves_and_cycles(tmp_path: Path) -> None:
    saved: list[str] = []

    async def no_models(provider: object) -> list[ModelInfo]:
        return []

    session = ChatSession(
        root=tmp_path,
        anatomy=get_preset("files"),
        provider=get_provider("chatgpt"),
        model="luna",
        effort=None,
        make_llm=lambda provider, model, effort: ScriptedLLM([AssistantMessage(text="ok")]),
        load_models=no_models,
        load_prompt=lambda anatomy: "talimat",
        sessions_dir=tmp_path / "logs",
        save_choice=lambda s: saved.append(s.theme),
        theme="yok-boyle-tema",
    )
    assert session.theme == "koyu"
    picker = (await session.run("/theme")).picker
    assert picker is not None and picker.kind == "theme"
    assert [c.value for c in picker.choices] == list(themes.PALETTES)
    assert next(c for c in picker.choices if c.current).value == "koyu"

    reply = await session.choose("theme", "renk-körü")
    assert reply.theme == "renk-körü" and session.theme == "renk-körü"
    assert "yok" in (await session.run("/theme mor")).text and reply.theme
    assert session.cycle_theme() == "koyu"  # son paletten başa döner
    assert saved == ["renk-körü", "koyu"]
