"""Renk temaları: tek palet tanımından tam ekran (Textual) ve sade akış (Rich) renkleri.

Ön yüz renkleri anlamla adlandırır (hata, uyarı, harness müdahalesi...), renk adıyla
değil; tema değişince her yer birlikte değişir. Hazır paletlerin yanında
`~/.harness/theme.toml` ile kişisel tema yazılabilir:

    base = "koyu"          # üstüne yazılacak hazır palet
    [colors]
    primary = "#ff8a3d"    # yalnız değişmesi istenen renkler

`NO_COLOR` tanımlıysa sade akış renksizdir (Rich bunu kendisi uygular).
"""

import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import cast

from rich.theme import Theme as RichTheme
from textual.theme import Theme as TextualTheme

from harness.core.config import HARNESS_HOME

THEME_FILE = HARNESS_HOME / "theme.toml"
CUSTOM = "özel"


@dataclass(frozen=True)
class Palette:
    name: str
    description: str
    dark: bool
    primary: str  # vurgu: kullanıcı, başlıklar, seçili satır
    foreground: str
    background: str
    surface: str
    muted: str  # ikincil metin, düşünce
    border: str
    success: str
    warning: str
    error: str
    harness: str  # harness'ın kendi müdahaleleri (kod bloğu yazımı, takılma uyarısı)
    agent: str  # modelin cevap başlığı


PALETTES: dict[str, Palette] = {
    p.name: p
    for p in (
        Palette(
            name="koyu",
            description="varsayılan koyu tema",
            dark=True,
            primary="#6895f4",
            foreground="#ebeff5",
            background="#0f1216",
            surface="#171b20",
            muted="#9199a5",
            border="#2a2e34",
            success="#5cc98a",
            warning="#e0a83a",
            error="#f07878",
            harness="#c39bff",
            agent="#e58ad6",
        ),
        Palette(
            name="açık",
            description="aydınlık ortam için",
            dark=False,
            primary="#436ed1",
            foreground="#11161f",
            background="#fcfcfc",
            surface="#f8f8fa",
            muted="#6b727e",
            border="#e3e5e7",
            success="#1f8a4c",
            warning="#9a6400",
            error="#c23b3b",
            harness="#7a3fc4",
            agent="#a8358f",
        ),
        Palette(
            name="yüksek-kontrast",
            description="siyah zemin, en yüksek okunurluk",
            dark=True,
            primary="#ffd400",
            foreground="#ffffff",
            background="#000000",
            surface="#000000",
            muted="#d0d0d0",
            border="#ffffff",
            success="#00e676",
            warning="#ffd400",
            error="#ff5252",
            harness="#40c4ff",
            agent="#ff80ff",
        ),
        Palette(
            # Okabe-Ito: kırmızı-yeşil ayrımı yapamayanlar için seçilmiş renkler.
            name="renk-körü",
            description="kırmızı/yeşil yerine mavi/turuncu ayrımı (Okabe-Ito)",
            dark=True,
            primary="#56b4e9",
            foreground="#ebeff5",
            background="#0f1216",
            surface="#171b20",
            muted="#9199a5",
            border="#2a2e34",
            success="#009e73",
            warning="#e69f00",
            error="#d55e00",
            harness="#cc79a7",
            agent="#f0e442",
        ),
    )
}
DEFAULT_THEME = "koyu"
_COLOR_FIELDS = [f.name for f in fields(Palette) if f.name not in {"name", "description", "dark"}]


def load_custom(path: Path | None = None) -> Palette | None:
    """theme.toml varsa kişisel palet; yoksa ya da bozuksa None (hata sohbeti düşürmez)."""
    path = path or THEME_FILE
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    base = PALETTES.get(str(data.get("base", DEFAULT_THEME)), PALETTES[DEFAULT_THEME])
    raw_colors = data.get("colors")
    colors = cast(dict[str, object], raw_colors) if isinstance(raw_colors, dict) else {}
    changes = {k: str(v) for k, v in colors.items() if k in _COLOR_FIELDS and _is_hex(str(v))}
    return replace(base, name=CUSTOM, description=f"{path} · {base.name} üstüne", **changes)


def available(path: Path | None = None) -> dict[str, Palette]:
    custom = load_custom(path)
    return {**PALETTES, **({CUSTOM: custom} if custom else {})}


def palette_for(name: str | None, path: Path | None = None) -> Palette:
    return available(path).get(name or DEFAULT_THEME, PALETTES[DEFAULT_THEME])


def textual_theme(palette: Palette) -> TextualTheme:
    return TextualTheme(
        name=f"harness-{palette.name}",
        primary=palette.primary,
        secondary=palette.primary,
        accent=palette.primary,
        foreground=palette.foreground,
        background=palette.background,
        surface=palette.surface,
        panel=palette.surface,
        success=palette.success,
        warning=palette.warning,
        error=palette.error,
        variables={"text-muted": palette.muted, "border": palette.border},
        dark=palette.dark,
    )


def rich_theme(palette: Palette) -> RichTheme:
    """Sade akışın stil adları; ön yüz renk değil bu adları kullanır."""
    return RichTheme(
        {
            "h.user": f"bold {palette.primary}",
            "h.agent": f"bold {palette.agent}",
            "h.muted": "dim",
            "h.thought": "dim italic",
            "h.ok": palette.success,
            "h.ok.strong": f"bold {palette.success}",
            "h.warn": palette.warning,
            "h.warn.strong": f"bold {palette.warning}",
            "h.error": palette.error,
            "h.harness": palette.harness,
        }
    )


def _is_hex(value: str) -> bool:
    digits = value.removeprefix("#")
    return (
        value.startswith("#")
        and len(digits) in {3, 6}
        and all(c in "0123456789abcdefABCDEF" for c in digits)
    )
