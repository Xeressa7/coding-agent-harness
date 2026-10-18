"""Araçların ve kayıt defterinin sözleri: hata asla istisna olarak fırlamaz, modele döner."""

import json
from pathlib import Path

import pytest

from harness.environments.base import CommandResult
from harness.messages import ToolCall
from harness.tools.base import ToolRegistry
from harness.tools.bash import Bash
from harness.tools.edit_file import EditFile
from harness.tools.read_file import ReadFile
from harness.tools.search import Search
from harness.tools.write_file import WriteFile

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


# --- write_file / edit_file / search / bash ---


def tool_call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="c1", name=name, arguments=json.dumps(arguments, ensure_ascii=False))


@pytest.fixture
def file_tools(tmp_path: Path) -> ToolRegistry:
    return ToolRegistry([WriteFile(tmp_path), EditFile(tmp_path), Search(tmp_path)])


async def test_write_creates_folders_and_keeps_newlines_as_written(
    file_tools: ToolRegistry, tmp_path: Path
) -> None:
    result = await file_tools.call(tool_call("write_file", path="yeni/a.txt", content="bir\niki\n"))

    assert not result.is_error and "oluşturuldu (2 satır)" in result.content
    assert (tmp_path / "yeni" / "a.txt").read_bytes() == b"bir\niki\n"

    again = await file_tools.call(tool_call("write_file", path="yeni/a.txt", content="üç"))
    assert "üzerine yazıldı" in again.content


async def test_write_outside_working_directory_is_refused(
    file_tools: ToolRegistry, tmp_path: Path
) -> None:
    result = await file_tools.call(tool_call("write_file", path="../dışarı.txt", content="x"))

    assert result.is_error and "dışında" in result.content
    assert not (tmp_path.parent / "dışarı.txt").exists()


async def test_edit_replaces_the_single_match_and_reports_its_line(
    file_tools: ToolRegistry, tmp_path: Path
) -> None:
    (tmp_path / "a.py").write_bytes(b"x = 1\ny = 2\nz = 3\n")

    result = await file_tools.call(
        tool_call("edit_file", path="a.py", old_text="y = 2", new_text="y = 20")
    )

    assert not result.is_error and "2. satır" in result.content
    assert (tmp_path / "a.py").read_bytes() == b"x = 1\ny = 20\nz = 3\n"


async def test_edit_with_no_or_many_matches_changes_nothing(
    file_tools: ToolRegistry, tmp_path: Path
) -> None:
    (tmp_path / "a.py").write_bytes(b"a = 1\na = 1\n")

    missing = await file_tools.call(tool_call("edit_file", path="a.py", old_text="b", new_text="c"))
    many = await file_tools.call(
        tool_call("edit_file", path="a.py", old_text="a = 1", new_text="c")
    )

    assert missing.is_error and "yeniden oku" in missing.content
    assert many.is_error and "2 yerde" in many.content
    assert (tmp_path / "a.py").read_bytes() == b"a = 1\na = 1\n"


async def test_edit_matches_model_newlines_in_a_windows_file(
    file_tools: ToolRegistry, tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_bytes(b"bir\r\niki\r\n")

    result = await file_tools.call(
        tool_call("edit_file", path="a.txt", old_text="bir\niki", new_text="1\n2")
    )

    assert not result.is_error
    assert (tmp_path / "a.txt").read_bytes() == b"1\r\n2\r\n"


async def test_search_finds_lines_and_skips_virtualenv_and_git(
    file_tools: ToolRegistry, tmp_path: Path
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "gemi.py").write_text("def yelken():\n    renk = 'turuncu'\n", "utf-8")
    (tmp_path / "notlar.md").write_text("Turuncu yelken\n", "utf-8")
    for skipped in (".venv", ".git"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "x.py").write_text("turuncu\n", "utf-8")

    lines = await file_tools.call(tool_call("search", pattern="turuncu", ignore_case=True))
    only_py = await file_tools.call(tool_call("search", pattern="turuncu", glob="src/**/*.py"))
    names = await file_tools.call(tool_call("search", glob="*.py"))

    assert lines.content.splitlines() == [
        "notlar.md:1: Turuncu yelken",
        "src/gemi.py:2:     renk = 'turuncu'",
    ]
    assert only_py.content == "src/gemi.py:2:     renk = 'turuncu'"
    assert names.content == "src/gemi.py"


async def test_search_reports_bad_regex_and_caps_results(
    file_tools: ToolRegistry, tmp_path: Path
) -> None:
    (tmp_path / "log.txt").write_text("satır\n" * 250, "utf-8")

    bad = await file_tools.call(tool_call("search", pattern="("))
    capped = await file_tools.call(tool_call("search", pattern="satır"))

    assert bad.is_error and "regex" in bad.content
    assert len(capped.content.splitlines()) == 201
    assert "+50 sonuç daha" in capped.content


class FakeEnvironment:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.commands: list[tuple[str, float]] = []

    async def run(self, command: str, timeout_seconds: float) -> CommandResult:
        self.commands.append((command, timeout_seconds))
        return self.result


async def test_bash_shows_exit_code_and_both_streams_without_calling_it_an_error() -> None:
    env = FakeEnvironment(CommandResult(stdout="çıktı\n", stderr="uyarı\n", exit_code=1))

    result = await ToolRegistry([Bash(env)]).call(tool_call("bash", command="make"))

    assert not result.is_error  # komut çalıştı; başarısızlığını model çıkış kodundan okur
    assert result.content == "çıkış kodu: 1\n--- stdout ---\nçıktı\n--- stderr ---\nuyarı"
    assert env.commands == [("make", 60)]


async def test_bash_timeout_is_an_error_that_keeps_partial_output() -> None:
    env = FakeEnvironment(CommandResult(stdout="yarım", stderr="", exit_code=None, timed_out=True))

    result = await ToolRegistry([Bash(env)]).call(tool_call("bash", command="x", timeout_seconds=5))

    assert result.is_error
    assert "5 sn içinde bitmedi" in result.content and "yarım" in result.content
