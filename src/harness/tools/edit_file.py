"""edit_file: eşleşen metni değiştirir (FR-03).

Eşleşme yoksa ya da birden fazlaysa değişiklik yapmaz, hata döner. Model
dosyanın eski halini hatırlıyorsa eşleşme tutmaz; hata, dosyayı yeniden
okumasını söyler. Bayat bağlam tuzağında modelin ilk duvara çarptığı yer burası.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from harness.tools.base import Tool, ToolError, ToolOutput, count_lines, resolve_inside


class EditFileParams(BaseModel):
    path: str = Field(description="Çalışma dizinine göre dosya yolu")
    old_text: str = Field(min_length=1, description="Değişecek metin; dosyada tam bir kez geçmeli")
    new_text: str = Field(description="Yerine yazılacak metin (boş: sil)")


class EditFile(Tool[EditFileParams]):
    name = "edit_file"
    description = (
        "Dosyada tam bir kez geçen old_text'i new_text ile değiştirir. "
        "Eşleşme yoksa ya da birden fazlaysa hiçbir şey değişmez."
    )
    params_model = EditFileParams
    category = "write"

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def describe(self, params: EditFileParams) -> str:
        return params.path

    async def run(self, params: EditFileParams) -> ToolOutput:
        path = resolve_inside(self._root, params.path)
        if not path.is_file():
            raise ToolError(f"{params.path}: böyle bir dosya yok.")
        try:
            with path.open(encoding="utf-8", newline="") as f:
                text = f.read()
        except UnicodeDecodeError as e:
            raise ToolError(f"{params.path}: metin dosyası değil (UTF-8 okunamadı).") from e

        old, new = params.old_text, params.new_text
        if old not in text and "\r\n" in text:
            # Model satır sonlarını \n yazar; Windows dosyası \r\n taşıyabilir.
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        count = text.count(old)
        if count == 0:
            raise ToolError(
                f"{params.path}: old_text dosyada bulunamadı; dosya değişmiş olabilir, "
                "read_file ile yeniden oku."
            )
        if count > 1:
            raise ToolError(
                f"{params.path}: old_text {count} yerde geçiyor; "
                "çevresinden birkaç satır ekleyerek tekil yap."
            )
        start = text.index(old)
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old, new, 1))
        line = text.count("\n", 0, start) + 1
        removed, added = count_lines(params.old_text), count_lines(params.new_text)
        return ToolOutput(
            f"{params.path}: {line}. satırdaki metin değiştirildi.",
            summary=f"satır {line} · −{removed} +{added}",
            details={"path": params.path, "line": line, "removed": removed, "added": added},
        )
