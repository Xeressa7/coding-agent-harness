"""Anatomy: harness'ın hangi katmanlarının açık olduğunu söyleyen tek ayar.

Her katman tek bir alandır; katmanı sökmek o alanı değiştirmektir. Hazır
paketler (preset) katman merdiveninin basamaklarıdır: `raw` seviye 0, yani
araçsız ham model; `files` seviye 1, dosya araçları ve plan; `bash` seviye 2,
komut çalıştırma; `full` seviye 3, web erişimi. Her koşu kendi anatomisini
kaydına yazar; deney sonucu hangi kombinasyondan geldiğini söyler.

Varsayılan paket `bash`tır: komut çalıştırabilir, izin katmanı her yazma ve komut
çağrısını sorar. Ağ aracı (`fetch_url`) yalnız `full`da vardır, açıkça seçilmelidir.
`raw` hiçbir ince katman taşımaz (AGENTS.md dahil): taban ölçümü çıplak modeldir.
"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from harness.environments.local import LocalEnvironment
from harness.tools.ask_user import AskUser
from harness.tools.base import Tool, ToolRegistry
from harness.tools.bash import Bash
from harness.tools.delete_file import DeleteFile
from harness.tools.edit_file import EditFile
from harness.tools.fetch_url import FetchUrl
from harness.tools.jobs import JobOutput, JobStop
from harness.tools.list_dir import ListDir
from harness.tools.read_file import ReadFile
from harness.tools.search import Search
from harness.tools.update_plan import UpdatePlan
from harness.tools.write_file import WriteFile

ToolName = Literal[
    "read_file",
    "list_dir",
    "search",
    "write_file",
    "edit_file",
    "delete_file",
    "update_plan",
    "bash",
    "job_output",
    "job_stop",
    "fetch_url",
]

# Araç kurucuları: (çalışma dizini, anatomi, ortam). Ortam bir kayıt defterinde tektir:
# bash'in başlattığı arka plan işini job_output aynı iş tablosundan okur.
_TOOL_FACTORIES: dict[ToolName, Callable[[Path, "Anatomy", LocalEnvironment], Tool[Any]]] = {
    "read_file": lambda root, a, env: ReadFile(root=root, tab_prefix=a.tool_guidance),
    "list_dir": lambda root, a, env: ListDir(root=root),
    "search": lambda root, a, env: Search(root=root),
    "write_file": lambda root, a, env: WriteFile(root=root),
    "edit_file": lambda root, a, env: EditFile(root=root, show_region=a.tool_guidance),
    "delete_file": lambda root, a, env: DeleteFile(root=root),
    "update_plan": lambda root, a, env: UpdatePlan(),
    "bash": lambda root, a, env: Bash(
        env, root=root if a.persistent_cwd else None, guidance=a.tool_guidance
    ),
    "job_output": lambda root, a, env: JobOutput(env.jobs),
    "job_stop": lambda root, a, env: JobStop(env.jobs),
    "fetch_url": lambda root, a, env: FetchUrl(),
}
_BASH_TOOLS: tuple[ToolName, ...] = ("bash", "job_output", "job_stop")
_FILE_TOOLS: tuple[ToolName, ...] = (
    "read_file",
    "list_dir",
    "search",
    "write_file",
    "edit_file",
    "delete_file",
    "update_plan",
)


class Anatomy(BaseModel):
    """Açık katmanların listesi; kayda girer, bu yüzden değiştirilemez."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    tools: tuple[ToolName, ...] = ()
    prompt: Literal["agent", "raw"] = "agent"  # prompts/ altındaki talimat dosyası
    save_code_blocks: bool = False  # seviye 0: cevaptaki adlı kod blokları dosyaya yazılır
    loop_guard: bool = False  # takılma uyarısı (core/guards.py)
    output_limit: bool = True  # uzun araç çıktısının ortası kesilir (core/context.py)
    agents_md: bool = True  # AGENTS.md sistem talimatına eklenir (core/project_context.py)
    memory: bool = False  # agent-context hafızası: harita + dosyaya dokununca not (ctx)
    test_guard: bool = False  # eski testten doğrulama eksilince not (core/guards.py)
    narrow_scope: bool = False  # talimata "en dar yerde düzelt, testler sözleşmedir" (prompts/)
    context_prune: bool = False  # eşik aşınca eski araç sonuçları not olur (core/pruning.py)
    # bash'te `cd` sonraki komuta taşınır (tools/bash.py). Alanın varsayılanı kapalı: bu alan
    # yokken yazılmış kayıtlar o koşuların gerçeğini ("her komut kökten") söylesin.
    persistent_cwd: bool = False
    ask_user: bool = False  # belirsiz istekte kullanıcıya soru aracı (tools/ask_user.py)
    finish_check: bool = False  # "bitti"de eksik varsa bir kez hatırlatır (core/finish_check.py)
    # Araç arayüzü yönlendirmesi (G03): read_file sekmeli önek, edit_file sonucu bölgeyi gösterir,
    # bash okuma için dosya araçlarını önerir, talimat bağımsız çağrıları birlikte istemeyi söyler.
    tool_guidance: bool = False

    def tool_registry(self, root: Path) -> ToolRegistry:
        """Açık araçları, çalışma dizinine bağlı olarak kurar."""
        environment = LocalEnvironment(root)
        tools = [_TOOL_FACTORIES[name](root, self, environment) for name in self.tools]
        if self.ask_user:
            tools.append(AskUser())
        return ToolRegistry(tools)


