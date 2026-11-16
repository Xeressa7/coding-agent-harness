"""read_file: dosyayı satır numaralarıyla okur, isteğe bağlı satır aralığıyla (FR-02)."""

from pathlib import Path

from pydantic import BaseModel, Field

from harness.tools.base import Tool, ToolError, ToolOutput, resolve_inside

MAX_LINES = 2000
MAX_CHARS = 30_000  # çıktı sınırının (40.000) altında: okunan dosya ortadan kesilmez
MAX_LINE_CHARS = 2000  # tek satırlık dev dosyalar (sıkıştırılmış JS) bütçeyi yemesin


class ReadFileParams(BaseModel):
    path: str = Field(description="Çalışma dizinine göre dosya yolu")
    start_line: int | None = Field(default=None, ge=1, description="İlk satır (1'den başlar)")
    end_line: int | None = Field(default=None, ge=1, description="Son satır (dahil)")


class ReadFile(Tool[ReadFileParams]):
    name = "read_file"
    description = (
        "Bir metin dosyasını satır numaralarıyla okur. Bir seferde en çok "
        f"{MAX_LINES} satır ve ~{MAX_CHARS // 1000}.000 karakter gelir; devamı varsa "
        "sonuç bir sonraki start_line'ı söyler."
    )
    params_model = ReadFileParams
    category = "read"

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def describe(self, params: ReadFileParams) -> str:
        if params.start_line or params.end_line:
            return f"{params.path} · satır {params.start_line or 1}–{params.end_line or 'son'}"
        return params.path

    async def run(self, params: ReadFileParams) -> ToolOutput:
        path = resolve_inside(self._root, params.path)
        if not path.is_file():
            raise ToolError(f"{params.path}: böyle bir dosya yok.")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as e:
            raise ToolError(f"{params.path}: metin dosyası değil (UTF-8 okunamadı).") from e

        start = params.start_line or 1
        end = min(params.end_line or len(lines), len(lines))
        width = len(str(end))
        rows: list[str] = []
        size = 0
        for number in range(start, end + 1):
            line = lines[number - 1]
            if len(line) > MAX_LINE_CHARS:
                line = f"{line[:MAX_LINE_CHARS]} … [satır {len(line)} karakter, kısaltıldı]"
            row = f"{number:>{width}}  {line}"
            if rows and (len(rows) >= MAX_LINES or size + len(row) + 1 > MAX_CHARS):
                break
            rows.append(row)
            size += len(row) + 1
        last = start + len(rows) - 1
        content = "\n".join(rows)
        cut = last < end
        if cut:  # bütçe doldu: modelin tahmin etmesi yerine devamın nerede olduğu söylenir
            content += (
                f"\n\n[dosya {len(lines)} satır; {start}–{last} gösterildi. "
                f"Devamı için start_line={last + 1}]"
            )
        partial = f" / {len(lines)}" if len(rows) < len(lines) else ""
        return ToolOutput(
            content,
            summary=f"{len(rows)} satır{partial}" + (" · bütçe doldu" if cut else ""),
            details={
                "path": params.path,
                "lines": len(rows),
                "total_lines": len(lines),
                "start": start,  # gerçekte gösterilen aralık (istenen değil): harita şeridi
                "end": last,
                "cut": cut,
            },
        )
