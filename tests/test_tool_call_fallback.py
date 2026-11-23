"""Düz metin araç çağrısı yakalama: bilinen ad + şemaya uyan argüman dışında her şey yok sayılır."""

from pathlib import Path

from harness.core.tool_call_fallback import recover_tool_calls
from harness.tools.base import ToolRegistry
from harness.tools.read_file import ReadFile
from harness.tools.write_file import WriteFile


def registry(tmp_path: Path) -> ToolRegistry:
    return ToolRegistry([ReadFile(root=tmp_path), WriteFile(root=tmp_path)])


def test_single_line_json_call_is_recovered(tmp_path: Path) -> None:
    text = '{"name": "read_file", "arguments": {"path": "a.py"}}'
    [call] = recover_tool_calls(text, registry(tmp_path))
    assert call.name == "read_file"
    assert call.arguments == '{"path": "a.py"}'


def test_pretty_printed_json_across_several_lines_is_recovered(tmp_path: Path) -> None:
    text = '{\n  "name": "read_file",\n  "arguments": {\n    "path": "a.py"\n  }\n}'
    [call] = recover_tool_calls(text, registry(tmp_path))
    assert call.name == "read_file"


def test_fenced_json_block_is_unwrapped(tmp_path: Path) -> None:
    text = '```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```'
    [call] = recover_tool_calls(text, registry(tmp_path))
    assert call.name == "read_file"


def test_several_single_line_calls_are_recovered_in_order(tmp_path: Path) -> None:
    text = (
        '{"name": "read_file", "arguments": {"path": "a.py"}}\n'
        '{"name": "write_file", "arguments": {"path": "b.py", "content": "x = 1\\n"}}'
    )
    calls = recover_tool_calls(text, registry(tmp_path))
    assert [c.name for c in calls] == ["read_file", "write_file"]


def test_unknown_tool_name_is_ignored(tmp_path: Path) -> None:
    text = '{"name": "delete_everything", "arguments": {}}'
    assert recover_tool_calls(text, registry(tmp_path)) == ()


def test_arguments_not_matching_the_schema_are_ignored(tmp_path: Path) -> None:
    # read_file "path" ister; burada yalnız ilgisiz bir alan var.
    text = '{"name": "read_file", "arguments": {"not_a_field": 1}}'
    assert recover_tool_calls(text, registry(tmp_path)) == ()


def test_plain_prose_is_ignored(tmp_path: Path) -> None:
    text = "Dosyayı okudum, her şey yolunda görünüyor."
    assert recover_tool_calls(text, registry(tmp_path)) == ()


def test_empty_text_is_ignored(tmp_path: Path) -> None:
    assert recover_tool_calls("", registry(tmp_path)) == ()
