"""read_file ve kayıt defterinin sözleri: hata asla istisna olarak fırlamaz, modele döner."""

from pathlib import Path

import pytest

from harness.messages import ToolCall
from harness.tools.base import ToolRegistry
from harness.tools.read_file import ReadFile

pytestmark = pytest.mark.anyio


@pytest.fixture
def registry(tmp_path: Path) -> ToolRegistry:
    (tmp_path / "a.txt").write_text("bir\niki\nüç\ndört\n", encoding="utf-8")
    return ToolRegistry([ReadFile(root=tmp_path)])


def call(arguments: str, name: str = "read_file") -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=arguments)


async def test_reads_with_line_numbers(registry: ToolRegistry) -> None:
    result = await registry.call(call('{"path": "a.txt"}'))

    assert not result.is_error
    assert result.content.splitlines() == ["1  bir", "2  iki", "3  üç", "4  dört"]


async def test_reads_a_line_range(registry: ToolRegistry) -> None:
    result = await registry.call(call('{"path": "a.txt", "start_line": 2, "end_line": 3}'))

    assert result.content.splitlines() == ["2  iki", "3  üç"]


async def test_missing_file_is_an_error_result(registry: ToolRegistry) -> None:
    result = await registry.call(call('{"path": "yok.txt"}'))

    assert result.is_error
    assert "yok.txt" in result.content


async def test_path_outside_working_directory_is_refused(registry: ToolRegistry) -> None:
    result = await registry.call(call('{"path": "../../etc/passwd"}'))

    assert result.is_error
    assert "dışında" in result.content


async def test_broken_json_arguments_are_an_error_result(registry: ToolRegistry) -> None:
    result = await registry.call(call('{"path": '))

    assert result.is_error
    assert "geçersiz" in result.content


async def test_unknown_tool_lists_the_known_ones(registry: ToolRegistry) -> None:
    result = await registry.call(call("{}", name="sil"))

    assert result.is_error
    assert "read_file" in result.content


def test_spec_describes_parameters_to_the_model(registry: ToolRegistry) -> None:
    (spec,) = registry.specs()

    assert spec.name == "read_file"
    assert spec.parameters["required"] == ["path"]
