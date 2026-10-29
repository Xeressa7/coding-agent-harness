"""Anatomy: harness'ın hangi katmanlarının açık olduğunu söyleyen tek ayar.

Her katman tek bir alandır; katmanı sökmek o alanı değiştirmektir. Hazır
paketler (preset) katman merdiveninin basamaklarıdır: `raw` seviye 0, yani
araçsız ham model; `files` seviye 1, dosya araçları ve plan; `bash` seviye 2,
komut çalıştırma; `full` seviye 3, web erişimi. Her koşu kendi anatomisini
kaydına yazar; deney sonucu hangi kombinasyondan geldiğini söyler.

Komut ve ağ araçları varsayılan pakette değildir: çalışma dizininin dışına
dokunabilirler, bu yüzden açıkça seçilmelidir (izin katmanı yine her çağrıyı sorar).
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from harness.environments.local import LocalEnvironment
from harness.tools.base import Tool, ToolRegistry
from harness.tools.bash import Bash
from harness.tools.delete_file import DeleteFile
from harness.tools.edit_file import EditFile
from harness.tools.fetch_url import FetchUrl
from harness.tools.list_dir import ListDir
from harness.tools.read_file import ReadFile
from harness.tools.search import Search
from harness.tools.update_plan import UpdatePlan
from harness.tools.write_file import WriteFile

ToolName = Literal[
    "read_file",
    "list_dir",
    "search",
    "write_file",
    "edit_file",
    "delete_file",
    "update_plan",
    "bash",
    "fetch_url",
]

_TOOL_FACTORIES: dict[ToolName, Callable[[Path], Tool[Any]]] = {
    "read_file": lambda root: ReadFile(root=root),
    "list_dir": lambda root: ListDir(root=root),
    "search": lambda root: Search(root=root),
    "write_file": lambda root: WriteFile(root=root),
    "edit_file": lambda root: EditFile(root=root),
    "delete_file": lambda root: DeleteFile(root=root),
    "update_plan": lambda root: UpdatePlan(),
    "bash": lambda root: Bash(LocalEnvironment(root)),
    "fetch_url": lambda root: FetchUrl(),
}
_FILE_TOOLS: tuple[ToolName, ...] = (
    "read_file",
    "list_dir",
    "search",
    "write_file",
    "edit_file",
    "delete_file",
    "update_plan",
)


class Anatomy(BaseModel):
    """Açık katmanların listesi; kayda girer, bu yüzden değiştirilemez."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    tools: tuple[ToolName, ...] = ()
    prompt: Literal["agent", "raw"] = "agent"  # prompts/ altındaki talimat dosyası
    save_code_blocks: bool = False  # seviye 0: cevaptaki adlı kod blokları dosyaya yazılır
    loop_guard: bool = False  # takılma uyarısı (core/guards.py)

    def tool_registry(self, root: Path) -> ToolRegistry:
        """Açık araçları, çalışma dizinine bağlı olarak kurar."""
        return ToolRegistry([_TOOL_FACTORIES[name](root) for name in self.tools])


PRESETS: dict[str, Anatomy] = {
    "raw": Anatomy(name="raw", prompt="raw", save_code_blocks=True),
    "files": Anatomy(name="files", tools=_FILE_TOOLS),
    "bash": Anatomy(name="bash", tools=(*_FILE_TOOLS, "bash")),
    "full": Anatomy(name="full", tools=(*_FILE_TOOLS, "bash", "fetch_url")),
}
DEFAULT_PRESET = "files"

# Preset'in üstüne `+ad` ile açılıp `-ad` ile kapatılan katmanlar: `bash+loop_guard`.
LAYERS: dict[str, str] = {
    "loop_guard": "aynı araç çağrısı art arda 3 kez gelince modeli uyarır, 5'te durdurur",
}


def get_preset(name: str) -> Anatomy:
    """Preset adı, ardından isteğe bağlı katman ekleri: `files`, `bash+loop_guard`."""
    base, *changes = re.split(r"(?=[+-])", name.strip())
    try:
        anatomy = PRESETS[base]
    except KeyError:
        known = ", ".join(PRESETS)
        raise ValueError(f"'{base}' adında anatomi yok. Var olanlar: {known}") from None
    update: dict[str, object] = {}
    for change in changes:
        layer = change[1:]
        if layer not in LAYERS:
            known = ", ".join(LAYERS)
            raise ValueError(f"'{layer}' adında katman yok. Var olanlar: {known}")
        update[layer] = change[0] == "+"
    if not update:
        return anatomy
    return anatomy.model_copy(update={**update, "name": name.strip()})
