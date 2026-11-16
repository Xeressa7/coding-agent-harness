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

İki katman birbirine karışmaz: `ctx init`'in AGENTS.md'ye yazdığı hafıza bölümü
(haritayı taşıyan başlık + `ctx:index` bloğu) `agents_md` katmanına hiç girmez.
Hafıza kapalıyken modele hafızadan söz edilmez; açıkken kuralları ve taze haritayı
ctx'in kendi kancası verir. AGENTS.md'ye dokunmadan iki katman ayrı ayrı açılıp
kapanır ve farkları ölçülebilir.
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
# ctx'in AGENTS.md'ye yazdığı bölüm: haritayı taşıyan başlık (şablonda "## Hafıza") ve
# işaretli harita bloğu. Bölümün başı = bloktan önceki son başlık, sonu = blok sonu.
_INDEX_START = "<!-- ctx:index:basla -->"
_INDEX_END = "<!-- ctx:index:bitir -->"
_HEADING = re.compile(r"^#{1,6} ", re.MULTILINE)


@dataclass(frozen=True)
class Instructions:
    source: str  # "AGENTS.md" ya da "CLAUDE.md → AGENTS.md"
    text: str
    memory_chars: int = 0  # ayrılan ctx hafıza bölümünün boyu (0: bölüm yoktu)


def split_memory_section(text: str) -> tuple[str, str]:
    """AGENTS.md metnini (talimat, ctx hafıza bölümü) diye ayırır; bölüm yoksa ikincisi boş."""
    start = text.find(_INDEX_START)
    end = text.find(_INDEX_END, start)
    if start < 0 or end < 0:
        return text, ""
    end += len(_INDEX_END)
    headings = [m.start() for m in _HEADING.finditer(text, 0, start)]
    begin = headings[-1] if headings else start
    return text[:begin].rstrip() + "\n\n" + text[end:].lstrip(), text[begin:end]


def load_instructions(root: Path) -> Instructions | None:
    """Projenin ajan talimatı, ctx hafıza bölümü ayrılmış; dosya yoksa None.

    Dosya yalnız ctx bölümünden oluşuyorsa `text` boş döner (katman bunu söyler).
    Çok uzunsa kesilir ve bu metne yazılır.
    """
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
        text, memory = split_memory_section("\n".join(lines))
        text = text.strip()
        if len(text) > MAX_INSTRUCTION_CHARS:
            text = text[:MAX_INSTRUCTION_CHARS] + f"\n\n[{name} kesildi: ilk 20.000 karakter]"
        if not text and not memory:
            return None
        return Instructions(source=source, text=text, memory_chars=len(memory))
    return None


def ctx_command() -> str | None:
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
        command = ctx_command()
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
