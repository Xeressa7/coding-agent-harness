"""fetch_url: bir web sayfasını indirir ve okunur metne çevirir.

Yalnız http/https. HTML'den script/stil atılır, etiketler kaldırılır, boşluklar
sadeleşir. Diğer araçlardan farklı olarak bir üst sınır vardır: bir sayfa
megabaytlarca olabilir ve bu araç belleği korumak zorunda; kesildiyse sonuç
bunu açıkça söyler. Ağ isteği dışarıya veri taşıyabileceği için izin katmanı
her çağrıyı sorar.
"""

from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from harness.tools.base import Tool, ToolError, ToolOutput, short

_DEFAULT_CHARS = 20_000
_MAX_CHARS = 100_000
_MAX_BYTES = 2_000_000  # indirme sınırı: HTML çok büyük olsa da metni bundan çıkarılır


class FetchUrlParams(BaseModel):
    url: str = Field(description="http ya da https adresi")
    max_chars: int = Field(
        default=_DEFAULT_CHARS, ge=500, le=_MAX_CHARS, description="Döndürülecek en fazla karakter"
    )


class FetchUrl(Tool[FetchUrlParams]):
    name = "fetch_url"
    description = (
        "Bir web sayfasını indirir, okunur metin olarak döner (HTML etiketleri atılır). "
        "Belge, sürüm notu ya da hata mesajı aramak için kullan."
    )
    params_model = FetchUrlParams
    category = "network"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        # Testler ağa çıkmadan sahte bir taşıyıcı verir.
        self._transport = transport

    def describe(self, params: FetchUrlParams) -> str:
        return short(params.url, 70)

    async def run(self, params: FetchUrlParams) -> ToolOutput:
        parsed = urlparse(params.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ToolError(f"{params.url}: yalnız http/https adresleri okunabilir.")
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=20, follow_redirects=True
            ) as http:
                response = await http.get(params.url, headers={"User-Agent": "harness/0.1"})
        except httpx.HTTPError as e:
            raise ToolError(f"{params.url}: indirilemedi ({type(e).__name__}: {e}).") from e
        raw = response.content[:_MAX_BYTES]
        kind = response.headers.get("content-type", "").split(";")[0].strip() or "?"
        text = raw.decode(response.encoding or "utf-8", errors="replace")
        if "html" in kind:
            text = html_to_text(text)
        cut = len(text) > params.max_chars
        body = text[: params.max_chars]
        header = f"URL: {response.url}\nDurum: {response.status_code} · {kind} · {len(raw)} bayt"
        note = f"\n\n… (+{len(text) - params.max_chars} karakter kesildi)" if cut else ""
        if response.status_code >= 400:
            raise ToolError(f"{header}\n\n{body}{note}")
        return ToolOutput(
            f"{header}\n\n{body}{note}",
            summary=f"{response.status_code} · {len(body)} karakter"
            + (" (kesildi)" if cut else ""),
            details={
                "status": response.status_code,
                "content_type": kind,
                "bytes": len(raw),
                "chars": len(body),
                "truncated": cut,
            },
        )


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head"}
    _BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    return "\n".join(line for line in lines if line)
