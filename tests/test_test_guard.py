"""Test koruyucusu ve dar kapsam talimatı: eski testten doğrulama eksilince model uyarılır."""

import json
from pathlib import Path

import pytest

from harness.cli.app import load_system_prompt
from harness.core.agent import Agent
from harness.core.anatomy import get_preset
from harness.core.events import Event, HarnessIntervened, ToolFinished
from harness.core.guards import TestGuard, assertion_lines, is_test_file
from harness.core.permissions import PermissionManager
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall

pytestmark = pytest.mark.anyio

OLD_TEST = """\
describe('geriSayimMetni', () => {
  it('bilinmeyen 00:00', () => {
    expect(geriSayimMetni(0)).toBe('00:00');
    expect(geriSayimMetni(undefined)).toBe('00:00');
  });
});
"""


def edit(call_id: str, path: str, old: str, new: str) -> ToolCall:
    arguments = json.dumps({"path": path, "old_text": old, "new_text": new})
    return ToolCall(id=call_id, name="edit_file", arguments=arguments)


def write(call_id: str, path: str, content: str) -> ToolCall:
    return ToolCall(
        id=call_id, name="write_file", arguments=json.dumps({"path": path, "content": content})
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("frontend/src/lib/kapaliOturum.test.js", True),
        ("src/App.spec.tsx", True),
        ("tests/helpers.py", True),
        ("test_tarama.py", True),
        ("go/internal/cli/init_test.go", True),
        ("src\\lib\\format.test.js", True),
        ("src/lib/kapaliOturum.js", False),
        ("go/internal/cli/init.go", False),
        ("docs/testing.md", False),
    ],
)
def test_recognizes_test_files_by_name_or_folder(path: str, expected: bool) -> None:
    assert is_test_file(path) is expected


def test_assertion_lines_cover_js_python_and_go() -> None:
    text = """\
    expect(a).toBe(1);
    await expect(p).rejects.toThrow();
    assert x == 2
    self.assertEqual(a, b)
    with pytest.raises(ValueError):
    t.Errorf("got %v", got)
    require.NoError(t, err)
    const a = 1;
    it('başlık', () => {
"""
    assert sorted(assertion_lines(text)) == sorted(
        [
            "expect(a).toBe(1);",
            "await expect(p).rejects.toThrow();",
            "assert x == 2",
            "self.assertEqual(a, b)",
            "with pytest.raises(ValueError):",
            't.Errorf("got %v", got)',
            "require.NoError(t, err)",
        ]
    )


def run_edit(guard: TestGuard, root: Path, call: ToolCall, before: str, after: str | None):
    file = root / json.loads(call.arguments)["path"]
    guard.before(call)
    if after is None:
        file.unlink()
    else:
        file.write_text(after, encoding="utf-8")
    return guard.after(call)


def test_warns_once_per_removed_assertion_and_again_after_restore(tmp_path: Path) -> None:
    (tmp_path / "a.test.js").write_text(OLD_TEST, encoding="utf-8")
    guard = TestGuard(tmp_path)
    removed_line = "    expect(geriSayimMetni(undefined)).toBe('00:00');\n"
    without = OLD_TEST.replace(removed_line, "")

    first = run_edit(guard, tmp_path, edit("1", "a.test.js", "x", "y"), OLD_TEST, without)
    assert first is not None
    assert (
        first.removed == ("expect(geriSayimMetni(undefined)).toBe('00:00');",) and first.total == 1
    )
    assert "Değişen sözleşme" in first.message() and "a.test.js" in first.message()

    added = without.replace("  });", "    expect(yeni()).toBe(1);\n  });")
    again = run_edit(guard, tmp_path, edit("2", "a.test.js", "x", "y"), without, added)
    assert again is None  # aynı eksik satır için ikinci kez uyarılmaz; ekleme uyarı değildir

    assert run_edit(guard, tmp_path, edit("3", "a.test.js", "x", "y"), added, OLD_TEST) is None
    back = run_edit(guard, tmp_path, edit("4", "a.test.js", "x", "y"), OLD_TEST, without)
    assert back is not None  # geri getirilip yeniden silinirse yine uyarılır


