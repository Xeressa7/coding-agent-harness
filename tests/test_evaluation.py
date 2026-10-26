"""Görev seti runner'ı: sahte modelle geçme/geçmeme/eksik-test senaryoları."""

import json
import shutil
from pathlib import Path

import pytest

from harness.core.anatomy import get_preset
from harness.core.evaluation import run_all, run_task
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall

pytestmark = pytest.mark.anyio

_BUGGY_CALC = "def divide(a, b):\n    return a / b\n"
_TEST_CALC = (
    "from calc import divide\n\n\n"
    "def test_zero():\n    assert divide(4, 0) == 0\n\n\n"
    "def test_normal():\n    assert divide(10, 2) == 5\n"
)


def make_task(tmp_path: Path, name: str = "001-ornek") -> Path:
    task_dir = tmp_path / name
    (task_dir / "repo").mkdir(parents=True)
    (task_dir / "tests").mkdir()
    (task_dir / "task.md").write_text("calc.py'deki divide'ı düzelt.", encoding="utf-8")
    (task_dir / "repo" / "calc.py").write_text(_BUGGY_CALC, encoding="utf-8")
    (task_dir / "tests" / "test_calc.py").write_text(_TEST_CALC, encoding="utf-8")
    return task_dir


def fix_call(call_id: str = "c1") -> AssistantMessage:
    fixed = "def divide(a, b):\n    if b == 0:\n        return 0\n    return a / b\n"
    arguments = json.dumps({"path": "calc.py", "content": fixed})
    call = ToolCall(id=call_id, name="write_file", arguments=arguments)
    return AssistantMessage(tool_calls=(call,))


async def test_passing_fix_is_graded_as_passed(tmp_path: Path) -> None:
    task_dir = make_task(tmp_path)
    model = ScriptedLLM([fix_call(), AssistantMessage(text="düzelttim")])

    result = await run_task(task_dir, model, get_preset("files"), "talimat")

    assert result.passed is True
    assert result.reason == "done"
    assert result.steps == 2
    assert "2 passed" in result.detail


async def test_leaving_the_bug_is_graded_as_failed(tmp_path: Path) -> None:
    task_dir = make_task(tmp_path)
    model = ScriptedLLM([AssistantMessage(text="bakamadım")])

    result = await run_task(task_dir, model, get_preset("files"), "talimat")

    assert result.passed is False
    assert "failed" in result.detail


async def test_missing_tests_dir_is_graded_as_failed(tmp_path: Path) -> None:
    task_dir = make_task(tmp_path)
    shutil.rmtree(task_dir / "tests")
    model = ScriptedLLM([AssistantMessage(text="tamam")])

    result = await run_task(task_dir, model, get_preset("files"), "talimat")

    assert result.passed is False
    assert "tests/ klasörü yok" in result.detail


async def test_run_all_runs_every_task_in_order(tmp_path: Path) -> None:
    make_task(tmp_path, "001-a")
    make_task(tmp_path, "002-b")
    model = ScriptedLLM(
        [
            fix_call("c1"),
            AssistantMessage(text="tamam"),
            fix_call("c2"),
            AssistantMessage(text="tamam"),
        ]
    )

    results = await run_all(tmp_path, model, get_preset("files"), "talimat")

    assert [r.task_id for r in results] == ["001-a", "002-b"]
    assert all(r.passed for r in results)


async def test_permissions_are_pre_granted_so_nothing_is_asked(tmp_path: Path) -> None:
    # Değerlendirme güvenilir bir ortamda koşar: write_file "sor" demeden çalışmalı.
    task_dir = make_task(tmp_path)
    model = ScriptedLLM([fix_call(), AssistantMessage(text="tamam")])

    result = await run_task(task_dir, model, get_preset("files"), "talimat")

    assert result.passed is True


async def test_shipped_example_task_is_itself_gradable() -> None:
    # tasks/001-divide-by-zero'nun kendisi bozulursa bu test kırmızı olur.
    task_dir = Path(__file__).resolve().parent.parent / "tasks" / "001-divide-by-zero"
    model = ScriptedLLM([fix_call(), AssistantMessage(text="düzelttim")])

    result = await run_task(task_dir, model, get_preset("files"), "talimat")

    assert result.passed is True
