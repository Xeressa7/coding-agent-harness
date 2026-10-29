"""Görev seti runner'ı: sahte modelle geçme/geçmeme/eksik-test senaryoları."""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from harness.cli import app as cli
from harness.core.anatomy import get_preset
from harness.core.evaluation import (
    TaskResult,
    Variant,
    read_results,
    run_all,
    run_matrix,
    run_task,
    summarize,
    wilson_interval,
)
from harness.core.session_log import read_session
from harness.llm.base import LLMClient
from harness.llm.providers import Provider
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, TokenUsage, ToolCall

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


# ---------------------------------------------------------------- deney matrisi


def solver() -> ScriptedLLM:
    usage = TokenUsage(input_tokens=1000, output_tokens=50, cached_tokens=600)
    return ScriptedLLM([fix_call(), AssistantMessage(text="tamam", usage=usage)])


def giver_up() -> ScriptedLLM:
    return ScriptedLLM([AssistantMessage(text="yapamadım")])


async def test_matrix_runs_first_repeat_of_everything_first_and_logs_each_run(
    tmp_path: Path,
) -> None:
    tasks = [make_task(tmp_path / "set", "001-a"), make_task(tmp_path / "set", "002-b")]
    variants = [
        Variant("iyi · files", get_preset("files"), solver, "talimat", model="iyi"),
        Variant("pes · files", get_preset("files"), giver_up, "talimat"),
    ]
    seen: list[TaskResult] = []
    out = tmp_path / "deney"

    results = await run_matrix(tasks, variants, out, repeats=2, on_result=seen.append)

    order = [(r.repeat, r.task_id, r.variant) for r in results]
    assert order[:4] == [
        (1, "001-a", "iyi · files"),
        (1, "001-a", "pes · files"),
        (1, "002-b", "iyi · files"),
        (1, "002-b", "pes · files"),
    ]
    assert len(results) == 8 and seen == results
    assert [r.passed for r in results[:2]] == [True, False]
    first = results[0]
    assert (first.tool_calls, first.tool_errors, first.cached_tokens) == (1, 0, 600)
    assert first.log is not None
    events = read_session(Path(first.log))
    assert events[0].kind == "task_started" and events[0].model == "iyi"
    assert events[-1].kind == "task_finished"
    # Sonuç dosyası satır satır yazılır: yarıda kalan deney de okunur.
    assert read_results(out) == results


async def test_a_crashing_model_is_still_graded(tmp_path: Path) -> None:
    task_dir = make_task(tmp_path)
    result = await run_task(task_dir, ScriptedLLM([]), get_preset("files"), "talimat")
    assert result.reason == "error" and not result.passed and not result.completed


def test_summary_counts_pass_rate_interval_and_cache_share() -> None:
    def run(passed: bool, cached: int | None) -> TaskResult:
        return TaskResult(
            task_id="t",
            passed=passed,
            reason="done" if passed else "step_limit",
            steps=4,
            duration_seconds=10.0,
            input_tokens=1000,
            output_tokens=100,
            detail="",
            cached_tokens=cached,
            variant="v",
        )

    (summary,) = summarize([run(True, 500), run(True, 300), run(False, None)])
    assert (summary.runs, summary.passed, summary.completed) == (3, 2, 2)
    assert summary.cache_ratio == 0.4  # yalnız sayım bildiren iki koşudan: 800 / 2000
    assert summary.mean_input_tokens == 1000
    low, high = wilson_interval(2, 3)
    assert (summary.pass_low, summary.pass_high) == (low, high)
    assert 0.2 < low < 0.25 and 0.9 < high < 0.95
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_eval_command_runs_the_matrix_and_report_rereads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_task(tmp_path / "set", "001-a")
    monkeypatch.setenv("OPENCODE_API_KEY", "k")

    def factory(provider: Provider, model: str, effort: str | None, http: object) -> LLMClient:
        return solver() if model == "iyi" else giver_up()

    monkeypatch.setattr(cli, "make_llm", factory)
    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        [
            "eval",
            str(tmp_path / "set"),
            "-m",
            "opencode-go/iyi",
            "-m",
            "opencode-go/kotu",
            "-n",
            "2",
        ],
        env={"COLUMNS": "200"},
    )
    assert result.exit_code == 0, result.output
    assert "1 görev × 2 varyant × 2 tekrar = 4 koşu" in result.output
    assert "opencode-go/iyi · files" in result.output and "2/2 (%100)" in result.output
    assert "0/2 (%0)" in result.output

    (out,) = (tmp_path / "harness-evals").iterdir()
    again = runner.invoke(cli.app, ["eval", "--report", str(out)], env={"COLUMNS": "200"})
    assert again.exit_code == 0 and "2/2 (%100)" in again.output


def test_eval_refuses_a_provider_without_a_key(tmp_path: Path) -> None:
    make_task(tmp_path / "set", "001-a")
    result = CliRunner().invoke(cli.app, ["eval", str(tmp_path / "set"), "-m", "deepseek/x"])
    assert result.exit_code == 1 and "hazır değil" in result.output
