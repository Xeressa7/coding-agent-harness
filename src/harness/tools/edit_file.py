"""edit_file: eşleşen metni değiştirir (FR-03).

Eşleşme yoksa ya da birden fazlaysa değişiklik yapmaz, hata döner. Model
dosyanın eski halini hatırlıyorsa eşleşme tutmaz; hata, dosyayı yeniden
okumasını söyler. Bayat bağlam tuzağında modelin ilk duvara çarptığı yer burası.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from harness.tools.base import Tool, ToolError, ToolOutput, count_lines, resolve_inside
from harness.tools.hints import closest_line, missing_path


class EditFileParams(BaseModel):
    path: str = Field(description="Çalışma dizinine göre dosya yolu")
    old_text: str = Field(min_length=1, description="Değişecek metin; dosyada tam bir kez geçmeli")
    new_text: str = Field(description="Yerine yazılacak metin (boş: sil)")
    replace_all: bool = Field(
        default=False, description="true: old_text'in geçtiği her yeri değiştirir (ad değişikliği)"
    )


class EditFile(Tool[EditFileParams]):
    name = "edit_file"
    description = (
        "Dosyada tam bir kez geçen old_text'i new_text ile değiştirir; replace_all ile her "
        "geçtiği yeri. Eşleşme yoksa (ya da replace_all olmadan birden fazlaysa) hiçbir şey "
        "değişmez."
    )
    params_model = EditFileParams
    category = "write"

    def __init__(self, root: Path, *, show_region: bool = False) -> None:
        """show_region (`tool_guidance` katmanı): sonuç düzenlenen bölgeyi gösterir ve açıklama
        read_file önekini katmamayı söyler. Kapalıyken sonuç eskisi gibi tek satırdır; model
        düzenlemeyi doğrulamak için dosyayı yeniden okuyordu (5. tur pilotu)."""
        self._root = root.resolve()
        self._show_region = show_region
        if show_region:
            self.description = (
                f"{EditFile.description} old_text'e read_file'ın satır öneki (numara + sekme) "
                "katılmaz; girinti dosyadaki gibi yazılır. Sonuç düzenlenen bölgeyi satır "
                "numaralarıyla gösterir: doğrulamak için dosyayı yeniden okumaya gerek yok."
            )

    def describe(self, params: EditFileParams) -> str:
        return params.path

    async def run(self, params: EditFileParams) -> ToolOutput:
        path = resolve_inside(self._root, params.path)
        if not path.is_file():
            raise ToolError(missing_path(self._root, params.path))
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
            closest = closest_line(text, params.old_text)
            raise ToolError(
                f"{params.path}: old_text dosyada bulunamadı; dosya değişmiş olabilir, "
                "read_file ile yeniden oku." + (f"\n{closest}" if closest else "")
            )
        if count > 1 and not params.replace_all:
            raise ToolError(
                f"{params.path}: old_text {count} yerde geçiyor; çevresinden birkaç satır "
                "ekleyerek tekil yap ya da hepsi için replace_all: true kullan."
            )
        start = text.index(old)
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old, new) if params.replace_all else text.replace(old, new, 1))
        line = text.count("\n", 0, start) + 1
        removed, added = count_lines(params.old_text), count_lines(params.new_text)
        where = f"{count} yerde" if count > 1 else f"{line}. satırdaki"
        content = f"{params.path}: {where} metin değiştirildi."
        if self._show_region:
            content += "\n" + _region(path, line, added, first_only=count > 1)
        return ToolOutput(
            content,
            summary=f"satır {line}"
            + (f" (+{count - 1} yer)" if count > 1 else "")
            + f" · −{removed} +{added}",
            details={
                "path": params.path,
                "line": line,
                "removed": removed * count,
                "added": added * count,
                "replaced": count,
            },
        )


REGION_CONTEXT = 3  # düzenlenen bölgenin üstünde ve altında gösterilen satır
REGION_MAX = 40  # uzun bir yeni metin sonucu şişirmesin


def _region(path: Path, line: int, added: int, *, first_only: bool) -> str:
    """Düzenlemeden sonraki dosyadan değişen satırlar ve çevresi; read_file'ın sekmeli önekiyle."""
    lines = path.read_text(encoding="utf-8").splitlines()
    first = max(1, line - REGION_CONTEXT)
    last = min(len(lines), line + max(added, 1) - 1 + REGION_CONTEXT)
    shown_last = min(last, first + REGION_MAX - 1)
    width = len(str(shown_last))
    rows = [f"{n:>{width}}\t{lines[n - 1]}" for n in range(first, shown_last + 1)]
    head = "İlk değişen yer, düzenlemeden sonra:" if first_only else "Düzenlemeden sonra:"
    tail = f"\n[… {last - shown_last} satır daha]" if shown_last < last else ""
    return f"{head}\n" + "\n".join(rows) + tail
