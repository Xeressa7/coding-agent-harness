"""Sahte model (rapor Bölüm 18).

Önceden yazılmış yanıtları sırayla döndürür. Testler API maliyeti olmadan,
internetsiz ve her koşumda aynı sonuçla çalışır.
"""

from collections.abc import Sequence

from harness.messages import AssistantMessage, Message


class ScriptExhaustedError(Exception):
    """Sahte modele, senaryosunda yazılı olandan fazla soru soruldu."""


class ScriptedLLM:
    """Önceden yazılmış cevapları sırayla döndüren sahte model.

    Kendisine gönderilen her mesaj geçmişini `received` listesinde saklar;
    testler "model neyi gördü?" sorusunu buradan denetler.
    """

    def __init__(self, replies: Sequence[AssistantMessage]) -> None:
        self._replies = list(replies)
        self.received: list[tuple[Message, ...]] = []

    async def complete(self, history: Sequence[Message]) -> AssistantMessage:
        # Geçmişin o anki kopyası saklanır: liste sonradan büyüse de kayıt değişmez.
        self.received.append(tuple(history))
        if not self._replies:
            raise ScriptExhaustedError(f"Senaryoda cevap kalmadı; bu {len(self.received)}. çağrı.")
        return self._replies.pop(0)
