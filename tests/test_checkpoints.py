"""Kontrol noktası ve `/undo`: tur öncesi hâle dönülür, kullanıcının git deposu değişmez."""

import json
import subprocess
from pathlib import Path

import pytest

from harness.cli.commands import ChatSession
from harness.core import checkpoints as checkpoints_module
from harness.core.anatomy import get_preset
from harness.core.checkpoints import Checkpoints, CheckpointUnavailable
from harness.core.events import HarnessIntervened, TaskStarted
from harness.llm.base import ModelInfo
from harness.llm.providers import Provider, get_provider
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage, ToolCall

pytestmark = pytest.mark.anyio


def write(call_id: str, path: str, content: str) -> ToolCall:
    arguments = json.dumps({"path": path, "content": content})
    return ToolCall(id=call_id, name="write_file", arguments=arguments)


def test_undo_restores_changed_deleted_and_created_files(tmp_path: Path) -> None:
    root = tmp_path / "proje"
    root.mkdir()
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (root / "a.txt").write_bytes(b"bir\r\n")  # satır sonu baytı baytına korunur
    (root / "sil.txt").write_text("x", encoding="utf-8")
    points = Checkpoints(root)
    points.save("düzelt")

    (root / "a.txt").write_text("iki\n", encoding="utf-8")
    (root / "sil.txt").unlink()
    (root / "yeni" / "alt").mkdir(parents=True)
    (root / "yeni" / "alt" / "çalışma.py").write_text("y", encoding="utf-8")  # Türkçe ad
    (root / "node_modules").mkdir()
    (root / "node_modules" / "p.js").write_text("z", encoding="utf-8")

    restored = points.undo()

    assert restored is not None and restored.label == "düzelt"
    assert set(restored.rewritten) == {"a.txt", "sil.txt"}
    assert restored.removed == ("yeni/alt/çalışma.py",)
    assert restored.summary() == "2 dosya eski hâline döndü, 1 yeni dosya silindi"
    assert (root / "a.txt").read_bytes() == b"bir\r\n"
    assert (root / "sil.txt").read_text(encoding="utf-8") == "x"
    assert not (root / "yeni").exists()  # boş kalan klasörler de gider
    assert (root / "node_modules" / "p.js").exists()  # yok sayılan dosyaya dokunulmaz
    assert len(points) == 0 and points.undo() is None


def test_turns_without_file_changes_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    points = Checkpoints(tmp_path)
    points.save("ilk")
    (tmp_path / "a.txt").write_text("2", encoding="utf-8")
    points.save("yalnız soru")  # bu tur dosyaya dokunmadı

    restored = points.undo()

    assert restored is not None and restored.label == "ilk"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "1"


def test_the_users_own_git_repository_is_untouched(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        done = subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)
        return done.stdout.decode()

    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "ilk")
    (tmp_path / "a.txt").write_text("1", encoding="utf-8")
    git("add", "a.txt")
    head, status = git("rev-parse", "HEAD"), git("status", "--porcelain")
    points = Checkpoints(tmp_path)
    points.save("tur")
    (tmp_path / "a.txt").write_text("2", encoding="utf-8")
    points.undo()

    assert (git("rev-parse", "HEAD"), git("status", "--porcelain")) == (head, status)
    assert not git("stash", "list")


def test_missing_git_disables_checkpoints_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_git(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(checkpoints_module.subprocess, "run", no_git)
    points = Checkpoints(tmp_path)
    with pytest.raises(CheckpointUnavailable, match="git bulunamadı"):
        points.save("tur")
    assert points.broken is not None and "git" in points.broken


def session_for(
    tmp_path: Path, replies: list[AssistantMessage], *, checkpoints: bool = True
) -> ChatSession:
    async def no_models(provider: Provider) -> list[ModelInfo]:
        return []

    script = ScriptedLLM(replies)
    session = ChatSession(
        root=tmp_path / "proje",
        anatomy=get_preset("files"),
        provider=get_provider("chatgpt"),
        model="luna",
        effort=None,
        make_llm=lambda provider, model, effort: script,
        load_models=no_models,
        load_prompt=lambda anatomy: "talimat",
        sessions_dir=tmp_path / "kayitlar",  # proje dışında: kayıt geri alınmaz
        checkpoints=checkpoints,
    )
    session.allow(["all"])
    return session


async def test_chat_undo_reverts_the_turn_and_tells_the_model(tmp_path: Path) -> None:
    (tmp_path / "proje").mkdir()
    session = session_for(
        tmp_path,
        [
            AssistantMessage(tool_calls=(write("c1", "yeni.py", "print(1)\n"),)),
            AssistantMessage(text="yazdım"),
            AssistantMessage(text="tamam"),
        ],
    )
    events = [event async for event in session.agent.run("bir dosya yaz")]
    saved = [e for e in events if isinstance(e, HarnessIntervened)]
    assert [(e.layer, e.action) for e in saved] == [("checkpoint", "save")]
    assert (tmp_path / "proje" / "yeni.py").exists()

    reply = await session.run("/undo")

    assert not (tmp_path / "proje" / "yeni.py").exists()
    assert '"bir dosya yaz" turundan önceki hâle dönüldü: 1 yeni dosya silindi' in reply.text
    assert reply.view == "chat"
    session._rebuild()  # model değişse de not kaybolmaz  # pyright: ignore[reportPrivateUsage]
    second = [event async for event in session.agent.run("şimdi ne var?")]
    started = next(e for e in second if isinstance(e, TaskStarted))
    assert started.task.startswith('[harness] Kullanıcı /undo ile "bir dosya yaz"')
    assert started.task.endswith("şimdi ne var?")
    assert "geri alınacak değişiklik yok" in (await session.run("/undo")).text.lower()


async def test_single_shot_sessions_take_no_snapshots(tmp_path: Path) -> None:
    (tmp_path / "proje").mkdir()
    session = session_for(tmp_path, [AssistantMessage(text="tamam")], checkpoints=False)
    events = [event async for event in session.agent.run("soru")]

    assert not [e for e in events if isinstance(e, HarnessIntervened)]
    assert "yalnız sohbette" in (await session.run("/undo")).text
