# harness

LLM tabanlı coding agent harness'i: büyük dil modeline dosya okuma, yazma,
düzenleme, arama ve terminal araçları verip bunları denetimli bir döngüde
çalıştıran komut satırı uygulaması. Tekirdağ Namık Kemal Üniversitesi
Bilgisayar Mühendisliği bitirme projesi.

> Durum: iskelet. Modüller boş; her birinin başında ne iş yapacağı yazıyor.

## Mimari

Dört katman, yukarıdan aşağı. Üstteki alttakini kullanır, tersi yasak:

```
cli/            Ön yüz: kullanıcıyla konuşur, çekirdeğin olaylarını ekrana çizer
core/           Agent çekirdeği: döngü, izin, bağlam, kayıt; arayüzden habersiz
llm/  tools/    Model adaptörleri ve araçlar; birbirinden bağımsız
environments/   Komutların çalıştığı yer (yerel makine; ileride Docker)
messages.py     Herkesin konuştuğu ortak veri tipleri
```

Kural `pyproject.toml` içindeki import-linter sözleşmesinde tanımlıdır ve
`tests/test_architecture.py` tarafından her test koşumunda denetlenir.

## Geliştirme

Gereken tek araç [uv](https://docs.astral.sh/uv/):

```sh
uv sync                     # sanal ortamı ve bağımlılıkları kur
uv run pytest               # testler (katman kuralı dahil)
uv run ruff check           # lint
uv run ruff format --check  # biçim
uv run pyright              # tip kontrolü (strict)
```
