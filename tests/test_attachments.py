"""Görsel ekleri: depo, sürükle-bırak yolları, adaptör biçimleri ve kayıttan geri yükleme."""

import base64
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from harness.cli.attachments import (
    MAX_BYTES,
    AttachmentError,
    dropped_paths,
    from_file,
    sniff,
    split_pasted_paths,
    store,
)
from harness.core.events import TaskStarted
from harness.core.session_log import history_from_events
from harness.llm import anthropic_messages, chat_completions, responses
from harness.llm.opencode import parse_catalog
from harness.messages import ImageAttachment, UserMessage

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def test_sniff_reads_the_first_bytes_not_the_name() -> None:
    assert sniff(PNG) == "image/png" and sniff(JPEG) == "image/jpeg"
    assert sniff(b"GIF89a....") == "image/gif"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff(b"%PDF-1.7") is None


def test_store_is_content_addressed_and_limited(tmp_path: Path) -> None:
    first = store(PNG, "a.png", tmp_path)
    second = store(PNG, "baska-ad.png", tmp_path)
    assert first.path == second.path and len(list(tmp_path.iterdir())) == 1
    assert first.media_type == "image/png" and first.size == len(PNG)
    assert second.name == "baska-ad.png"
    with pytest.raises(AttachmentError, match="desteklenen bir görsel değil"):
        store(b"duz metin", "not.txt", tmp_path)
    with pytest.raises(AttachmentError, match="sınır"):
        store(PNG + b"\x00" * MAX_BYTES, "dev.png", tmp_path)


def test_from_file_reports_unreadable_files(tmp_path: Path) -> None:
    with pytest.raises(AttachmentError, match="okunamadı"):
        from_file(tmp_path / "yok.png", tmp_path)


def test_dropped_paths_accept_quoted_uri_and_escaped_forms(tmp_path: Path) -> None:
    spaced = tmp_path / "ekran görüntüsü.png"
    spaced.write_bytes(PNG)
    other = tmp_path / "b.jpg"
    other.write_bytes(JPEG)
    assert dropped_paths(f"'{spaced}'") == [spaced]
    assert dropped_paths(f'"{spaced}" {other}') == [spaced, other]
    assert dropped_paths(spaced.as_uri()) == [spaced]  # file:///…/ekran%20g%C3%B6r…
    if os.name != "nt":  # kaçışlı boşluk yalnız POSIX kabuklarında
        escaped = str(spaced).replace(" ", "\\ ")
        assert dropped_paths(f"{escaped} {other}\n") == [spaced, other]


def test_windows_split_keeps_backslashes() -> None:
    text = r'"C:\Users\kullanici\ekran görüntüsü.png" C:\tmp\b.jpg'
    assert split_pasted_paths(text, windows=True) == [
        r"C:\Users\kullanici\ekran görüntüsü.png",
        r"C:\tmp\b.jpg",
    ]
    # POSIX kipi aynı metindeki ters bölüleri yutar: Windows'ta kırılan buydu.
    assert split_pasted_paths(r"C:\tmp\b.jpg", windows=False) == ["C:tmpb.jpg"]


def test_dropped_paths_leave_ordinary_text_alone(tmp_path: Path) -> None:
    text_file = tmp_path / "not.txt"
    text_file.write_text("x", encoding="utf-8")
    image = tmp_path / "a.png"
    image.write_bytes(PNG)
    assert dropped_paths("merhaba dünya") is None
    assert dropped_paths(str(text_file)) is None
    assert dropped_paths(str(tmp_path / "olmayan.png")) is None
    assert dropped_paths(f"{image} ve metin") is None  # yalnız bir kısmı görsel: metindir
    assert dropped_paths("'kapanmayan tırnak") is None
    assert dropped_paths("") is None


@pytest.fixture
def image(tmp_path: Path) -> ImageAttachment:
    return store(PNG, "ekran.png", tmp_path)


def encoded() -> str:
    return base64.b64encode(PNG).decode("ascii")


def test_responses_sends_input_image_parts(image: ImageAttachment) -> None:
    message = UserMessage(text="bu ne?", images=(image,))
    assert responses.user_content(UserMessage(text="düz")) == "düz"
    assert responses.user_content(message) == [
        {"type": "input_text", "text": "bu ne?"},
        {"type": "input_image", "image_url": f"data:image/png;base64,{encoded()}"},
    ]
    request: dict[str, Any] = responses.build_request("gpt-x", [message])
    assert request["input"][0]["content"][1]["type"] == "input_image"


def test_chat_completions_sends_image_url_parts(image: ImageAttachment) -> None:
    message = UserMessage(text="bu ne?", images=(image,))
    assert chat_completions.user_content(message) == [
        {"type": "text", "text": "bu ne?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded()}"}},
    ]
    request = chat_completions.build_request("m", [message])
    assert request["messages"][0]["content"][1]["type"] == "image_url"


def test_anthropic_puts_base64_images_before_the_text(image: ImageAttachment) -> None:
    message = UserMessage(text="bu ne?", images=(image,))
    assert anthropic_messages.user_content(message) == [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": encoded()},
        },
        {"type": "text", "text": "bu ne?"},
    ]
    request = anthropic_messages.build_request("claude-x", [message])
    assert request["messages"][0]["content"][0]["type"] == "image"


def test_missing_image_file_becomes_a_note(image: ImageAttachment) -> None:
    Path(image.path).unlink()
    message = UserMessage(text="bu ne?", images=(image,))
    for content in (
        responses.user_content(message),
        chat_completions.user_content(message),
        anthropic_messages.user_content(message),
    ):
        assert isinstance(content, list)
        notes = [part["text"] for part in content if "artık yok" in str(part.get("text", ""))]
        assert notes == ["[Görsel eklenmişti ama dosyası artık yok: ekran.png]"]


def test_catalog_modalities_tell_whether_a_model_reads_images() -> None:
    payload: dict[str, Any] = {
        "opencode-go": {
            "models": {
                "gorur": {"name": "G", "modalities": {"input": ["text", "image"]}},
                "kor": {"name": "K", "modalities": {"input": ["text"]}},
                "bilinmez": {"name": "B"},
            }
        }
    }
    catalog = parse_catalog(payload, "opencode-go")
    assert [catalog[m].images for m in ("gorur", "kor", "bilinmez")] == [True, False, None]


def test_chatgpt_model_list_reads_input_modalities() -> None:
    info = responses._model_info(  # pyright: ignore[reportPrivateUsage]
        {"slug": "gpt-x", "input_modalities": ["text", "image"]}
    )
    assert info.images is True
    assert responses._model_info({"slug": "gpt-y"}).images is None  # pyright: ignore[reportPrivateUsage]


def test_resumed_history_keeps_the_images(image: ImageAttachment) -> None:
    started = TaskStarted(task="bu ne?", images=(image,), timestamp=datetime.now(UTC))
    restored = TaskStarted.model_validate_json(started.model_dump_json())
    history = history_from_events([restored])
    assert history == [UserMessage(text="bu ne?", images=(image,))]
