# harness

LLM tabanlı coding agent harness'i: büyük dil modeline dosya okuma, yazma,
düzenleme, arama ve terminal araçları verip bunları denetimli bir döngüde
çalıştıran komut satırı uygulaması. Tekirdağ Namık Kemal Üniversitesi
Bilgisayar Mühendisliği bitirme projesi.

> Durum: çekirdek döngü, `read_file` aracı ve oturum kaydı çalışıyor; gerçek model
> ChatGPT planı üzerinden (Responses API) bağlı. Sırada izin katmanı ve kalan araçlar.

## Kullanım

```sh
uv run harness demo                 # sahte modelle hazır görev: döngü, araç, hata, kayıt
uv run harness login                # ChatGPT hesabıyla giriş (tarayıcı açılır)
uv run harness model                # model ve effort seç (numarayla); seçim kaydedilir
uv run harness ask "soru"           # gerçek modele görev ver; projede dosya okuyabilir
uv run harness replay sessions/<dosya>.jsonl   # kaydedilmiş oturumu yeniden oynat
```

Tek görevlik değişiklik için: `harness ask "soru" --model gpt-6-sol --effort high`.

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
