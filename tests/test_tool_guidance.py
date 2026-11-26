"""`tool_guidance` katmanı (G03): araç arayüzü yönlendirmesi.

Katman kapalıyken modelin gördüğü her şey (araç açıklamaları, okuma ve düzenleme
sonuçları, talimat) eskisiyle bayt bayt aynı kalmalı: kapalı kol, pilotun ve
2.–4. turların tabanıdır. Açıkken dört değişiklik gelir: sekmeli satır öneki,
düzenleme sonucunda bölge, bash açıklamasında okuma için dosya araçları ve
talimatta bağımsız çağrıları birlikte isteme.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from harness.cli.app import load_system_prompt
from harness.core.anatomy import compose, get_preset
from harness.environments.local import LocalEnvironment
from harness.tools.bash import Bash
from harness.tools.edit_file import EditFile, EditFileParams
from harness.tools.read_file import ReadFile, ReadFileParams

pytestmark = pytest.mark.anyio

# Katman yokken (Ekim–Kasım 2026 koşuları) modele giden açıklamalar; değişirse taban değişmiştir.
READ_DESCRIPTION = (
    "Bir metin dosyasını satır numaralarıyla okur. Bir seferde en çok 2000 satır ve ~30.000 "
    "karakter gelir; devamı varsa sonuç bir sonraki start_line'ı söyler."
)
EDIT_DESCRIPTION = (
    "Dosyada tam bir kez geçen old_text'i new_text ile değiştirir; replace_all ile her "
    "geçtiği yeri. Eşleşme yoksa (ya da replace_all olmadan birden fazlaysa) hiçbir şey "
    "değişmez."
)
BASH_DESCRIPTION = (
    "Bash komutu çalıştırır; çıkış kodunu, stdout ve stderr'i döner. `cd` sonraki "
    "komutlara taşınır (kök dışına çıkmaz); dosya araçlarının yolları yine köke "
    "göredir. Komuta girdi verilmez; etkileşimli komutlar çalışmaz."
)
TOML = "[project]\nname = 'x'\n\n[extras]\n  gateway = [\n    \"otari>=0.1.0\",\n  ]\n"


def tools_of(anatomy_name: str, root: Path) -> dict[str, Any]:
    registry = get_preset(anatomy_name).tool_registry(root)
    return {spec.name: registry.get(spec.name) for spec in registry.specs()}


# --- katman kapalı: taban değişmez -------------------------------------------------------------


async def test_off_read_file_keeps_two_space_prefix_and_description(tmp_path: Path) -> None:
    (tmp_path / "p.toml").write_text(TOML, encoding="utf-8")
    tool = ReadFile(tmp_path)

    out = await tool.run(ReadFileParams(path="p.toml", start_line=5, end_line=6))

    assert out.content == '5    gateway = [\n6      "otari>=0.1.0",'
    assert tool.description == READ_DESCRIPTION


async def test_off_edit_result_is_one_line(tmp_path: Path) -> None:
    (tmp_path / "p.toml").write_text(TOML, encoding="utf-8")
    tool = EditFile(tmp_path)

    out = await tool.run(
        EditFileParams(path="p.toml", old_text='"otari>=0.1.0"', new_text='"otari>=0.2.0"')
    )

    assert out.content == "p.toml: 6. satırdaki metin değiştirildi."
    assert tool.description == EDIT_DESCRIPTION


def test_off_bash_preset_tools_and_prompt_are_unchanged(tmp_path: Path) -> None:
    tools = tools_of("bash", tmp_path)

    assert tools["read_file"].description == READ_DESCRIPTION
    assert tools["edit_file"].description == EDIT_DESCRIPTION
    assert tools["bash"].description == BASH_DESCRIPTION
    assert "tek yanıtta" not in load_system_prompt(get_preset("bash"))
    assert not get_preset("bash").tool_guidance


# --- katman açık ------------------------------------------------------------------------------


def test_layer_is_named_and_composable() -> None:
    anatomy = get_preset("bash+tool_guidance")

    assert anatomy.tool_guidance and anatomy.name == "bash+tool_guidance"
    assert (
        compose(
            "bash",
            {
                "output_limit",
                "agents_md",
                "persistent_cwd",
                "ask_user",
                "tool_call_fallback",
                "tool_guidance",
            },
        )
        == "bash+tool_guidance"
    )


async def test_on_read_file_separates_number_with_a_tab(tmp_path: Path) -> None:
    (tmp_path / "p.toml").write_text(TOML, encoding="utf-8")
    tool = tools_of("bash+tool_guidance", tmp_path)["read_file"]

    out = await tool.run(ReadFileParams(path="p.toml", start_line=5, end_line=6))

    # Girinti (iki boşluk) önekten ayrı durur: pilotta model iki boşluklu öneki girintiye kattı.
    assert out.content == '5\t  gateway = [\n6\t    "otari>=0.1.0",'
    assert "numara<TAB>içerik" in tool.description


async def test_on_edit_result_shows_the_edited_region(tmp_path: Path) -> None:
    (tmp_path / "p.toml").write_text(TOML, encoding="utf-8")
    tool = tools_of("bash+tool_guidance", tmp_path)["edit_file"]

    out = await tool.run(
        EditFileParams(path="p.toml", old_text='"otari>=0.1.0"', new_text='"otari>=0.2.0"')
    )

    assert out.content.splitlines() == [
        "p.toml: 6. satırdaki metin değiştirildi.",
        "Düzenlemeden sonra:",
        "3\t",
        "4\t[extras]",
        "5\t  gateway = [",
        '6\t    "otari>=0.2.0",',
        "7\t  ]",
    ]
    assert "satır öneki (numara + sekme) katılmaz" in tool.description


async def test_on_edit_region_is_capped_for_long_new_text(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("başla\nson\n", encoding="utf-8")
    tool = EditFile(tmp_path, show_region=True)
    body = "\n".join(f"satır {n}" for n in range(100))

    out = await tool.run(EditFileParams(path="a.txt", old_text="başla", new_text=body))

    lines = out.content.splitlines()
    assert lines[2] == " 1\tsatır 0" and len(lines) == 2 + 40 + 1
    assert lines[-1] == "[… 61 satır daha]"


async def test_on_replace_all_shows_the_first_place(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\ny = x\nz = x\n", encoding="utf-8")
    tool = EditFile(tmp_path, show_region=True)

    out = await tool.run(EditFileParams(path="a.py", old_text="x", new_text="w", replace_all=True))

    assert out.content.splitlines()[1] == "İlk değişen yer, düzenlemeden sonra:"


def test_on_bash_points_reading_to_file_tools(tmp_path: Path) -> None:
    tool = tools_of("bash+tool_guidance", tmp_path)["bash"]

    assert tool.description.startswith(BASH_DESCRIPTION)
    assert "cat/head/tail/sed yerine read_file" in tool.description
    # Kalıcı klasörsüz bash da aynı yönlendirmeyi alır.
    plain = Bash(LocalEnvironment(tmp_path), guidance=True)
    assert "grep/find yerine search" in plain.description


def test_on_prompt_asks_for_independent_calls_together() -> None:
    prompt = load_system_prompt(get_preset("bash+tool_guidance"))

    assert prompt.startswith(load_system_prompt(get_preset("bash")))
    assert "tek yanıtta birlikte iste" in prompt
    assert "<!--" not in prompt


# 2026-11-23, katman eklenmeden önceki kodla (7602fb3) aynı çıktığı doğrulanan özetler: araç
# şemaları + talimat. Değişirse deney tabanı değişmiştir; bilinçliyse güncelle ve deney notuna yaz.
BASELINE = {"bash": "201f0e2285492488", "files": "d7e9a39820f798a6", "full": "321ea9af5d4492b8"}


@pytest.mark.parametrize("name", sorted(BASELINE))
def test_off_presets_send_the_model_byte_identical_schemas_and_prompt(
    name: str, tmp_path: Path
) -> None:
    anatomy = get_preset(name)
    specs = [vars(spec) for spec in anatomy.tool_registry(tmp_path).specs()]
    blob = json.dumps(specs, ensure_ascii=False, sort_keys=True, default=str)
    blob += load_system_prompt(anatomy)

    assert hashlib.sha256(blob.encode()).hexdigest()[:16] == BASELINE[name]
