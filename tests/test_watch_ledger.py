"""Akış defterinin JS hesabı (watch_ledger.js → compile): kayıt → tur/adım/satır modeli."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

LEDGER_JS = Path(__file__).resolve().parent.parent / "src" / "harness" / "cli" / "watch_ledger.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")


def compile_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    script = (
        "globalThis.window = globalThis;"
        f"require({json.dumps(str(LEDGER_JS))});"
        "let data = '';"
        "process.stdin.on('data', (c) => data += c);"
        "process.stdin.on('end', () => {"
        "  process.stdout.write(JSON.stringify(window.HarnessLedger.compile(JSON.parse(data))));"
        "});"
    )
    done = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(events),
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def ev(kind: str, second: float, **fields: object) -> dict[str, object]:
    return {"kind": kind, "timestamp": f"2026-11-16T10:00:{second:06.3f}+00:00", **fields}


def call(call_id: str, name: str, **args: object) -> dict[str, object]:
    return {"id": call_id, "name": name, "arguments": json.dumps(args)}


ANATOMY = {"name": "bash+memory", "tools": ["read_file", "bash"], "output_limit": True}


def test_new_run_separates_preparation_notes_and_what_the_model_saw() -> None:
    cut = "baş\n\n[harness: çıktı 60.000 karakterdi; ortadaki 20.000 karakter atıldı.]\n\nson"
    events = [
        ev("task_started", 0, task="nerede?", provider="demo", model="m", anatomy=ANATOMY),
        ev(
            "harness_intervened",
            0.1,
            step=0,
            layer="agents_md",
            action="inject",
            summary="AGENTS.md",
        ),
        ev(
            "context_assembled",
            0.2,
            step=1,
            messages=[{"role": "system", "text": "t" * 100}, {"role": "user", "text": "nerede?"}],
            tools=[{"name": "bash"}],
        ),
        ev("model_started", 0.3, step=1),
        ev("model_reasoning_delta", 0.8, step=1, text="bakayım"),
        ev(
            "model_replied",
            1.3,
            step=1,
            text="",
            tool_calls=[call("b1", "bash", command="cat log")],
            usage={"input_tokens": 100, "output_tokens": 10, "cached_tokens": 50},
        ),
        ev(
            "harness_intervened",
            1.35,
            step=1,
            layer="loop_guard",
            action="warn",
            summary="bash 3. kez",
            call_id="b1",
        ),
        ev(
            "tool_started",
            1.4,
            call=call("b1", "bash", command="cat log"),
            category="exec",
            summary="$ cat log",
        ),
        ev(
            "harness_intervened",
            2.0,
            step=1,
            layer="output_limit",
            action="truncate",
            summary="bash sonucu 60.000 → 40.000",
            call_id="b1",
        ),
        ev(
            "tool_finished",
            2.0,
            call=call("b1", "bash", command="cat log"),
            result={"tool_call_id": "b1", "content": cut, "is_error": False},
            duration_seconds=0.6,
            category="exec",
            summary="$ cat log",
            outcome="çıkış kodu 0",
            full_content="x" * 60000,
        ),
        ev(
            "context_assembled",
            2.1,
            step=2,
            messages=[
                {"role": "system", "text": "t" * 100},
                {"role": "user", "text": "nerede?"},
                {
                    "role": "assistant",
                    "text": "",
                    "tool_calls": [call("b1", "bash", command="cat log")],
                },
                {"role": "tool", "tool_call_id": "b1", "content": cut},
            ],
            tools=[{"name": "bash"}],
        ),
        ev("model_started", 2.2, step=2),
        ev("model_replied", 3.0, step=2, text="burada", tool_calls=[], first_text_seconds=0.5),
        ev("task_finished", 3.1, reason="done", steps=2, duration_seconds=3.1),
    ]
    out = compile_events(events)

    [turn] = out["turns"]
    assert turn["status"] == "done" and turn["finish"]["steps"] == 2
    assert [(p["layer"], p["step"]) for p in turn["prep"]] == [("agents_md", 0)]
    first, second = turn["steps"]
    model, tool = first["rows"]  # takılma uyarısı ayrı satır değil, aracın notu
    assert (
        model["kind"] == "model" and model["first"] - model["start"] == 500
    )  # ilk düşünce parçası
    assert [n["layer"] for n in tool["notes"]] == ["loop_guard", "output_limit"]
    assert tool["full"] == "x" * 60000 and tool["result"] == cut and not tool["oldCut"]
    assert tool["duration"] == 600
    assert second["context"]["cut"] == 1 and second["context"]["fresh"] == 2
    assert (
        second["context"]["prevCount"] == 2
        and second["rows"][0]["first"] - second["rows"][0]["start"] == 500
    )
    totals = out["totals"]
    assert totals["layers"] == {"agents_md": 1, "loop_guard": 1, "output_limit": 1}
    assert (totals["input"], totals["cached"], totals["calls"], totals["steps"]) == (100, 50, 1, 2)


def test_old_run_reveals_the_silent_four_thousand_character_cut() -> None:
    old_cut = "baş\n\n… [1200 karakter kısaltıldı] …\n\nson"
    old_anatomy = {"name": "files", "tools": ["read_file"]}  # output_limit alanı yok: eski kayıt
    events = [
        ev("task_started", 0, task="incele", anatomy=old_anatomy),
        ev(
            "context_assembled",
            0.1,
            step=1,
            messages=[{"role": "user", "text": "incele"}],
            tools=[],
        ),
        ev("model_replied", 1, step=1, text="", tool_calls=[call("r1", "read_file", path="a.py")]),
        ev(
            "tool_finished",
            1.1,
            call=call("r1", "read_file", path="a.py"),
            result={"tool_call_id": "r1", "content": "y" * 5200, "is_error": False},
            summary="a.py",
        ),
        ev(
            "context_assembled",
            1.2,
            step=2,
            messages=[
                {"role": "user", "text": "incele"},
                {"role": "tool", "tool_call_id": "r1", "content": old_cut},
            ],
            tools=[],
        ),
        ev("task_finished", 2, reason="step_limit", steps=2),
    ]
    out = compile_events(events)

    [turn] = out["turns"]
    tool = turn["steps"][0]["rows"][-1]
    assert tool["oldCut"] and turn["steps"][1]["context"]["oldCut"] == 1
    assert turn["status"] == "step_limit" and out["totals"]["layers"] == {}


def test_long_wait_between_chat_turns_is_compressed_on_the_timeline() -> None:
    events = [
        ev("task_started", 0, task="bir", anatomy=ANATOMY),
        ev("task_finished", 1, reason="done", steps=1),
        {
            "kind": "task_started",
            "timestamp": "2026-11-16T10:05:00+00:00",
            "task": "iki",
            "anatomy": ANATOMY,
        },
        {
            "kind": "task_finished",
            "timestamp": "2026-11-16T10:05:01+00:00",
            "reason": "done",
            "steps": 1,
        },
    ]
    out = compile_events(events)
    [gap] = out["gaps"]
    assert gap["to"] - gap["from"] == 299_000 and gap["saved"] == 299_000 - 1500
    assert [t["task"] for t in out["turns"]] == ["bir", "iki"]


def run_js(expression: str) -> object:
    script = (
        "globalThis.window = globalThis;"
        f"require({json.dumps(str(LEDGER_JS))});"
        f"process.stdout.write(JSON.stringify({expression}));"
    )
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, encoding="utf-8"
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_edit_and_write_calls_become_a_line_diff() -> None:
    diff = run_js("window.HarnessLedger.lineDiff('a\\nb\\nc', 'a\\nB\\nc\\nd')")
    assert diff == [[" ", "a"], ["-", "b"], ["+", "B"], [" ", "c"], ["+", "d"]]
    edit = {"path": "x.py", "old_text": "x = 1", "new_text": "x = 2"}
    change = run_js(
        "window.HarnessLedger.changeOf({tool: 'edit_file', details: {line: 7},"
        f" call: {{arguments: {json.dumps(json.dumps(edit))}}}}})"
    )
    assert change == {"path": "x.py", "line": 7, "rows": [["-", "x = 1"], ["+", "x = 2"]]}
    write = {"path": "y.py", "content": "a\nb"}
    whole = run_js(
        "window.HarnessLedger.changeOf({tool: 'write_file',"
        f" call: {{arguments: {json.dumps(json.dumps(write))}}}}})"
    )
    assert isinstance(whole, dict) and whole["whole"] and whole["rows"] == [["+", "a"], ["+", "b"]]


def test_expand_context_matches_python_reader(tmp_path: Path) -> None:
    """Panel kaydı ham satırlardan okur: JS açması `read_session` ile aynı bağlamı vermeli."""
    from test_session_log import growing_contexts

    from harness.core.session_log import SessionLogger, read_session

    log = SessionLogger(tmp_path / "oturum.jsonl")
    for context in growing_contexts():
        log.write(context)
    script = (
        "globalThis.window = globalThis;"
        f"require({json.dumps(str(LEDGER_JS))});"
        "let data = '';"
        "process.stdin.on('data', (c) => data += c);"
        "process.stdin.on('end', () => {"
        "  let prev = null; const out = [];"
        "  for (const line of data.split(String.fromCharCode(10)).filter(Boolean)) {"
        "    prev = window.HarnessLedger.expandContext(JSON.parse(line), prev); out.push(prev);"
        "  }"
        "  process.stdout.write(JSON.stringify(out));"
        "});"
    )
    done = subprocess.run(
        ["node", "-e", script],
        input=log.path.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=30,
        encoding="utf-8",
    )
    assert done.returncode == 0, done.stderr
    expected = [json.loads(e.model_dump_json()) for e in read_session(log.path)]
    assert json.loads(done.stdout) == expected
