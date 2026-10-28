"""Web ön yüzü: tek sayfa + WebSocket. CLI/TUI'nin yaptığını tarayıcıda yapar.

Çekirdek hiçbir şey bilmez: Agent.run() aynı olayları (events.py) üretir, biz
onları JSON'a çevirip soket üzerinden akıtırız. "Sor" kararında aynı asend
protokolü kullanılır — PermissionRequested gönderilir, tarayıcıdan gelen
cevap (allow/deny/always) agent'a asend ile geri verilir. Her WebSocket
bağlantısı kendi Agent'ını (ve izin hafızasını) alır; LLM istemcisi paylaşılır.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import TypeAdapter

from harness.core.agent import Agent
from harness.core.anatomy import Anatomy
from harness.core.events import Event, PermissionRequested
from harness.core.permissions import PermissionManager
from harness.llm.base import LLMClient

_STATIC_DIR = Path(__file__).parent / "web_static"
_EVENT = TypeAdapter[Event](Event)


def create_app(
    anatomy: Anatomy,
    root: Path,
    system_prompt: str,
    llm_factory: Callable[[], LLMClient],
    max_steps: int = 20,
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
        )
        try:
            while True:
                message: dict[str, Any] = await websocket.receive_json()
                task = message.get("task")
                if not isinstance(task, str) or not task.strip():
                    continue
                await _run_turn(websocket, agent, task)
        except WebSocketDisconnect:
            pass

    return app


async def _run_turn(websocket: WebSocket, agent: Agent, task: str) -> None:
    stream = agent.run(task)
    send: bool | None = None
    try:
        while True:
            event = await stream.asend(send)
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
