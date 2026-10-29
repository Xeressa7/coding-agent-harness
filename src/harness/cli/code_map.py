"""Proje içi içe aktarma (import) bağlantıları: haritada "hangi dosya hangisine dayanıyor".

Gezinti haritası ajanın yolunu klasör ağacında gösterir; bu modül ağacın üstüne
kodun kendi bağlantılarını ekler. Böylece "app.py'den evaluation.py'ye geçti" bir
import bağlantısını mı izledi, yoksa aramadan mı geldi, görülebilir.

Dosyalar çalıştırılmaz, yalnız okunur. Python `ast` ile ayrıştırılır (göreli
importlar dahil, `src/` düzeni tanınır); JS/TS'de yalnız göreli yollar (`./x`,
`../y`) çözülür. Proje dışına (kütüphanelere) giden bağlantılar atılır.
"""

import ast
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from harness.tools.search import SKIPPED_DIRS

_MAX_FILES = 3000
_MAX_BYTES = 512 * 1024
_JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_JS_IMPORT = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*)["'](\.{1,2}/[^"']+)["']"""
)


@dataclass(frozen=True)
class ImportGraph:
    edges: list[tuple[str, str]]  # (içe aktaran, içe aktarılan), proje köküne göre yollar
    files: int
    complete: bool


def import_graph(root: Path) -> ImportGraph:
    files: list[str] = []
    complete = True
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS and not d.startswith("."))
        base = Path(folder).relative_to(root)
        for name in sorted(names):
            if name.endswith(".py") or name.endswith(_JS_SUFFIXES):
                files.append((base / name).as_posix())
        if len(files) >= _MAX_FILES:
            complete = False
            break
    files = files[:_MAX_FILES]
    known = set(files)
    modules = _python_modules(files)
    edges: set[tuple[str, str]] = set()
    for rel in files:
        path = root / rel
        try:
            if path.stat().st_size > _MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        targets = (
            _python_targets(rel, text, modules)
            if rel.endswith(".py")
            else _js_targets(rel, text, known)
        )
        edges.update((rel, target) for target in targets if target != rel)
    return ImportGraph(edges=sorted(edges), files=len(files), complete=complete)


def _python_modules(files: list[str]) -> dict[str, str]:
    """Modül adı -> dosya. `src/harness/core/x.py` hem `harness.core.x` hem `src.harness.core.x`."""
    modules: dict[str, str] = {}
    for rel in files:
        if not rel.endswith(".py"):
            continue
        parts = list(PurePosixPath(rel).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        modules.setdefault(".".join(parts), rel)
        if parts[0] == "src" and len(parts) > 1:
            modules.setdefault(".".join(parts[1:]), rel)
    return modules


def _package_of(rel: str) -> list[str]:
    """Dosyanın içinde bulunduğu paketin adı parçaları (`src/` düzeninde `src` atılır)."""
    parts = list(PurePosixPath(rel).parent.parts)
    return parts[1:] if parts[:1] == ["src"] else parts


def _python_targets(rel: str, text: str, modules: dict[str, str]) -> set[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    found: set[str] = set()

    def resolve(name: str) -> None:
        # `a.b.c` yoksa `a.b`: içe aktarılan bir modül değil, modüldeki bir ad olabilir.
        parts = name.split(".")
        while parts:
            hit = modules.get(".".join(parts))
            if hit:
                found.add(hit)
                return
            parts.pop()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolve(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _package_of(rel)
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = ".".join([*base, node.module] if node.module else base)
            else:
                prefix = node.module or ""
            for alias in node.names:
                resolve(f"{prefix}.{alias.name}" if prefix else alias.name)
    return found


def _js_targets(rel: str, text: str, known: set[str]) -> set[str]:
    found: set[str] = set()
    folder = PurePosixPath(rel).parent
    for spec in _JS_IMPORT.findall(text):
        target = os.path.normpath(str(folder / spec)).replace("\\", "/")
        candidates = [target, *(target + s for s in _JS_SUFFIXES)]
        candidates += [f"{target}/index{s}" for s in _JS_SUFFIXES]
        hit = next((c for c in candidates if c in known), None)
        if hit:
            found.add(hit)
    return found
