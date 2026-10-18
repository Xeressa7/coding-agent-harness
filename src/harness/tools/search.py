"""search: dosya adına (glob) ve içeriğe (regex) göre arar (FR-04).

Sanal ortam, git ve önbellek klasörlerine girmez. Sonuç sayısı sınırlıdır;
sınır aşılırsa modele aramayı daraltması söylenir.
"""

import os
import re
from fnmatch import fnmatchcase
from pathlib import Path

from pydantic import BaseModel, Field

from harness.tools.base import Tool, ToolError

_SKIPPED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}
_MAX_RESULTS = 200
_MAX_LINE_CHARS = 300


class SearchParams(BaseModel):
    pattern: str | None = Field(
        default=None, description="İçerikte aranacak regex; boşsa yalnız dosya adları listelenir"
    )
    glob: str = Field(
        default="*",
        description="Hangi dosyalar: '/' yoksa yalnız dosya adına (*.py), varsa yola (src/**/*.py)",
    )
    ignore_case: bool = Field(default=False, description="Büyük/küçük harf ayrımı yapma")


class Search(Tool[SearchParams]):
    name = "search"
    description = (
        "Çalışma dizininde dosya arar. pattern verilirse eşleşen satırları "
        "'yol:satır: metin' olarak, verilmezse yalnız dosya yollarını döner."
    )
    params_model = SearchParams

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    async def run(self, params: SearchParams) -> str:
        regex = None
        if params.pattern is not None:
            try:
                regex = re.compile(params.pattern, re.IGNORECASE if params.ignore_case else 0)
            except re.error as e:
                raise ToolError(f"Geçersiz regex: {e}") from e

        results: list[str] = []
        total = 0
        for rel in self._files(params.glob):
            if regex is None:
                total += 1
                if len(results) < _MAX_RESULTS:
                    results.append(rel)
                continue
            try:
                lines = (self._root / rel).read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue  # ikili ya da okunamayan dosya: aramada yok sayılır
            for number, line in enumerate(lines, start=1):
                if regex.search(line):
                    total += 1
                    if len(results) < _MAX_RESULTS:
                        results.append(f"{rel}:{number}: {line[:_MAX_LINE_CHARS]}")

        if not results:
            return "Eşleşme yok."
        if total > len(results):
            results.append(f"… (+{total - len(results)} sonuç daha; aramayı daralt)")
        return "\n".join(results)

    def _files(self, glob: str) -> list[str]:
        patterns = {glob, glob.replace("**/", "")}  # src/**/*.py, src/a.py'yi de kapsasın
        found: list[str] = []
        for folder, dirs, files in os.walk(self._root):
            dirs[:] = sorted(d for d in dirs if d not in _SKIPPED_DIRS)
            for name in sorted(files):
                rel = (Path(folder) / name).relative_to(self._root).as_posix()
                target = rel if "/" in glob else name
                if any(fnmatchcase(target, p) for p in patterns):
                    found.append(rel)
        return found
