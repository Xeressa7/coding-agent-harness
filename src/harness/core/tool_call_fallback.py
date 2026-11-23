"""Düz metin araç çağrısı yakalama (`tool_call_fallback` katmanı).

Bazı zayıf yerel modeller (ör. Ollama'da qwen2.5-coder) araç çağrısını sağlayıcının
yapılandırılmış `tool_calls` alanına değil, cevap metnine düz JSON olarak yazar:
`{"name": "read_file", "arguments": {"path": "a.py"}}`. Harness bunu araç çağrısı
saymazsa model hiçbir şey yapmadan "bitti" sanılır (core/agent.py).

Bu katman yalnız şu üç şartı birlikte karşılayan metni araç çağrısı sayar: bilinen
bir araç adı, o aracın şemasına uyan argümanlar, geçerli JSON. Başka her şey (düz
cümle, örnek kod, bilinmeyen ad, bozuk şema) sessizce yok sayılır — amaç modelin
gerçek niyetini tahmin etmek değil, zaten açık bir niyeti kaçırmamaktır.
"""

import json
import re
from typing import cast

from pydantic import ValidationError

from harness.messages import ToolCall
from harness.tools.base import ToolRegistry

_FENCE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


def recover_tool_calls(text: str, tools: ToolRegistry) -> tuple[ToolCall, ...]:
    """Metindeki araç çağrısı biçimli JSON'u ayıklar; yoksa boş tuple döner.

    Önce bütün metni (olası ```json çiti soyularak) tek çağrı olarak dener — tek
    satıra ya da güzelce biçimlenmiş çok satıra yayılmış tek bir JSON nesnesini
    kapsar. Olmazsa satır satır dener — art arda yazılmış birden çok tek satırlık
    çağrıyı kapsar.
    """
    whole = _parse_one(_unfenced(text.strip()), tools, "recovered_0")
    if whole is not None:
        return (whole,)
    calls: list[ToolCall] = []
    for i, line in enumerate(text.strip().splitlines()):
        call = _parse_one(line.strip(), tools, f"recovered_{i}")
        if call is not None:
            calls.append(call)
    return tuple(calls)


def _unfenced(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1).strip() if match else text


def _parse_one(candidate: str, tools: ToolRegistry, call_id: str) -> ToolCall | None:
    if not candidate:
        return None
    try:
        parsed: object = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    fields = cast(dict[str, object], parsed)
    name = fields.get("name")
    arguments = fields.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return None
    tool = tools.get(name)
    if tool is None:
        return None
    arguments_json = json.dumps(arguments)
    try:
        tool.params_model.model_validate_json(arguments_json)
    except ValidationError:
        return None
    return ToolCall(id=call_id, name=name, arguments=arguments_json)
