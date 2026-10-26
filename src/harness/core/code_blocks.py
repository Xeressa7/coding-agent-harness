"""Seviye 0'ın tek harness müdahalesi: cevaptaki adlı kod bloklarını dosyaya yazmak.

Araçsız model dosyaları cevabında verir. Bloğun açılış satırı dil + yol taşır
(```html index.html); tek kelimelik ve nokta içeren açılış da yol sayılır
(```style.css). Adı olmayan blok örnek koddur, yazılmaz, yalnız sayılır.
Böylece ham modelin çıktısı da diğer seviyelerle aynı yerde, aynı biçimde durur.
"""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from harness.tools.base import ToolError, resolve_inside

# Satır başında ``` ile açılan, satır başında ``` ile kapanan blok.
_FENCE = re.compile(r"^```([^\n`]*)\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)


class CodeBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    content: str


def extract_code_blocks(text: str) -> tuple[list[CodeBlock], int]:
    """Adlı blokları sırayla döner; ikinci değer adsız blok sayısıdır."""
    blocks: list[CodeBlock] = []
    unnamed = 0
    for match in _FENCE.finditer(text):
        path = _path_from_info(match.group(1))
        if path is None:
            unnamed += 1
        else:
            blocks.append(CodeBlock(path=path, content=match.group(2)))
    return blocks, unnamed


def _path_from_info(info: str) -> str | None:
    words = info.split()
    if len(words) >= 2:
        return words[-1]
    if len(words) == 1 and ("." in words[0] or "/" in words[0]):
        return words[0]
    return None


def save_code_blocks(blocks: list[CodeBlock], root: Path) -> tuple[list[str], list[str]]:
    """Blokları çalışma dizinine yazar; (kaydedilen, reddedilen) yolları döner."""
    saved: list[str] = []
    rejected: list[str] = []
    for block in blocks:
        try:
            path = resolve_inside(root.resolve(), block.path)
        except ToolError:
            rejected.append(block.path)
            continue
        if path.is_dir():
            rejected.append(block.path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write(block.content)
        saved.append(block.path)
    return saved, rejected
