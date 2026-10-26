"""Tool arayüzü ve ToolRegistry.

Her aracın adı, açıklaması, girdi şeması (Pydantic modelinden üretilen JSON
Schema) ve çalıştırma fonksiyonu vardır. Kayıt defteri araçları tutar,
şemalarını modele sunar ve çağrıyı doğru araca yönlendirir. Araç hatası
istisna olarak fırlamaz; hata mesajı araç sonucu olarak modele döner.

Telemetri için her araç kendini insan diliyle de anlatır: `category` (okuma,
yazma, komut...), `describe()` (ne yapacak: "README.md · satır 1–40") ve
sonucun özeti (`ToolOutput.summary`: "40 satır"). Bunlar modele gitmez;
olaylara yazılır, zaman çizelgesi ve ekranlar onları gösterir.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from harness.messages import ToolCall, ToolResult, ToolSpec

# Aracın ne tür iş yaptığı; izin kuralı ve zaman çizelgesindeki şeridi bundan çıkar.
ToolCategory = Literal["read", "search", "write", "exec", "network", "plan", "other"]


class ToolError(Exception):
    """Aracın modele açıklamak istediği beklenen hata (dosya yok, yol izinsiz...)."""


@dataclass(frozen=True)
class ToolOutput:
    """Aracın döndüğü: modele giden metin + yalnız kayda giden özet ve ölçümler."""

    content: str
    summary: str = ""
    details: dict[str, Any] = field(default_factory=lambda: {})


@dataclass(frozen=True)
class ToolOutcome:
    """Bir çağrının bütün sonucu: modele giden ToolResult ve telemetri."""

    result: ToolResult
    summary: str = ""
    details: dict[str, Any] = field(default_factory=lambda: {})


def resolve_inside(root: Path, raw: str) -> Path:
    """Modelin verdiği yolu çalışma dizinine göre çözer; dışarı çıkıyorsa reddeder.

    Asıl izin katmanı core/permissions.py'dedir; bu, araçların kendi emniyet kemeri.
    """
    path = (root / raw).resolve()
    if not path.is_relative_to(root):
        raise ToolError(f"{raw}: çalışma dizini dışında.")
    return path


class Tool[P: BaseModel](ABC):
    """Bir araç: adı, modele açıklaması, girdi modeli ve çalıştırma fonksiyonu."""

    name: str
    description: str
    params_model: type[P]
    category: ToolCategory = "other"

    @abstractmethod
    async def run(self, params: P) -> str | ToolOutput:
        """Aracı çalıştırır, modele gidecek metni döner. Beklenen hatada ToolError fırlatır."""

    def describe(self, params: P) -> str:
        """Çağrının insan diliyle kısa anlatımı (ör. "README.md · satır 1–40")."""
        return ""

    def spec(self) -> ToolSpec:
        """Aracın modele tanıtımı: ad, açıklama, girdilerin JSON Schema'sı."""
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.params_model.model_json_schema(),
        )


class ToolRegistry:
    """Araçları adlarıyla tutar; modelin isteğini doğru araca yönlendirir."""

    def __init__(self, tools: list[Tool[Any]]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools.values()]

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def describe(self, call: ToolCall) -> tuple[ToolCategory | None, str]:
        """Çağrının kategorisi ve kısa anlatımı; argüman bozuksa anlatım boş kalır."""
        tool = self._tools.get(call.name)
        if tool is None:
            return None, ""
        try:
            params = tool.params_model.model_validate_json(call.arguments)
        except ValidationError:
            return tool.category, ""
        return tool.category, tool.describe(params)

    async def call(self, call: ToolCall) -> ToolResult:
        """İsteği çalıştırır. Ne olursa olsun bir ToolResult döner, istisna fırlatmaz."""
        return (await self.execute(call)).result

    async def execute(self, call: ToolCall) -> ToolOutcome:
        """`call` ile aynı, ayrıca özet ve ölçümleri döner."""
        tool = self._tools.get(call.name)
        if tool is None:
            known = ", ".join(sorted(self._tools))
            return _error(call, f"'{call.name}' adında araç yok. Var olanlar: {known}")
        try:
            params = tool.params_model.model_validate_json(call.arguments)
        except ValidationError as e:
            return _error(call, f"Argümanlar geçersiz: {e}")
        try:
            output = await tool.run(params)
        except ToolError as e:
            return _error(call, str(e))
        except Exception as e:
            # Beklenmeyen hata da modele döner; döngü çökmez, model başka yol dener.
            return _error(call, f"Araç beklenmedik biçimde hata verdi: {type(e).__name__}: {e}")
        if isinstance(output, str):
            output = ToolOutput(output)
        return ToolOutcome(
            result=ToolResult(tool_call_id=call.id, content=output.content),
            summary=output.summary,
            details=output.details,
        )


def _error(call: ToolCall, message: str) -> ToolOutcome:
    first_line = message.splitlines()[0] if message else ""
    return ToolOutcome(
        result=ToolResult(tool_call_id=call.id, content=message, is_error=True),
        summary=first_line[:120],
    )


def count_lines(text: str) -> int:
    return len(text.splitlines())


def short(text: str, limit: int = 80) -> str:
    """Tek satıra indirip kısaltır (zaman çizelgesi etiketleri için)."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
