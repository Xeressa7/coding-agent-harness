"""Yol gösteren hata mesajları: hata modele ne olduğunu ve bir sonraki adımı söyler.

Ajanlar hatada aynı çağrıyı tekrarlar ya da körlemesine arar. Bulunamayan yol için
benzer adlar ve aynı adlı dosyaların yeri, eşleşmeyen metin için dosyadaki en yakın
satır verilir. Arama sınırlıdır: büyük projede hata mesajı yavaşlamaz.
"""

import difflib
import os
from pathlib import Path

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next"}
_SCAN_LIMIT = 20_000  # en fazla bu kadar girdiye bakılır
_SHOWN = 3


def missing_path(root: Path, raw: str, *, want_dir: bool = False) -> str:
    """`raw` yoksa: aynı klasördeki benzer adlar, yoksa projede aynı adı taşıyan yollar."""
    kind = "klasör" if want_dir else "dosya"
    target = (root / raw).resolve()
    hints: list[str] = []
    if target.parent.is_dir() and target.parent.is_relative_to(root):
        names = [entry.name for entry in _entries(target.parent) if entry.is_dir() == want_dir]
        close = difflib.get_close_matches(target.name, names, n=_SHOWN, cutoff=0.6)
        hints = [_relative(root, target.parent / name) for name in close]
    if not hints:
        hints = _same_name(root, target.name, want_dir)
    message = f"{raw}: böyle bir {kind} yok."
    if hints:
        shown = ", ".join(f"`{hint}`" for hint in hints)
        message += f" Bunu mu demek istedin: {shown}?"
    else:
        message += " Yolu list_dir ya da search ile bul."
    return message + " Dosya araçlarının yolları çalışma dizininin köküne göredir."


def closest_line(text: str, wanted: str) -> str | None:
    """old_text'in ilk dolu satırına dosyada en çok benzeyen satır: "12. satır: ..."."""
    first = next((line.strip() for line in wanted.splitlines() if line.strip()), "")
    if not first:
        return None
    lines = text.splitlines()
    stripped = [line.strip() for line in lines]
    best = difflib.get_close_matches(first, stripped, n=1, cutoff=0.6)
    if not best:
        return None
    number = stripped.index(best[0]) + 1
    shown = lines[number - 1]
    if len(shown) > 160:
        shown = shown[:159] + "…"
    exact_but_indented = best[0] == first
    note = " (içerik aynı, girinti ya da boşluk farklı)" if exact_but_indented else ""
    return f"En yakın satır {number}{note}: {shown}"


def _entries(folder: Path) -> list[os.DirEntry[str]]:
    try:
        with os.scandir(folder) as found:
            return list(found)
    except OSError:
        return []


def _same_name(root: Path, name: str, want_dir: bool) -> list[str]:
    found: list[str] = []
    seen = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        seen += len(dirs) + len(files)
        if name in (dirs if want_dir else files):
            found.append(_relative(root, Path(folder) / name))
            if len(found) >= _SHOWN:
                break
        if seen > _SCAN_LIMIT:
            break
    return found


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
