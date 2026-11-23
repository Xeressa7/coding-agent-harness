"""Ürün varsayılanı, koşu parmak izi ve iki kapalı katman (timeout_hint, scope_focus).

Varsayılan `bash+tool_guidance` oldu ama `bash` hazır ayarı değişmedi: deneylerin tabanı (A0)
odur. Deney kolu A1 = `bash+tool_guidance`, 26b3096'daki tanımıyla donuk kalmalı; yeni katmanlar
ona girmez. Parmak izi, adlar değişse de iki koşunun aynı harness ayarıyla koşup koşmadığını söyler.
"""

import hashlib
import json
from pathlib import Path

import pytest

from harness.cli.app import load_system_prompt
from harness.core.agent import Agent, harness_fingerprint
from harness.core.anatomy import DEFAULT_PRESET, LAYERS, get_preset
from harness.core.events import TaskStarted
from harness.core.permissions import PermissionManager
from harness.environments.base import CommandResult
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall
from harness.tools.base import ToolRegistry
from harness.tools.bash import TIMEOUT_HINT, Bash

pytestmark = pytest.mark.anyio

# 26b3096'da (G03 ön kaydı) A1'in araç şemaları + talimat özeti; test_tool_guidance.py'deki
# A0 özetleriyle aynı yöntem. Değişirse G03 ile 5. tur arasında A1 değişmiştir.
A1_BASELINE = "2f8baa08dd59467a"


def digest(name: str, root: Path) -> str:
    anatomy = get_preset(name)
    specs = [vars(spec) for spec in anatomy.tool_registry(root).specs()]
    blob = json.dumps(specs, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256((blob + load_system_prompt(anatomy)).encode()).hexdigest()[:16]


def test_product_default_is_bash_with_tool_guidance_only() -> None:
    default = get_preset(DEFAULT_PRESET)

    assert DEFAULT_PRESET == "bash+tool_guidance"
    assert default.tool_guidance and not default.timeout_hint and not default.scope_focus
    assert not get_preset("bash").tool_guidance  # A0 aynı kaldı


def test_experiment_arm_a1_is_frozen(tmp_path: Path) -> None:
    assert digest("bash+tool_guidance", tmp_path) == A1_BASELINE


def test_new_layers_are_off_and_named() -> None:
    assert {"timeout_hint", "scope_focus"} <= set(LAYERS)
    for name in ("raw", "files", "bash", "full", DEFAULT_PRESET):
        anatomy = get_preset(name)
        assert not anatomy.timeout_hint and not anatomy.scope_focus


# --- parmak izi --------------------------------------------------------------------------------


def registry_of(name: str, root: Path) -> ToolRegistry:
    return get_preset(name).tool_registry(root)


def test_fingerprint_follows_prompt_and_tool_schemas_not_names(tmp_path: Path) -> None:
    a0 = harness_fingerprint("talimat", registry_of("bash", tmp_path))

    assert a0 == harness_fingerprint("talimat", registry_of("bash", tmp_path))
    assert a0 != harness_fingerprint("talimat", registry_of("bash+tool_guidance", tmp_path))
    assert a0 != harness_fingerprint("başka talimat", registry_of("bash", tmp_path))
    assert len(a0) == 16


async def test_task_started_records_the_fingerprint(tmp_path: Path) -> None:
    tools = ToolRegistry([])
    agent = Agent(
        ScriptedLLM([AssistantMessage(text="tamam")]),
        tools,
        system_prompt="talimat",
        permissions=PermissionManager(tmp_path, tools),
    )

    events = [event async for event in agent.run("görev")]

    started = next(e for e in events if isinstance(e, TaskStarted))
    assert started.fingerprint == harness_fingerprint("talimat", tools) == agent.fingerprint


def test_old_task_started_without_fingerprint_still_reads() -> None:
    old = TaskStarted.model_validate(
        {"kind": "task_started", "task": "x", "timestamp": "2026-11-18T10:00:00Z"}
    )

    assert old.fingerprint is None


# --- timeout_hint (pilot madde 5) ---------------------------------------------------------------


class TimingOut:
    async def run(self, command: str, timeout_seconds: float) -> CommandResult:
        return CommandResult(stdout="yarım", stderr="", exit_code=None, timed_out=True)


def bash_call() -> ToolCall:
    return ToolCall(id="c1", name="bash", arguments='{"command": "pytest", "timeout_seconds": 5}')


async def test_timeout_hint_suggests_narrower_tests_and_background_but_keeps_full_suite() -> None:
    result = await ToolRegistry([Bash(TimingOut(), timeout_hint=True)]).call(bash_call())

    assert result.is_error and "5 sn içinde bitmedi" in result.content and "yarım" in result.content
    assert result.content.endswith(TIMEOUT_HINT)
    assert "tam takımı da çalıştır" in TIMEOUT_HINT and "background: true" in TIMEOUT_HINT


async def test_timeout_without_the_layer_is_unchanged() -> None:
    result = await ToolRegistry([Bash(TimingOut())]).call(bash_call())

    assert "[harness]" not in result.content


def test_timeout_hint_layer_reaches_the_bash_tool(tmp_path: Path) -> None:
    on = get_preset("bash+timeout_hint").tool_registry(tmp_path).get("bash")
    off = get_preset("bash").tool_registry(tmp_path).get("bash")

    assert isinstance(on, Bash) and isinstance(off, Bash)
    assert on._timeout_hint is True and off._timeout_hint is False  # pyright: ignore[reportPrivateUsage]


# --- scope_focus (pilot madde 4) ----------------------------------------------------------------


def test_scope_focus_appends_its_sentences_only_when_on() -> None:
    on = load_system_prompt(get_preset("bash+scope_focus"))
    off = load_system_prompt(get_preset("bash"))

    assert on.startswith(off)
    assert "Kurulu ortamla çalış" in on and "Kurulu ortamla çalış" not in off
    assert "<!--" not in on
