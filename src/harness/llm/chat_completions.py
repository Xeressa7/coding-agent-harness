"""OpenAI uyumlu Chat Completions adaptörü.

httpx ile doğrudan HTTP konuşur; modele giden ve modelden gelen JSON gizlenmez.
Aynı biçimi konuşan her sağlayıcıyla çalışır: Ollama (yerel) ve OpenCode
Go'daki GLM, Kimi, DeepSeek modelleri. Uç nokta ve model adı yapılandırmadan
gelir.
"""
