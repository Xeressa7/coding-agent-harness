"""LLM tabanlı coding agent harness'i.

Katmanlar (yukarıdan aşağı; üstteki alttakini kullanır, tersi yasak):

    cli/           Ön yüz: kullanıcıyla konuşur, çekirdeğin olaylarını ekrana çizer
    core/          Agent çekirdeği: döngü, izin, bağlam, kayıt; arayüzden habersiz
    llm/, tools/   Model adaptörleri ve araçlar; birbirinden bağımsız
    environments/  Komutların çalıştığı yer (yerel makine; ileride Docker)
    messages.py    Herkesin konuştuğu ortak veri tipleri

Kural, pyproject.toml'daki import-linter sözleşmesiyle denetlenir.
"""
