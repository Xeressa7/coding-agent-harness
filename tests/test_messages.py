"""messages.py'nin verdiği sözler.

Oturum kaydı ve ileride masaüstü ön yüzü, mesajların JSON'a çevrilip kayıpsız
geri okunabilmesine dayanır. Bu testler o garantiyi ve mesajların
değiştirilemezliğini denetler.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from harness.messages import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolResult,
    UserMessage,
)

history = TypeAdapter(list[Message])


def sample_conversation() -> list[Message]:
    """Model bir dosya okuyup soruyu cevaplıyor: dört rolün hepsi var."""
    return [
        SystemMessage(text="Sen bir kodlama ajanısın."),
        UserMessage(text="app.py'de ne var?"),
        AssistantMessage(
            tool_calls=(ToolCall(id="call_1", name="read_file", arguments='{"path": "app.py"}'),)
        ),
        ToolResult(tool_call_id="call_1", content="1  print('merhaba')"),
        AssistantMessage(text="app.py ekrana 'merhaba' yazıyor."),
    ]


def test_history_survives_json_round_trip() -> None:
    conversation = sample_conversation()

    restored = history.validate_json(history.dump_json(conversation))

    assert restored == conversation


def test_each_message_is_read_back_as_its_own_type() -> None:
    restored = history.validate_json(history.dump_json(sample_conversation()))

    assert [type(message) for message in restored] == [
        SystemMessage,
        UserMessage,
        AssistantMessage,
        ToolResult,
        AssistantMessage,
    ]


def test_tool_result_without_tool_call_id_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolResult.model_validate({"content": "sonuç"})


def test_written_message_cannot_be_changed() -> None:
    message = UserMessage(text="ilk hali")

    with pytest.raises(ValidationError):
        message.text = "değiştirilmiş hali"


def test_malformed_tool_arguments_are_kept_as_written() -> None:
    broken_json = '{"path": "app.py"'  # kapanış parantezi eksik

    call = ToolCall(id="call_1", name="read_file", arguments=broken_json)

    assert call.arguments == broken_json
