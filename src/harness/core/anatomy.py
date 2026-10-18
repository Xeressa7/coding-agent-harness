"""Anatomy: harness'ın hangi katmanlarının açık olduğunu söyleyen tek ayar.

Her katman tek bir alandır; katmanı sökmek o alanı değiştirmektir. Hazır
paketler (preset) katman merdiveninin basamaklarıdır: `raw` seviye 0, yani
araçsız ham model; `files` seviye 1, dosya araçları; `bash` seviye 2, komut
çalıştırma. Her koşu kendi anatomisini kaydına yazar; deney sonucu hangi
kombinasyondan geldiğini söyler.

bash izin katmanı gelene kadar varsayılan pakette değildir: komut çalışma
dizininin dışına da dokunabilir, bu yüzden açıkça seçilmelidir.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from harness.environments.local import LocalEnvironment
from harness.tools.base import Tool, ToolRegistry
from harness.tools.bash import Bash
from harness.tools.edit_file import EditFile
from harness.tools.read_file import ReadFile
from harness.tools.search import Search
from harness.tools.write_file import WriteFile

ToolName = Literal["read_file", "write_file", "edit_file", "search", "bash"]

_TOOL_FACTORIES: dict[ToolName, Callable[[Path], Tool[Any]]] = {
    "read_file": lambda root: ReadFile(root=root),
    "write_file": lambda root: WriteFile(root=root),
    "edit_file": lambda root: EditFile(root=root),
    "search": lambda root: Search(root=root),
    "bash": lambda root: Bash(LocalEnvironment(root)),
}
_FILE_TOOLS: tuple[ToolName, ...] = ("read_file", "write_file", "edit_file", "search")


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
    "files": Anatomy(name="files", tools=_FILE_TOOLS),
    "bash": Anatomy(name="bash", tools=(*_FILE_TOOLS, "bash")),
}
DEFAULT_PRESET = "files"


def get_preset(name: str) -> Anatomy:
    try:
        return PRESETS[name]
    except KeyError:
        known = ", ".join(PRESETS)
        raise ValueError(f"'{name}' adında anatomi yok. Var olanlar: {known}") from None
