"""Ortak veri tipleri: Message, ToolCall, ToolResult (rapor Bölüm 12).

Paketin en alt katmanı: hiçbir harness modülünü import etmez, herkes onu
import eder. Sağlayıcıdan bağımsızdır; OpenAI ya da Anthropic biçimine çeviri
llm/ altındaki adaptörlerin işidir. Bir mesaj dört rolden birini taşır:
system, user, assistant, tool.
"""
