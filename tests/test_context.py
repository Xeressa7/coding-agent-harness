from harness.core.context import truncated_history
from harness.messages import ToolResult, UserMessage


def test_short_tool_result_is_untouched():
    history = [UserMessage(text="merhaba"), ToolResult(tool_call_id="1", content="kısa")]
    result = truncated_history(history)
    assert result == history


def test_long_tool_result_is_truncated():
    long_content = "A" * 5000
    history = [ToolResult(tool_call_id="1", content=long_content)]

    result = truncated_history(history, head_chars=10, tail_chars=10)

    assert isinstance(result[0], ToolResult)
    assert result[0].content != long_content
    assert "kısaltıldı" in result[0].content
