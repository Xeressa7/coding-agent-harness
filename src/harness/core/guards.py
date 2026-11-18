"""Koruyucular: takılma uyarısı (RepeatGuard) ve test koruyucusu (TestGuard).

Takılma uyarısı: aynı araç çağrısı art arda tekrarlanınca modeli uyarır, sonra durdurur.

Model bazen aynı dosyayı aynı satırlarla tekrar tekrar okur ya da aynı komutu
yeniden çalıştırır; sonuç değişmediği için ilerleyemez. Bu katman ardışık özdeş
çağrıları (aynı ad + aynı argümanlar) sayar: `warn_after`. tekrarda çağrı çalışır
ama sonucuna bir hatırlatma eklenir; `block_after`. tekrarda çağrı hiç çalışmaz,
modele hata döner. Farklı bir çağrı sayacı sıfırlar. Model çağrısı yapmaz.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from harness.messages import ToolCall

GuardAction = Literal["warn", "block"]


@dataclass(frozen=True)
class GuardVerdict:
    action: GuardAction
    repeats: int

    def message(self, tool: str) -> str:
        if self.action == "block":
            return (
                f"[harness] `{tool}` aynı argümanlarla art arda {self.repeats}. kez çağrıldı; "
                "çağrı çalıştırılmadı. Sonuç değişmeyecek: başka bir yol dene ya da işi bitir."
            )
        return (
            f"[harness] `{tool}` aynı argümanlarla art arda {self.repeats}. kez çağrıldı. "
            "Önceki sonuca bak; yöntemini değiştir ya da işi bitir."
        )


class RepeatGuard:
    """Ardışık özdeş araç çağrılarını sayar; her görev başında yenisi kurulur."""

    def __init__(self, warn_after: int = 3, block_after: int = 5) -> None:
        self._warn_after = warn_after
        self._block_after = block_after
        self._last: tuple[str, str] | None = None
        self._repeats = 0

    def check(self, call: ToolCall) -> GuardVerdict | None:
        signature = (call.name, _normalized(call.arguments))
        self._repeats = self._repeats + 1 if signature == self._last else 1
        self._last = signature
        if self._repeats >= self._block_after:
            return GuardVerdict("block", self._repeats)
        if self._repeats >= self._warn_after:
            return GuardVerdict("warn", self._repeats)
        return None


def _normalized(arguments: str) -> str:
    """`{"a":1, "b":2}` ile `{"b": 2, "a": 1}` aynı çağrıdır."""
    try:
        return json.dumps(json.loads(arguments), sort_keys=True, ensure_ascii=False)
    except ValueError:
        return arguments


# --------------------------------------------------------------- test koruyucusu
# Ajan bazen istenmeyen bir davranış değişikliği yapıp onunla çakışan ESKİ testteki doğrulamayı
# yeniden yazıyor; testi kırmızı görmeden, kaynağı düzenledikten hemen sonra (2. tur, 2026-11-18).
# Bu katman dosya araçlarıyla yapılan düzenlemede, görevden önce var olan bir test dosyasından
# doğrulama satırı eksilirse sonuca bir not ekler. Engellemez: eski testi değiştirmek bazen
# doğrudur (istek o davranışın değişmesini açıkça istiyorsa). bash ile yapılan düzenleme görülmez.

_TEST_NAME = re.compile(r"(\.(test|spec)\.[^.]+$)|(^test_.*\.py$)|(_test\.(py|go)$)", re.IGNORECASE)
_TEST_DIRS = {"tests", "test", "__tests__"}
_ASSERTION = re.compile(
    r"\bexpect\s*\(|\bassert\w*\b|\bself\.assert\w+|\brequire\.\w+\(|\bt\.(Error|Errorf|Fatal|Fatalf|Fail)\b"
    r"|\.(to|not\.to)[A-Z]\w*\(|\bpytest\.raises\b"
)
_FILE_TOOLS = {"edit_file", "write_file", "delete_file"}
_SHOWN = 3


def is_test_file(path: str) -> bool:
    parts = Path(path.replace("\\", "/")).parts
    if not parts:
        return False
    return _TEST_NAME.search(parts[-1]) is not None or bool(_TEST_DIRS & set(parts[:-1]))


def assertion_lines(text: str) -> Counter[str]:
    """Doğrulama satırları (baştaki/sondaki boşluk atılmış) çoklu küme olarak."""
    return Counter(s for line in text.splitlines() if _ASSERTION.search(s := line.strip()))


@dataclass(frozen=True)
class TestGuardVerdict:
    path: str
    removed: tuple[str, ...]  # yeni eksilen satırlar
    total: int  # görev başındaki hâle göre eksik doğrulama sayısı

    def message(self) -> str:
        shown = "\n".join(f"  {line[:160]}" for line in self.removed[:_SHOWN])
        extra = len(self.removed) - _SHOWN
        more = f"\n  … ve {extra} satır daha" if extra > 0 else ""
        return (
            f"[harness] Bu düzenleme `{self.path}` dosyasında görevden önce var olan "
            f"{len(self.removed)} doğrulamayı kaldırdı ya da değiştirdi:\n{shown}{more}\n"
            "İstek bu davranışın değişmesini açıkça istiyor mu? İstemiyorsa kaynak değişikliğini "
            "daralt ve doğrulamayı eski hâline getir; istiyorsa devam et ve özetine "
            '"Değişen sözleşme: …" satırı yaz.'
        )


class TestGuard:
    """Test dosyalarının görevdeki ilk dokunuştan önceki doğrulamalarını hatırlar."""

    __test__ = False  # pytest bunu test sınıfı sanmasın

    def __init__(self, root: Path) -> None:
        self._root = root
        self._baseline: dict[Path, Counter[str] | None] = {}  # None: dosya görevde doğdu
        self._warned: dict[Path, Counter[str]] = {}

    def _target(self, call: ToolCall) -> tuple[str, Path] | None:
        if call.name not in _FILE_TOOLS:
            return None
        try:
            path = json.loads(call.arguments).get("path")
        except (ValueError, AttributeError):
            return None
        if not isinstance(path, str) or not is_test_file(path):
            return None
        return path, (self._root / path).resolve()

    def before(self, call: ToolCall) -> None:
        """Araç çalışmadan önce: dosyanın ilk hâli kaydedilir (yoksa yeni dosyadır)."""
        target = self._target(call)
        if target is None or target[1] in self._baseline:
            return
        file = target[1]
        self._baseline[file] = assertion_lines(_read(file)) if file.is_file() else None

    def after(self, call: ToolCall) -> TestGuardVerdict | None:
        """Araç çalıştıktan sonra: ilk hâle göre yeni eksilen doğrulama varsa karar döner."""
        target = self._target(call)
        if target is None:
            return None
        shown, file = target
        baseline = self._baseline.get(file)
        if baseline is None:
            return None
        current = assertion_lines(_read(file)) if file.is_file() else Counter[str]()
        missing = baseline - current
        fresh = missing - self._warned.get(file, Counter())
        self._warned[file] = missing  # geri getirilen satır yeniden eksilirse yine uyarılır
        if not fresh:
            return None
        return TestGuardVerdict(path=shown, removed=tuple(fresh.elements()), total=missing.total())


def _read(file: Path) -> str:
    try:
        return file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
