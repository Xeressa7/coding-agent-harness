"""Tool arayüzü ve ToolRegistry.

Her aracın adı, açıklaması, girdi şeması (Pydantic modelinden üretilen JSON
Schema) ve çalıştırma fonksiyonu vardır. Kayıt defteri araçları tutar,
şemalarını modele sunar ve çağrıyı doğru araca yönlendirir. Araç hatası
istisna olarak fırlamaz; hata mesajı araç sonucu olarak modele döner.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from harness.messages import ToolCall, ToolResult, ToolSpec


class ToolError(Exception):
    """Aracın modele açıklamak istediği beklenen hata (dosya yok, yol izinsiz...)."""


def resolve_inside(root: Path, raw: str) -> Path:
    """Modelin verdiği yolu çalışma dizinine göre çözer; dışarı çıkıyorsa reddeder.

    Asıl izin katmanı core/permissions.py'de olacak; bu, araçların kendi emniyet kemeri.
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

    @abstractmethod
    async def run(self, params: P) -> str:
        """Aracı çalıştırır, modele gidecek metni döner. Beklenen hatada ToolError fırlatır."""

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

    async def call(self, call: ToolCall) -> ToolResult:
        """İsteği çalıştırır. Ne olursa olsun bir ToolResult döner, istisna fırlatmaz."""
        tool = self._tools.get(call.name)
        if tool is None:
            known = ", ".join(sorted(self._tools))
            return _error(call, f"'{call.name}' adında araç yok. Var olanlar: {known}")
        try:
            params = tool.params_model.model_validate_json(call.arguments)
        except ValidationError as e:
            return _error(call, f"Argümanlar geçersiz: {e}")
        try:
            content = await tool.run(params)
        except ToolError as e:
            return _error(call, str(e))
        except Exception as e:
            # Beklenmeyen hata da modele döner; döngü çökmez, model başka yol dener.
            return _error(call, f"Araç beklenmedik biçimde hata verdi: {type(e).__name__}: {e}")
        return ToolResult(tool_call_id=call.id, content=content)


def _error(call: ToolCall, message: str) -> ToolResult:
    return ToolResult(tool_call_id=call.id, content=message, is_error=True)
