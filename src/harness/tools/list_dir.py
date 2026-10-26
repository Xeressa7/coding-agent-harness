"""list_dir: klasör ağacını derinlik sınırıyla gösterir.

Modelin projeyi tanırken ilk yaptığı iş budur; `search` ile dosya listesi
almaktan daha az yer kaplar çünkü yapıyı gösterir, her yolu tekrar yazmaz.
Sanal ortam, git ve önbellek klasörlerine girmez (search ile aynı liste).
"""

from pathlib import Path

from pydantic import BaseModel, Field

from harness.tools.base import Tool, ToolError, ToolOutput, resolve_inside
from harness.tools.search import SKIPPED_DIRS

_MAX_ENTRIES = 300


class ListDirParams(BaseModel):
    path: str = Field(default=".", description="Çalışma dizinine göre klasör yolu")
    depth: int = Field(default=2, ge=1, le=5, description="Kaç seviye aşağı inilsin")


class ListDir(Tool[ListDirParams]):
    name = "list_dir"
    description = (
        "Klasördeki dosya ve alt klasörleri ağaç olarak listeler (boyutlarıyla). "
        "Projeyi tanımak için read_file'dan önce kullan."
    )
    params_model = ListDirParams
    category = "read"

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def describe(self, params: ListDirParams) -> str:
        return f"{params.path} · derinlik {params.depth}"

    async def run(self, params: ListDirParams) -> ToolOutput:
        folder = resolve_inside(self._root, params.path)
        if not folder.is_dir():
            raise ToolError(f"{params.path}: böyle bir klasör yok.")
        lines: list[str] = []
        counts = {"dirs": 0, "files": 0}
        total = self._walk(folder, params.depth, 0, lines, counts)
        if total > len(lines):
            lines.append(f"… (+{total - len(lines)} girdi daha; daha dar bir klasör seç)")
        content = "\n".join(lines) if lines else "(boş klasör)"
        return ToolOutput(
            content,
            summary=f"{counts['dirs']} klasör · {counts['files']} dosya",
            details={**counts, "truncated": total > _MAX_ENTRIES},
        )

    def _walk(
        self, folder: Path, depth: int, level: int, lines: list[str], counts: dict[str, int]
    ) -> int:
        """Ağacı yazar; sınırı aşan girdileri yazmadan sayar. Toplam girdi sayısını döner."""
        try:
            entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return 0
        total = 0
        for entry in entries:
            if entry.is_dir() and entry.name in SKIPPED_DIRS:
                continue
            total += 1
            indent = "  " * level
            if entry.is_dir():
                counts["dirs"] += 1
                if len(lines) < _MAX_ENTRIES:
                    lines.append(f"{indent}{entry.name}/")
                if level + 1 < depth:
                    total += self._walk(entry, depth, level + 1, lines, counts)
            else:
                counts["files"] += 1
                if len(lines) < _MAX_ENTRIES:
                    lines.append(f"{indent}{entry.name}  ({_size(entry)})")
        return total


def _size(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return "?"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"
