"""search: dosya adına (glob) ve içeriğe (regex) göre arar (FR-04).

Sanal ortam, git ve önbellek klasörlerine girmez. Sonuç sayısı sınırlıdır;
sınır aşılırsa modele aramayı daraltması söylenir.
"""

import os
import re
from fnmatch import fnmatchcase
from pathlib import Path

from pydantic import BaseModel, Field

from harness.tools.base import Tool, ToolError, ToolOutput, short

SKIPPED_DIRS = {
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


_MAX_PATHS = 100


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
    category = "search"

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def describe(self, params: SearchParams) -> str:
        where = "" if params.glob == "*" else f" · {params.glob}"
        if params.pattern is None:
            return f"dosya listesi{where}"
        return f"“{short(params.pattern, 40)}”{where}"

    async def run(self, params: SearchParams) -> ToolOutput:
        regex = None
        if params.pattern is not None:
            try:
                regex = re.compile(params.pattern, re.IGNORECASE if params.ignore_case else 0)
            except re.error as e:
                raise ToolError(
                    f"Geçersiz regex: {e}. pattern bir düzenli ifadedir; dosya listesi için "
                    "pattern verme, yalnız glob kullan (ör. glob='*.md')."
                ) from e

        results: list[str] = []
        total = 0
        matched_files: set[str] = set()
        for rel in self._files(params.glob):
            if regex is None:
                total += 1
                matched_files.add(rel)
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
                    matched_files.add(rel)
                    if len(results) < _MAX_RESULTS:
                        results.append(f"{rel}:{number}: {line[:_MAX_LINE_CHARS]}")

        details = {
            "matches": total,
            "files": len(matched_files),
            "truncated": total > len(results),
            "paths": sorted(matched_files)[:_MAX_PATHS],  # harita: arama hangi dosyalara açıldı
        }
        if not results:
            return ToolOutput("Eşleşme yok.", summary="eşleşme yok", details=details)
        if total > len(results):
            results.append(f"… (+{total - len(results)} sonuç daha; aramayı daralt)")
        if regex is None:
            summary = f"{total} dosya"
        else:
            summary = f"{total} eşleşme · {len(matched_files)} dosya"
        return ToolOutput("\n".join(results), summary=summary, details=details)

    def _files(self, glob: str) -> list[str]:
        patterns = {glob, glob.replace("**/", "")}  # src/**/*.py, src/a.py'yi de kapsasın
        found: list[str] = []
        for folder, dirs, files in os.walk(self._root):
            dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS)
            for name in sorted(files):
                rel = (Path(folder) / name).relative_to(self._root).as_posix()
                target = rel if "/" in glob else name
                if any(fnmatchcase(target, p) for p in patterns):
                    found.append(rel)
        return found
