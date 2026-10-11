"""Sahte modelin verdiği sözler.

Döngü testleri bu sahte modele dayanacak; bu yüzden önce sahte modelin
kendisinin doğru çalıştığından emin oluyoruz.
"""

import pytest

from harness.llm.base import LLMClient
from harness.llm.scripted import ScriptedLLM, ScriptExhaustedError
from harness.messages import AssistantMessage, Message, UserMessage

pytestmark = pytest.mark.anyio


async def test_replies_come_back_in_order() -> None:
    model = ScriptedLLM([AssistantMessage(text="birinci"), AssistantMessage(text="ikinci")])
    history = [UserMessage(text="merhaba")]

    first = await model.complete(history)
    second = await model.complete(history)

    assert (first.text, second.text) == ("birinci", "ikinci")


async def test_keeps_a_snapshot_of_each_history_it_receives() -> None:
    model = ScriptedLLM([AssistantMessage(text="cevap 1"), AssistantMessage(text="cevap 2")])
    history: list[Message] = [UserMessage(text="soru 1")]

    await model.complete(history)
    history.append(UserMessage(text="soru 2"))
    await model.complete(history)

    assert model.received == [
        (UserMessage(text="soru 1"),),
        (UserMessage(text="soru 1"), UserMessage(text="soru 2")),
    ]


async def test_asking_more_than_scripted_is_an_error() -> None:
    model = ScriptedLLM([])

    with pytest.raises(ScriptExhaustedError):
        await model.complete([UserMessage(text="merhaba")])


def test_scripted_llm_fits_the_llm_client_contract() -> None:
    # Asıl denetimi pyright yapar: ScriptedLLM sözleşmeye uymasaydı bu satır
    # tip hatası verirdi.
    model: LLMClient = ScriptedLLM([])

    assert model is not None
