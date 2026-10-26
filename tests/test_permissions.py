"""PermissionManager: izin ver / sor / reddet kararı, araç içeriğine bakmadan."""

from pathlib import Path

from harness.core.permissions import Decision, PermissionManager
from harness.environments.local import LocalEnvironment
from harness.messages import ToolCall
from harness.tools.base import ToolRegistry
from harness.tools.bash import Bash
from harness.tools.edit_file import EditFile
from harness.tools.read_file import ReadFile
from harness.tools.search import Search
from harness.tools.write_file import WriteFile


def make_manager(root: Path) -> PermissionManager:
    tools = ToolRegistry(
        [
            ReadFile(root=root),
            WriteFile(root=root),
            EditFile(root=root),
            Search(root=root),
            Bash(LocalEnvironment(root)),
        ]
    )
    return PermissionManager(root, tools)


def test_read_only_tools_are_always_allowed(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    call = ToolCall(id="1", name="read_file", arguments='{"path": "a.txt"}')
    assert manager.decide(call) is Decision.ALLOW


def test_mutating_tool_inside_root_asks(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    call = ToolCall(id="1", name="write_file", arguments='{"path": "a.txt", "content": "x"}')
    assert manager.decide(call) is Decision.ASK


def test_mutating_tool_escaping_root_is_denied(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    call = ToolCall(
        id="1", name="write_file", arguments='{"path": "../dışarı.txt", "content": "x"}'
    )
    assert manager.decide(call) is Decision.DENY


def test_edit_file_follows_the_same_path_rule(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    inside = ToolCall(
        id="1",
        name="edit_file",
        arguments='{"path": "a.txt", "old_text": "x", "new_text": "y"}',
    )
    outside = ToolCall(
        id="2",
        name="edit_file",
        arguments='{"path": "../a.txt", "old_text": "x", "new_text": "y"}',
    )
    assert manager.decide(inside) is Decision.ASK
    assert manager.decide(outside) is Decision.DENY


def test_bash_has_no_path_so_it_always_asks(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    call = ToolCall(id="1", name="bash", arguments='{"command": "ls"}')
    assert manager.decide(call) is Decision.ASK


def test_unknown_tool_is_allowed_through_to_its_own_error(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    call = ToolCall(id="1", name="yok_boyle_bir_arac", arguments="{}")
    assert manager.decide(call) is Decision.ALLOW


def test_malformed_arguments_fall_back_to_asking(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    call = ToolCall(id="1", name="write_file", arguments="bozuk json")
    assert manager.decide(call) is Decision.ASK


def test_granted_tool_is_allowed_without_asking_again(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    call = ToolCall(id="1", name="write_file", arguments='{"path": "a.txt", "content": "x"}')
    assert manager.decide(call) is Decision.ASK

    manager.grant("write_file")

    assert manager.decide(call) is Decision.ALLOW


def test_grant_does_not_cover_a_different_tool(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    manager.grant("write_file")

    call = ToolCall(id="1", name="edit_file", arguments='{"path": "a.txt", "content": "x"}')
    assert manager.decide(call) is Decision.ASK


def test_grant_does_not_override_a_path_escaping_root(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    manager.grant("write_file")

    call = ToolCall(
        id="1", name="write_file", arguments='{"path": "../dışarı.txt", "content": "x"}'
    )
    assert manager.decide(call) is Decision.DENY