def test_changed_assertion_counts_as_removed(tmp_path: Path) -> None:
    (tmp_path / "a.test.js").write_text(OLD_TEST, encoding="utf-8")
    changed = OLD_TEST.replace("toBe('00:00');\n  });", "toBeNull();\n  });")
    verdict = run_edit(
        TestGuard(tmp_path), tmp_path, edit("1", "a.test.js", "x", "y"), OLD_TEST, changed
    )
    assert verdict is not None and verdict.removed == (
        "expect(geriSayimMetni(undefined)).toBe('00:00');",
    )


def test_ignores_new_test_files_source_files_and_non_assertion_edits(tmp_path: Path) -> None:
    guard = TestGuard(tmp_path)
    created = write("1", "yeni.test.js", OLD_TEST)
    guard.before(created)  # dosya görevde doğdu: koruma yok
    (tmp_path / "yeni.test.js").write_text(OLD_TEST, encoding="utf-8")
    assert guard.after(created) is None
    assert run_edit(guard, tmp_path, edit("2", "yeni.test.js", "x", "y"), OLD_TEST, "") is None

    (tmp_path / "kaynak.js").write_text("assert(1)\n", encoding="utf-8")
    assert run_edit(guard, tmp_path, edit("3", "kaynak.js", "x", "y"), "assert(1)\n", "") is None

    (tmp_path / "b.test.js").write_text(OLD_TEST, encoding="utf-8")
    renamed = OLD_TEST.replace("bilinmeyen 00:00", "yeni başlık")
    assert run_edit(guard, tmp_path, edit("4", "b.test.js", "x", "y"), OLD_TEST, renamed) is None


def test_deleting_an_existing_test_file_warns(tmp_path: Path) -> None:
    (tmp_path / "a.test.js").write_text(OLD_TEST, encoding="utf-8")
    call = ToolCall(id="1", name="delete_file", arguments='{"path": "a.test.js"}')
    verdict = run_edit(TestGuard(tmp_path), tmp_path, call, OLD_TEST, None)
    assert verdict is not None and verdict.total == 2


async def run_agent(tmp_path: Path, anatomy_name: str) -> list[Event]:
    (tmp_path / "a.test.js").write_text(OLD_TEST, encoding="utf-8")
    anatomy = get_preset(anatomy_name)
    tools = anatomy.tool_registry(tmp_path)
    old = "    expect(geriSayimMetni(undefined)).toBe('00:00');\n"
    replies = [
        AssistantMessage(tool_calls=(edit("c1", "a.test.js", old, ""),)),
        AssistantMessage(text="bitti"),
    ]
    agent = Agent(
        ScriptedLLM(replies),
        tools,
        "talimat",
        permissions=PermissionManager(tmp_path, tools),
        anatomy=anatomy,
        root=tmp_path,
    )
    agent.permissions.grant("edit_file")
    return [event async for event in agent.run("düzelt")]


async def test_agent_appends_note_and_records_intervention(tmp_path: Path) -> None:
    events = await run_agent(tmp_path, "files+test_guard")

    notes = [e for e in events if isinstance(e, HarnessIntervened)]
    assert [(n.layer, n.action, n.call_id) for n in notes] == [("test_guard", "warn", "c1")]
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert not finished.result.is_error  # engellemez: düzenleme yapıldı
    assert "[harness] Bu düzenleme `a.test.js`" in finished.result.content
    assert "geriSayimMetni(undefined)" not in (tmp_path / "a.test.js").read_text(encoding="utf-8")


async def test_layer_off_leaves_the_result_untouched(tmp_path: Path) -> None:
    events = await run_agent(tmp_path, "files")

    assert not [e for e in events if isinstance(e, HarnessIntervened)]
    finished = next(e for e in events if isinstance(e, ToolFinished))
    assert "[harness]" not in finished.result.content


def test_layers_compose_by_name() -> None:
    anatomy = get_preset("bash+test_guard+narrow_scope")
    assert (
        anatomy.test_guard
        and anatomy.narrow_scope
        and anatomy.name == "bash+test_guard+narrow_scope"
    )
    assert not get_preset("bash").test_guard and not get_preset("bash").narrow_scope


def test_narrow_scope_appends_its_sentences_to_the_instructions() -> None:
    plain = load_system_prompt(get_preset("bash"))
    narrow = load_system_prompt(get_preset("bash+narrow_scope"))

    assert narrow.startswith(plain)
    assert "en dar yerde" in narrow and "en dar yerde" not in plain
    assert "<!--" not in narrow and "2. turda" not in narrow  # açıklama yorumu modele gitmez
