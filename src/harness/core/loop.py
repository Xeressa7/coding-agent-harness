from harness.llm.base import LLMClient
from harness.messages import Message, ToolResult


async def run_loop(
    client: LLMClient,
    history: list[Message],
    max_steps: int = 10,
) -> list[Message]:
    for _ in range(max_steps):
        response = await client.complete(history)
        history.append(response)

        if not response.tool_calls:
            break

        for call in response.tool_calls:
            result = ToolResult(
                tool_call_id=call.id,
                content=f"(henüz uygulanmadı: {call.name})",
            )
            history.append(result)

    return history