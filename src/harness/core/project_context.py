"""Proje bağlamı katmanları: AGENTS.md talimatı ve agent-context hafızası (`ctx`).

İkisi de modele harness'ın eklediği metindir; ajan her eklemeyi `HarnessIntervened`
olarak kaydeder ve eklenen metin `ContextAssembled`'da birebir görünür.

- `agents_md`: çalışma dizinindeki AGENTS.md (yoksa CLAUDE.md) sistem talimatına
  eklenir. Satırın tamamı `@dosya` ise o dosya içeri alınır (Claude Code kuralı).
- `memory`: agent-context aracı `ctx`, Claude Code'a verdiği kanca sözleşmesinin
  aynısıyla çağrılır: görev başında `ctx hook index` (not haritası), dosya
  okunduğunda/yazıldığında `ctx hook recall` (o dosyayı anan notlar). Hafızanın
  kuralları ctx'te kalır; harness yalnız taşır. ctx kurulu değilse ya da projede
  `context.yaml` yoksa katman hiçbir şey eklemez ve bunu söyler.
"""

import asyncio
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
MAX_INSTRUCTION_CHARS = 20_000
CTX_TIMEOUT_SECONDS = 10
# ctx'in AGENTS.md'ye yazdığı harita bloğu: hafıza katmanı açıkken taze haritayla çakışmasın.
_INDEX_BLOCK = re.compile(r"<!-- ctx:index:basla -->.*?<!-- ctx:index:bitir -->", re.DOTALL)


@dataclass(frozen=True)
class Instructions:
    source: str  # "AGENTS.md" ya da "CLAUDE.md → AGENTS.md"
    text: str


def load_instructions(root: Path, *, drop_memory_index: bool = False) -> Instructions | None:
    """Projenin ajan talimatı; yoksa None. Çok uzunsa kesilir ve bu söylenir."""
    for name in INSTRUCTION_FILES:
        path = root / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        source = name
        lines: list[str] = []
        inside = root.resolve()
        for line in text.splitlines():
            target = line.strip()[1:] if line.strip().startswith("@") else ""
            included = (root / target).resolve() if target else None
            if (
                included is not None
                and inside in included.parents  # proje dışı dosya içeri alınmaz
                and included.is_file()
                and included != path.resolve()
            ):
                lines.append(included.read_text(encoding="utf-8", errors="replace"))
                source = f"{name} → {target}"
            else:
                lines.append(line)
        text = "\n".join(lines)
        if drop_memory_index:
            text = _INDEX_BLOCK.sub("(hafıza haritası aşağıda, hafıza katmanından)", text)
        text = text.strip()
        if len(text) > MAX_INSTRUCTION_CHARS:
            text = text[:MAX_INSTRUCTION_CHARS] + f"\n\n[{name} kesildi: ilk 20.000 karakter]"
        return Instructions(source=source, text=text) if text else None
    return None


def _ctx_command() -> str | None:
    return shutil.which("ctx")


def _run_ctx(command: str, args: list[str], payload: dict[str, object], cwd: Path) -> str:
    done = subprocess.run(
        [command, *args],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        timeout=CTX_TIMEOUT_SECONDS,
        check=False,
    )
    return done.stdout


class MemoryUnavailable(Exception):
    """Hafıza katmanı açık ama çalışamıyor; mesaj kullanıcıya/kayda gider."""


class CtxMemory:
    """agent-context (`ctx`) kancalarını alt süreçle çağırır; çıktıdaki ek bağlamı döner."""

    def __init__(self, root: Path, session_id: str) -> None:
        self._root = root
        self._session_id = session_id  # ctx aynı notu aynı oturumda ikinci kez eklemez

    def _command(self) -> str:
        command = _ctx_command()
        if command is None:
            raise MemoryUnavailable("ctx bulunamadı (agent-context kurulu değil)")
        if not (self._root / "context.yaml").is_file():
            raise MemoryUnavailable("projede context.yaml yok (ctx bootstrap yapılmamış)")
        return command

    async def _hook(self, name: str, payload: dict[str, object]) -> str | None:
        command = self._command()
        payload = {"session_id": self._session_id, "cwd": str(self._root), **payload}
        try:
            out = await asyncio.to_thread(_run_ctx, command, ["hook", name], payload, self._root)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MemoryUnavailable(f"ctx hook {name} çalışmadı: {exc}") from exc
        if not out.strip():
            return None
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return out.strip()
        extra = data.get("hookSpecificOutput", {}).get("additionalContext")
        return extra.strip() if isinstance(extra, str) and extra.strip() else None

    async def index(self) -> str | None:
        """Not haritası (her notun tek satırı); notun kendisi değil."""
        return await self._hook("index", {"hook_event_name": "SessionStart"})

    async def recall(self, path: str, tool: str) -> str | None:
        """Okunan/değişen dosyayı adıyla anan notlar; yoksa None."""
        payload: dict[str, object] = {
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": {"file_path": str((self._root / path).resolve())},
        }
        return await self._hook("recall", payload)
