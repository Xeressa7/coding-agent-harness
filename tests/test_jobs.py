"""Arka plan işleri: başlar, çıktısı parça parça okunur, ağacıyla birlikte durur.

`&` ile süreç bırakan komut artık zaman aşımına kadar asılı kalmaz.
"""

import json
from pathlib import Path
from time import perf_counter

import anyio
import pytest

from harness.core.anatomy import get_preset
from harness.environments.jobs import MAX_JOBS
from harness.environments.local import LocalEnvironment, find_bash
from harness.messages import ToolCall
from harness.tools.base import ToolRegistry

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(find_bash() is None, reason="bash yok"),
]

# Sunucu taklidi: hazır olduğunu yazar, sonra her 0,2 sn'de bir satır basar.
SERVER = "echo hazır; i=0; while true; do i=$((i+1)); echo satır $i; sleep 0.2; done"


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="c", name=name, arguments=json.dumps(arguments))


@pytest.fixture
def registry(tmp_path: Path) -> ToolRegistry:
    (tmp_path / "web").mkdir()
    return get_preset("bash").tool_registry(tmp_path)


async def test_background_job_streams_only_new_output_and_stops(registry: ToolRegistry) -> None:
    started = await registry.call(call("bash", command=SERVER, background=True))
    assert not started.is_error
    assert started.content.startswith("Arka plan işi j1 başladı, çalışıyor.")
    assert "hazır" in started.content and "job_output (j1)" in started.content

    first = await registry.call(call("job_output", job_id="j1", wait_seconds=2))
    second = await registry.call(call("job_output", job_id="j1", wait_seconds=2))
    assert first.content.startswith("j1 · çalışıyor") and "satır" in first.content
    assert "hazır" not in first.content  # okunmuş çıktı tekrar gelmez
    last_first = [line for line in first.content.splitlines() if line.startswith("satır")][-1]
    assert last_first not in second.content.splitlines()

    listing = await registry.call(call("job_output"))
    assert listing.content.startswith("j1 · çalışıyor · echo hazır")

    stopped = await registry.call(call("job_stop", job_id="j1"))
    assert stopped.content.startswith("j1 durduruldu.")
    after = await registry.call(call("job_output", job_id="j1"))
    assert "bitti" in after.content.splitlines()[0]


async def test_short_background_command_reports_its_exit(registry: ToolRegistry) -> None:
    result = await registry.call(call("bash", command="echo bitti; exit 4", background=True))

    assert "3 sn içinde bitti, çıkış kodu 4" in result.content and "bitti" in result.content


async def test_background_job_starts_in_the_persistent_folder(registry: ToolRegistry) -> None:
    await registry.call(call("bash", command="cd web"))

    result = await registry.call(call("bash", command="pwd", background=True))

    assert result.content.rstrip().endswith("/web")


async def test_job_limit_and_unknown_ids(registry: ToolRegistry) -> None:
    for _ in range(MAX_JOBS):
        await registry.call(call("bash", command="sleep 30", background=True))

    refused = await registry.call(call("bash", command="sleep 30", background=True))
    unknown = await registry.call(call("job_output", job_id="j99"))

    assert refused.is_error and f"En fazla {MAX_JOBS}" in refused.content
    assert unknown.is_error and "böyle bir iş yok" in unknown.content
    for number in range(1, MAX_JOBS + 1):
        await registry.call(call("job_stop", job_id=f"j{number}"))


async def test_ampersand_child_holding_the_pipe_no_longer_hangs(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)
    started = perf_counter()

    result = await env.run("sleep 30 & echo başladı", timeout_seconds=20)

    assert perf_counter() - started < 10  # eskiden 20 sn'lik zaman aşımına kadar beklerdi
    assert result.left_behind and not result.timed_out
    assert result.exit_code == 0 and "başladı" in result.stdout


async def test_redirected_ampersand_job_keeps_running(tmp_path: Path) -> None:
    env = LocalEnvironment(tmp_path)

    result = await env.run("(sleep 1; echo sonra > iz.txt) > /dev/null 2>&1 &", timeout_seconds=20)

    assert not result.left_behind  # boruyu bırakan iş öldürülmez, harness yaşadıkça yaşar
    for _ in range(50):
        if (tmp_path / "iz.txt").exists():
            break
        await anyio.sleep(0.1)
    assert (tmp_path / "iz.txt").read_text(encoding="utf-8").strip() == "sonra"