PRESETS: dict[str, Anatomy] = {
    # Seviye 0 çıplak modeldir: proje bağlamı da eklenmez (`raw+agents_md` ile açılır).
    "raw": Anatomy(name="raw", prompt="raw", save_code_blocks=True, agents_md=False),
    "files": Anatomy(name="files", tools=_FILE_TOOLS),
    "bash": Anatomy(
        name="bash", tools=(*_FILE_TOOLS, *_BASH_TOOLS), persistent_cwd=True, ask_user=True
    ),
    "full": Anatomy(
        name="full",
        tools=(*_FILE_TOOLS, *_BASH_TOOLS, "fetch_url"),
        persistent_cwd=True,
        ask_user=True,
    ),
}
DEFAULT_PRESET = "bash"  # standart harness: komut çalıştırır, her çağrıda izin sorar

# Preset'in üstüne `+ad` ile açılıp `-ad` ile kapatılan katmanlar: `bash+loop_guard`.
# Varsayılanı açık olan katman (output_limit; bash/full'da persistent_cwd) `-ad` ile kapatılır.
LAYERS: dict[str, str] = {
    "loop_guard": "aynı araç çağrısı art arda 3 kez gelince modeli uyarır, 5'te durdurur",
    "output_limit": "40.000 karakteri aşan araç çıktısının ortasını keser, modele ve kayda söyler",
    "agents_md": "projedeki AGENTS.md'yi (yoksa CLAUDE.md) talimata ekler; ctx hafıza bölümü hariç",
    "memory": "agent-context hafızası: görev başında not haritası, dosya okununca ilgili notlar",
    "test_guard": "düzenleme görevden önce var olan bir testten doğrulama silerse modele sorar",
    "narrow_scope": "talimata ekler: en dar yerde düzelt, mevcut testler ve belgeler sözleşmedir",
    "context_prune": "bağlam 60k token'ı aşınca son 5 adım dışındaki sonuçları nota çevirir",
    "persistent_cwd": "bash'te `cd` sonraki komutlara taşınır; kök dışına çıkmaz",
    "ask_user": "belirsiz istekte kullanıcıya soru sorma aracı; -p'de cevap yok, varsayım yazar",
    "finish_check": "bitirirken: düzenlemeden sonra komut yoksa ya da plan yarımsa bir kez sorar",
    "tool_guidance": "araç arayüzü: sekmeli satır öneki, düzenleme sonucu bölge, okuma için "
    "dosya araçları, bağımsız çağrılar birlikte",
}


def compose(base: str, layers_on: set[str]) -> str:
    """Preset + açık katmanlar → ad: preset'in varsayılanından farklı olan katmanlar yazılır."""
    preset = PRESETS[base]
    changes = "".join(
        ("+" if layer in layers_on else "-") + layer
        for layer in LAYERS
        if (layer in layers_on) != getattr(preset, layer)
    )
    return base + changes


def get_preset(name: str) -> Anatomy:
    """Preset adı, ardından isteğe bağlı katman ekleri: `files`, `bash+loop_guard`."""
    base, *changes = re.split(r"(?=[+-])", name.strip())
    try:
        anatomy = PRESETS[base]
    except KeyError:
        known = ", ".join(PRESETS)
        raise ValueError(f"'{base}' adında anatomi yok. Var olanlar: {known}") from None
    update: dict[str, object] = {}
    for change in changes:
        layer = change[1:]
        if layer not in LAYERS:
            known = ", ".join(LAYERS)
            raise ValueError(f"'{layer}' adında katman yok. Var olanlar: {known}")
        update[layer] = change[0] == "+"
    if not update:
        return anatomy
    return anatomy.model_copy(update={**update, "name": name.strip()})
