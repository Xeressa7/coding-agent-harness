"""Native masaüstü penceresi: PySide6/qasync kurulu değilken de import edilebilir
(`cli/native.py` üst düzeyde Qt'ye dokunmaz); günlük satırlarının HTML'i Qt'den
bağımsız, elle doğrulanabilir."""

import sys
import types

import pytest

from harness.cli.native import Block, available, block_html, render_blocks


def test_available_is_false_without_pyside6_or_qasync(monkeypatch: pytest.MonkeyPatch) -> None:
    # sys.modules[ad] = None: Python'un "bu modül yok" için resmi testi.
    monkeypatch.setitem(sys.modules, "PySide6", None)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", None)
    monkeypatch.setitem(sys.modules, "qasync", None)
    assert available() is False


def test_available_is_true_when_both_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pyside = types.ModuleType("PySide6")
    fake_qtwidgets = types.ModuleType("PySide6.QtWidgets")
    monkeypatch.setitem(sys.modules, "PySide6", fake_pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", fake_qtwidgets)
    monkeypatch.setitem(sys.modules, "qasync", types.ModuleType("qasync"))
    assert available() is True


def test_available_is_false_when_only_one_is_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pyside = types.ModuleType("PySide6")
    fake_qtwidgets = types.ModuleType("PySide6.QtWidgets")
    monkeypatch.setitem(sys.modules, "PySide6", fake_pyside)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", fake_qtwidgets)
    monkeypatch.setitem(sys.modules, "qasync", None)
    assert available() is False


def test_user_and_agent_blocks_show_the_role_and_escape_html() -> None:
    html = block_html(Block("user", "<script>alert(1)</script>"))
    assert "Sen" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html

    assert "Ajan" in block_html(Block("agent", "merhaba"))
    assert "Ajan soruyor" in block_html(Block("question", "hangi dosya?"))


def test_note_error_is_visually_distinct_from_a_plain_note() -> None:
    plain = block_html(Block("note", "✓ read_file: 1 satır"))
    error = block_html(Block("note-error", "! read_file: dosya yok"))
    assert plain != error
    assert "#e2585a" in error  # danger rengi
    assert "#e2585a" not in plain


def test_thought_block_is_italic_and_bordered() -> None:
    html = block_html(Block("thought", "düşünüyorum…"))
    assert "italic" in html
    assert "border-left" in html


def test_newlines_become_line_breaks() -> None:
    html = block_html(Block("note", "satır 1\nsatır 2"))
    assert "<br>" in html


def test_render_blocks_concatenates_in_order() -> None:
    blocks = [Block("turn", "TUR 1"), Block("user", "merhaba"), Block("agent", "selam")]
    html = render_blocks(blocks)
    assert html.index("TUR 1") < html.index("merhaba") < html.index("selam")
