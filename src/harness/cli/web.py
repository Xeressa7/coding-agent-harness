"""Web ön yüzü: tek sayfa + WebSocket. CLI/TUI'nin yaptığını tarayıcıda yapar.

Çekirdek hiçbir şey bilmez: Agent.run() aynı olayları (events.py) üretir, biz
onları JSON'a çevirip soket üzerinden akıtırız. "Sor" kararında aynı asend
protokolü kullanılır — PermissionRequested gönderilir, tarayıcıdan gelen
cevap (allow/deny/always) agent'a asend ile geri verilir. Her WebSocket
bağlantısı kendi Agent'ını (ve izin hafızasını) alır; LLM istemcisi paylaşılır.

`log_dir` verilirse her bağlantı kendi oturum kaydını yazar (TUI ve sade akışla
aynı biçim): canlı panel, harita, `harness inspect` ve `-c` tarayıcı sohbetlerini de görür.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import TypeAdapter

from harness.cli.commands import new_session_log
from harness.core.agent import Agent
from harness.core.anatomy import Anatomy
from harness.core.events import Event, PermissionRequested
from harness.core.permissions import PermissionManager
from harness.core.session_log import SessionLogger
from harness.llm.base import LLMClient

_STATIC_DIR = Path(__file__).parent / "web_static"
_EVENT = TypeAdapter[Event](Event)


def create_app(
    anatomy: Anatomy,
    root: Path,
    system_prompt: str,
    llm_factory: Callable[[], LLMClient],
    max_steps: int = 20,
    *,
    log_dir: Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> FastAPI:
    """`llm_factory` her WebSocket bağlantısında bir kez çağrılır (ScriptedLLM'in
    tükenebilir olması gibi durumlar için taze bir istemci üretir)."""
    app = FastAPI()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        tools = anatomy.tool_registry(root)
        permissions = PermissionManager(root, tools)
        agent = Agent(
            llm_factory(),
            tools,
            system_prompt,
            permissions=permissions,
            anatomy=anatomy,
            max_steps=max_steps,
            root=root,  # seviye 0 kod bloklarını buraya yazar; kayda da girer
            provider=provider,
            model_label=model,
            effort=effort,
        )
        log = new_session_log(log_dir) if log_dir is not None else None
        try:
            while True:
                message: dict[str, Any] = await websocket.receive_json()
                task = message.get("task")
                if not isinstance(task, str) or not task.strip():
                    continue
                await _run_turn(websocket, agent, task, log)
        except WebSocketDisconnect:
            pass

    return app


async def _run_turn(
    websocket: WebSocket, agent: Agent, task: str, log: SessionLogger | None
) -> None:
    stream = agent.run(task)
    send: bool | None = None
    try:
        while True:
            event = await stream.asend(send)
            if log is not None:
                log.write(event)
            await websocket.send_text(_EVENT.dump_json(event).decode())
            send = None
            if isinstance(event, PermissionRequested):
                answer: dict[str, Any] = await websocket.receive_json()
                choice = answer.get("choice")
                if choice == "always":
                    agent.permissions.grant(event.call.name)
                send = choice in ("allow", "always")
    except StopAsyncIteration:
        pass
