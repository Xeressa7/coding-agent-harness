"""Çıktı sınırı: uzun sonuç bir kez, görünür biçimde kesilir; okuma kendi bütçesiyle gelir."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from harness.core.agent import Agent
from harness.core.anatomy import compose, get_preset
from harness.core.context import limit_output
from harness.core.events import ContextAssembled, Event, HarnessIntervened, ToolFinished
from harness.core.permissions import PermissionManager
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall, ToolResult
from harness.tools.base import Tool, ToolRegistry
from harness.tools.read_file import MAX_CHARS, MAX_LINES, ReadFile, ReadFileParams

pytestmark = pytest.mark.anyio


class NoParams(BaseModel):
    pass


class Dump(Tool[NoParams]):
    """Bütçesi olmayan bir araç gibi (bash'in dev log basması) tek seferde çok metin döner."""

    name = "dump"
    description = "test"
    params_model = NoParams
    category = "read"  # izin sormasın: konu kesme

    def __init__(self, text: str) -> None:
        self._text = text

    async def run(self, params: NoParams) -> str:
        return self._text


def test_short_output_is_left_alone() -> None:
    assert limit_output("kısa", limit=10) is None


def test_long_output_keeps_head_and_tail_and_says_how_much_was_dropped() -> None:
    text = "B" * 10 + "O" * 1000 + "S" * 10
    limited = limit_output(text, limit=20)
    assert limited is not None
    assert limited.startswith("B" * 10) and limited.endswith("S" * 10)
    assert "1.020 karakterdi" in limited and "1.000 karakter atıldı" in limited


async def run(tmp_path: Path, anatomy_name: str, content: str) -> list[Event]:
    (tmp_path / "log.txt").write_text(content, encoding="utf-8")
    anatomy = get_preset(anatomy_name)
    tools = anatomy.tool_registry(tmp_path)
    call = ToolCall(id="c1", name="read_file", arguments='{"path": "log.txt"}')
    replies = [AssistantMessage(tool_calls=(call,)), AssistantMessage(text="bitti")]
    agent = Agent(
        ScriptedLLM(replies),
        tools,
        "talimat",
        permissions=PermissionManager(tmp_path, tools),
        anatomy=anatomy,
        root=tmp_path,
    )
    return [event async for event in agent.run("oku")]


async def test_file_within_the_read_budget_reaches_the_model_whole(tmp_path: Path) -> None:
    # Eski sürüm 4.000 karakterden uzun her sonucun ortasını sessizce atıyordu.
    content = "\n".join(f"satır {i} " + "x" * 40 for i in range(400))  # ~20.000 karakter
    events = await run(tmp_path, "files", content)

    assert not [e for e in events if isinstance(e, HarnessIntervened)]
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.full_content is None and "satır 200 " in finished.result.content
    seen = [e for e in events if isinstance(e, ContextAssembled)][-1].messages
    assert any(isinstance(m, ToolResult) and "satır 200 " in m.content for m in seen)


async def test_oversized_result_is_cut_once_and_recorded_as_an_intervention(
    tmp_path: Path,
) -> None:
    content = "\n".join("y" * 1500 for _ in range(40))  # 60.039 karakter, ör. dev bir log
    tools = ToolRegistry([Dump(content)])
    call = ToolCall(id="c1", name="dump", arguments="{}")
    agent = Agent(
        ScriptedLLM([AssistantMessage(tool_calls=(call,)), AssistantMessage(text="bitti")]),
        tools,
        "talimat",
        permissions=PermissionManager(tmp_path, tools),
        anatomy=get_preset("files"),
    )
    events = [e async for e in agent.run("oku")]

    [note] = [e for e in events if isinstance(e, HarnessIntervened)]
    assert (note.layer, note.action, note.call_id) == ("output_limit", "truncate", "c1")
    assert "60.039 →" in note.summary
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert finished.full_content == content  # tam hâl kayıtta
    assert "[harness: çıktı 60.039 karakterdi" in finished.result.content  # modele giden
    assert agent.history[-2] == finished.result  # geçmişe kesilmiş hâli girdi


async def test_output_limit_layer_can_be_switched_off(tmp_path: Path) -> None:
    anatomy = get_preset("files-output_limit")
    assert not anatomy.output_limit and anatomy.name == "files-output_limit"
    defaults = {"output_limit", "agents_md"}
    assert compose("files", {"agents_md"}) == "files-output_limit"
    assert compose("files", {*defaults, "loop_guard"}) == "files+loop_guard"
    assert compose("bash", defaults) == "bash"


async def test_read_file_stops_at_its_budget_and_names_the_next_start_line(
    tmp_path: Path,
) -> None:
    (tmp_path / "uzun.py").write_text(
        "\n".join(f"x = {i}" for i in range(MAX_LINES + 500)), encoding="utf-8"
    )
    out = await ReadFile(tmp_path).run(ReadFileParams(path="uzun.py"))
    assert out.details["end"] == MAX_LINES and out.details["cut"]
    assert f"Devamı için start_line={MAX_LINES + 1}" in out.content
    assert "bütçe doldu" in out.summary

    (tmp_path / "genis.txt").write_text("\n".join("z" * 900 for _ in range(100)), encoding="utf-8")
    wide = await ReadFile(tmp_path).run(ReadFileParams(path="genis.txt", start_line=10))
    assert len(wide.content) < MAX_CHARS + 200 and wide.details["start"] == 10
    assert f"start_line={wide.details['end'] + 1}" in wide.content

    (tmp_path / "tek.js").write_text("a" * 9000, encoding="utf-8")
    one = await ReadFile(tmp_path).run(ReadFileParams(path="tek.js"))
    assert "satır 9000 karakter, kısaltıldı" in one.content and not one.details["cut"]
