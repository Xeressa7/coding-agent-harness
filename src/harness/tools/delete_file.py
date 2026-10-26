"""delete_file: çalışma dizinindeki bir dosyayı siler.

Yalnız dosya siler, klasör silmez: geri alınamaz bir işin kapsamı küçük kalsın.
İzin katmanı her silmeyi sorar.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from harness.tools.base import Tool, ToolError, ToolOutput, resolve_inside


class DeleteFileParams(BaseModel):
    path: str = Field(description="Çalışma dizinine göre silinecek dosyanın yolu")


class DeleteFile(Tool[DeleteFileParams]):
    name = "delete_file"
    description = "Bir dosyayı siler (klasör silmez). Geri alınamaz."
    params_model = DeleteFileParams
    category = "write"

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def describe(self, params: DeleteFileParams) -> str:
        return params.path

    async def run(self, params: DeleteFileParams) -> ToolOutput:
        path = resolve_inside(self._root, params.path)
        if path.is_dir():
            raise ToolError(f"{params.path}: bir klasör; delete_file yalnız dosya siler.")
        if not path.is_file():
            raise ToolError(f"{params.path}: böyle bir dosya yok.")
        size = path.stat().st_size
        path.unlink()
        return ToolOutput(
            f"{params.path}: silindi.",
            summary=f"silindi · {size} bayt",
            details={"path": params.path, "bytes": size},
        )
