"""write_file: yeni dosya oluşturur ya da dosyanın tamamını yazar (FR-03)."""

from pathlib import Path

from pydantic import BaseModel, Field

from harness.tools.base import Tool, ToolError, ToolOutput, count_lines, resolve_inside


class WriteFileParams(BaseModel):
    path: str = Field(description="Çalışma dizinine göre dosya yolu")
    content: str = Field(description="Dosyanın yeni içeriğinin tamamı")


class WriteFile(Tool[WriteFileParams]):
    name = "write_file"
    description = (
        "Dosyayı oluşturur ya da içeriğini baştan sona yeniden yazar; eksik klasörleri açar. "
        "Var olan dosyada küçük değişiklik için edit_file kullan."
    )
    params_model = WriteFileParams
    category = "write"

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def describe(self, params: WriteFileParams) -> str:
        return params.path

    async def run(self, params: WriteFileParams) -> ToolOutput:
        path = resolve_inside(self._root, params.path)
        if path.is_dir():
            raise ToolError(f"{params.path}: bir klasör, dosya olarak yazılamaz.")
        existed = path.exists()
        before = _old_line_count(path) if existed else 0
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="": Windows'ta \n sessizce \r\n'ye çevrilmesin; model ne yazdıysa o.
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write(params.content)
        lines = count_lines(params.content)
        state = "üzerine yazıldı" if existed else "oluşturuldu"
        return ToolOutput(
            f"{params.path}: {state} ({lines} satır).",
            summary=f"{state} · {lines} satır" + (f" (önce {before})" if existed else ""),
            details={
                "path": params.path,
                "created": not existed,
                "lines": lines,
                "previous_lines": before,
                "bytes": len(params.content.encode("utf-8")),
            },
        )


def _old_line_count(path: Path) -> int:
    try:
        return count_lines(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, OSError):
        return 0
