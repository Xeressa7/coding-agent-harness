"""read_file: dosyayı satır numaralarıyla okur, isteğe bağlı satır aralığıyla (FR-02)."""

from pathlib import Path

from pydantic import BaseModel, Field

from harness.tools.base import Tool, ToolError, resolve_inside


class ReadFileParams(BaseModel):
    path: str = Field(description="Çalışma dizinine göre dosya yolu")
    start_line: int | None = Field(default=None, ge=1, description="İlk satır (1'den başlar)")
    end_line: int | None = Field(default=None, ge=1, description="Son satır (dahil)")


class ReadFile(Tool[ReadFileParams]):
    name = "read_file"
    description = "Bir metin dosyasını satır numaralarıyla okur."
    params_model = ReadFileParams

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    async def run(self, params: ReadFileParams) -> str:
        path = resolve_inside(self._root, params.path)
        if not path.is_file():
            raise ToolError(f"{params.path}: böyle bir dosya yok.")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as e:
            raise ToolError(f"{params.path}: metin dosyası değil (UTF-8 okunamadı).") from e

        start = params.start_line or 1
        end = params.end_line or len(lines)
        width = len(str(end))
        return "\n".join(
            f"{number:>{width}}  {line}"
            for number, line in enumerate(lines[start - 1 : end], start=start)
        )
