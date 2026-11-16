"""Dosyalar görünümünün JS hesabı (watch_files.js → build): dosya × adım tablosu ve özet cümle."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

CLI = Path(__file__).resolve().parent.parent / "src" / "harness" / "cli"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")


def build(events: list[dict[str, Any]]) -> dict[str, Any]:
    script = (
        "globalThis.window = globalThis;"
        f"require({json.dumps(str(CLI / 'watch_ledger.js'))});"
        f"require({json.dumps(str(CLI / 'watch_files.js'))});"
        "let data = '';"
        "process.stdin.on('data', (c) => data += c);"
        "process.stdin.on('end', () => {"
        "  const model = window.HarnessLedger.compile(JSON.parse(data));"
        "  process.stdout.write(JSON.stringify(window.HarnessFiles.build(model)));"
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


ROOT = "C:\\proje"
second = 0.0


def ev(kind: str, **fields: object) -> dict[str, object]:
    global second
    second += 0.5
    return {"kind": kind, "timestamp": f"2026-11-16T10:00:{second:06.3f}+00:00", **fields}


def tool(call_id: str, name: str, args: dict[str, object], **extra: Any) -> dict[str, object]:
    call = {"id": call_id, "name": name, "arguments": json.dumps(args)}
    error = extra.pop("error", False)
    result = {"tool_call_id": call_id, "content": extra.pop("content", "ok"), "is_error": error}
    return ev("tool_finished", call=call, result=result, **extra)


def step(n: int, text: str = "") -> list[dict[str, object]]:
    return [
        ev("context_assembled", step=n, messages=[], tools=[]),
        ev("model_started", step=n),
        ev("model_replied", step=n, text=text, tool_calls=[]),
    ]


def test_table_has_one_row_per_touched_file_and_a_plain_sentence() -> None:
    events = [
        ev("task_started", task="düzelt", root=ROOT, anatomy={"name": "bash"}),
        *step(1, "önce testleri koşayım"),
        tool("b1", "bash", {"command": "pytest -q"}, summary="$ pytest -q"),
        tool("s1", "search", {"pattern": "uygula"}, details={"paths": ["kasa\\indirim.py"]}),
        *step(2, "kodu okuyorum"),
        tool(
            "r1",
            "read_file",
            {"path": "C:\\proje\\kasa\\indirim.py"},
            details={"path": "C:\\proje\\kasa\\indirim.py", "start": 1, "end": 6, "total_lines": 6},
        ),
        tool(
            "r2",
            "read_file",
            {"path": "./kasa/sepet.py"},
            details={"path": "./kasa/sepet.py", "start": 1, "end": 10, "total_lines": 40},
        ),
        tool("x1", "read_file", {"path": "yok.py"}, error=True),
        *step(3, "düzeltiyorum"),
        tool(
            "e1",
            "edit_file",
            {"path": "kasa/indirim.py", "old_text": "a", "new_text": "b"},
            details={"path": "kasa/indirim.py", "line": 6},
        ),
        ev("task_finished", reason="done", steps=3),
    ]
    out = build(events)

    assert [c["label"] for c in out["columns"]] == ["1", "2", "3"]
    assert out["columns"][1]["reason"] == "kodu okuyorum"  # hücreye tıklayınca "neden"
    rows = {r["path"]: r for r in out["rows"]}
    assert list(rows) == ["kasa/indirim.py", "kasa/sepet.py", "yok.py"]  # ilk dokunuş sırası
    indirim = rows["kasa/indirim.py"]
    assert [m["kind"] for m in indirim["cells"]["1:1"]] == ["hit"]  # aramada çıktı
    assert indirim["counts"] == {"hit": 1, "read": 1, "edit": 1}
    assert indirim["total"] == 6
    assert rows["kasa/sepet.py"]["total"] == 40
    assert rows["yok.py"]["cells"]["1:2"][0]["rec"]["failed"]
    assert list(out["lanes"]["bash"]) == ["1:1"] and list(out["lanes"]["search"]) == ["1:1"]
    assert out["changed"] == ["kasa/indirim.py"]
    sentence = out["sentence"]
    assert sentence.startswith("Ajan 3 adımda 3 dosyaya dokundu")
    assert "1 dosyayı değiştirdi (indirim.py)" in sentence and "1 komut çalıştırdı" in sentence


def test_turns_get_their_own_step_labels() -> None:
    events = [
        ev("task_started", task="bir", root=ROOT),
        *step(1),
        ev("task_finished", reason="done", steps=1),
        ev("task_started", task="iki", root=ROOT),
        *step(1),
        ev("task_finished", reason="done", steps=1),
    ]
    out = build(events)
    assert [c["label"] for c in out["columns"]] == ["1·1", "2·1"]
    assert out["sentence"] == "Ajan 2 adımda hiçbir dosyaya dokunmadı."
