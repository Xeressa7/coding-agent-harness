"""Anatomy: harness'ın hangi katmanlarının açık olduğunu söyleyen tek ayar.

Her katman tek bir alandır; katmanı sökmek o alanı değiştirmektir. Hazır
paketler (preset) katman merdiveninin basamaklarıdır: `raw` seviye 0, yani
araçsız ham model; `files` seviye 1, dosya araçları. Her koşu kendi
anatomisini kaydına yazar; deney sonucu hangi kombinasyondan geldiğini söyler.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from harness.tools.base import Tool, ToolRegistry
from harness.tools.read_file import ReadFile

ToolName = Literal["read_file"]

_TOOL_FACTORIES: dict[ToolName, Callable[[Path], Tool[Any]]] = {
    "read_file": lambda root: ReadFile(root=root),
}


class Anatomy(BaseModel):
    """Açık katmanların listesi; kayda girer, bu yüzden değiştirilemez."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    tools: tuple[ToolName, ...] = ()

    def tool_registry(self, root: Path) -> ToolRegistry:
        """Açık araçları, çalışma dizinine bağlı olarak kurar."""
        return ToolRegistry([_TOOL_FACTORIES[name](root) for name in self.tools])


PRESETS: dict[str, Anatomy] = {
    "raw": Anatomy(name="raw"),
    "files": Anatomy(name="files", tools=("read_file",)),
}
DEFAULT_PRESET = "files"


def get_preset(name: str) -> Anatomy:
    try:
        return PRESETS[name]
    except KeyError:
        known = ", ".join(PRESETS)
        raise ValueError(f"'{name}' adında anatomi yok. Var olanlar: {known}") from None
