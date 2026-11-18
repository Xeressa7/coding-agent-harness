"""Kalıcı çalışma klasörü: `cd` sonraki komuta taşınır, kök dışına çıkmaz, çıktı temiz kalır."""

import json
from pathlib import Path

import pytest

from harness.core.anatomy import get_preset
from harness.environments.local import LocalEnvironment, find_bash
from harness.messages import ToolCall
from harness.tools.base import ToolRegistry
from harness.tools.bash import Bash

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(find_bash() is None, reason="bash yok"),
]


def bash(command: str, call_id: str = "c") -> ToolCall:
    return ToolCall(id=call_id, name="bash", arguments=json.dumps({"command": command}))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "frontend" / "paket.json").write_text("{}", encoding="utf-8")
    (tmp_path / "kök.txt").write_text("x", encoding="utf-8")
    return tmp_path


async def test_cd_carries_over_to_the_next_command(project: Path) -> None:
    tool = Bash(LocalEnvironment(project), root=project)
    registry = ToolRegistry([tool])

    first = await registry.call(bash("cd frontend && echo tamam"))
    second = await registry.call(bash("ls"))

    assert tool.cwd == "frontend"
    assert first.content == "çıkış kodu: 0\n--- stdout ---\ntamam\n(çalışma klasörü: frontend/)"
    assert "paket.json" in second.content and "kök.txt" not in second.content
    assert "__harness_cwd" not in first.content + second.content  # işaret satırı modele gitmez
    await registry.call(bash("cd src"))
    assert tool.cwd == "frontend/src"
    await registry.call(bash("cd ../.."))
    root_listing = await registry.call(bash("ls"))
    assert tool.cwd == "." and "kök.txt" in root_listing.content
    assert "çalışma klasörü" not in root_listing.content  # kökteyken satır eklenmez


async def test_leaving_the_root_is_not_carried_over(project: Path) -> None:
    tool = Bash(LocalEnvironment(project), root=project)
    registry = ToolRegistry([tool])
    await registry.call(bash("cd frontend"))

    result = await registry.call(bash("cd / && pwd"))

    assert tool.cwd == "frontend"
    assert "kök dışında bitti" in result.content and "frontend/ klasöründe başlar" in result.content


async def test_exit_code_and_output_without_newline_survive(project: Path) -> None:
    tool = Bash(LocalEnvironment(project), root=project)
    registry = ToolRegistry([tool])

    result = await registry.call(bash("cd frontend; printf yarım; exit 3"))

    assert tool.cwd == "frontend"  # exit de kancayı çalıştırır
    assert result.content.startswith("çıkış kodu: 3\n--- stdout ---\nyarım")


async def test_deleted_folder_falls_back_to_the_root(project: Path) -> None:
    tool = Bash(LocalEnvironment(project), root=project)
    registry = ToolRegistry([tool])
    await registry.call(bash("mkdir gecici && cd gecici"))
    (project / "gecici").rmdir()

    result = await registry.call(bash("ls"))

    assert "gecici/ klasörü yok" in result.content and "kök.txt" in result.content
    assert tool.cwd == "."


async def test_the_layer_is_on_in_bash_and_can_be_switched_off(project: Path) -> None:
    on = get_preset("bash")
    off = get_preset("bash-persistent_cwd")
    assert on.persistent_cwd and not off.persistent_cwd
    assert not get_preset("files").persistent_cwd

    registry = off.tool_registry(project)
    await registry.call(bash("cd frontend"))
    listing = await registry.call(bash("ls"))

    assert "kök.txt" in listing.content  # eski davranış: her komut kökten
