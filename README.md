# harness

LLM tabanlı coding agent harness'i: büyük dil modeline dosya okuma, yazma,
düzenleme, arama ve terminal araçları verip bunları denetimli bir döngüde
çalıştıran komut satırı uygulaması. Tekirdağ Namık Kemal Üniversitesi
Bilgisayar Mühendisliği bitirme projesi.

> Durum: çekirdek döngü, bağlam yöneticisi (uzun araç çıktısını kırpar), izin
> katmanı (ALLOW/ASK/DENY, oturum boyu izin ver), `read_file`/`write_file`/
> `edit_file`/`search`/`bash` araçları, çok turlu terminal sohbeti, görev
> sonrası yerel inceleme ekranı ve küçük bir görev seti üzerinde çalışan
> değerlendirme koşucusu (`tasks/`, `core/evaluation.py`) var. Gerçek model
> ChatGPT planı üzerinden (Responses API) bağlı; OpenAI uyumlu Chat
> Completions adaptörü (Ollama için) henüz CLI'a bağlanmadı.

## Kullanım

```sh
uv run harness demo                 # sahte modelle hazır görev: döngü, araç, hata, kayıt
uv run harness demo --tui           # tam ekran arayüzü sahte modelle dene
uv run harness login                # ChatGPT hesabıyla giriş (tarayıcı açılır)
uv run harness model                # model ve effort seç (numarayla); seçim kaydedilir
uv run harness ask "soru"           # tek görev; sade cevap, özet ve HTML inceleme kaydı
uv run harness chat                 # tam ekran: solda sohbet, sağda akış, altta mesaj
uv run harness chat --plain         # eski sade akış; boruda da otomatik seçilir
uv run harness inspect sessions/<dosya>.jsonl --open  # eski/yeni kaydı HTML'e çevir ve aç
uv run harness replay sessions/<dosya>.jsonl   # ayrıntılı terminal kaydını yeniden oynat
```

Tek görevlik değişiklik için: `harness ask "soru" --model gpt-6-sol --effort high`.

## Sohbet ve görev sonrası inceleme

`chat` etkileşimli terminalde Textual tabanlı **tam ekran** açar: solda kaydırılabilir
konuşma, sağda yaklaşık %20 genişlikte bağımsız işlem akışı, altta sabit mesaj
editörü bulunur. Çok dar terminalde sağ panelin okunabilir minimum genişliği korunur.
Konuşmanın sağ kenarındaki scrollbar, fare tekerleği veya PageUp/PageDown ile
kaydırılır; yukarı okurken yeni metin görünümü aşağı çekmez. **En alta** düğmesi
veya Ctrl+End son yanıta döner. Sağ akış paneli ayrıca kaydırılabilir.

Enter ya da Gönder mesajı yollar; Shift+Enter yeni satır ekler (Ctrl+Enter da gönderir).
Model çalışırken yeni taslak yazılabilir ama ikinci görev başlatılmaz. Esc/Durdur
turu iptal eder; Ctrl+Q veya `/exit` kaydı koruyarak çıkar. Ctrl+T açık/koyu temayı
değiştirir. Rapor veya `/inspect` son incelemeyi açar. Kısmi/başarısız geçmişle
devam edilmez: hata veya kesinti sonrası yeni sohbet açmak gerekir. Bu klavye ve
kaydırma davranışları Pilot testleriyle doğrulandı; fiziksel Windows Terminal ve
ekran okuyucu davranışı ayrıca doğrulanmadı.

`--plain` veya stdin/stdout'un terminal olmaması eski sade akışı seçer; burada
EOF veya `/exit` çıkar, Ctrl+C çalışan turu durdurur. Her iki arayüz aynı süreçte
geçmişi korur; kapatıp açınca otomatik devam etmez. Boş mesaj atlanır. Başarısız
tur ve önceki olaylar kaybolmaz. Ani kapanışta bitişsiz kayıt **yarım** sayılır.

Kullanıcı ve ajan mesajları **Sen / Ajan** başlıklarıyla ayrılır. Responses API'den
gelen metin parçaları tamamlanmayı beklemeden gösterilir; yapay yazma gecikmesi yoktur.
Tam ekran arayüzde Markdown akarak çizilir (bağlantılar otomatik açılmaz). `ask` ve
`chat --plain` akışı düz metin olarak yazar; akış sunmayan istemcinin tamamlanmış
yanıtını Markdown gösterir. Son yanıt tekrar basılmaz. Akış kesilirse **Yanıt
tamamlanmadı** görünür; kısmi metin başarı/geçmiş yanıtı sayılmaz.
Metin parçalarının sayısı token sayısı değildir; usage yine sağlayıcı tamamlanma
bilgisinden gelir. Modelin araç öncesi kullanıcıya yönelik açıklaması akabilir;
araç JSON argümanları, sonuçları ve reasoning akışı gösterilmez.

`ask` ve `chat` araç argümanlarını/çıktılarını sohbetin içine dökmez. Tur özeti tam
ekranda sağ akış panelinde, sade çıktıda rapor yolu ile birlikte gösterilir.
JSONL olayları geldikçe saklanır;
her tur sonunda bütün oturumun yeni bir HTML anlık görüntüsü oluşturulur.
`/inspect` son görüntüyü tarayıcıda açar. `inspect` komutu tarayıcıyı yalnız
`--open` verilirse açar; `--output yeni-dosya.html` ile hedef seçilebilir.
Var olan dosyalar ezilmez; otomatik hedef doluysa numaralı yeni ad kullanılır.
HTML yereldir; sunucu, internet, JavaScript veya harici kaynak gerektirmez.
`demo` açıkça sahte senaryodur; eski ayrıntılı terminal gösterimini ve HTML raporunu sunar.

İncelemede istem, model/effort, sıralı model ve araç olayları, çağrı kimliği,
argümanlar, tam araç sonucu/hatası ve süreler bulunur. Ayrıntılar tıklama veya
klavyede Tab + Enter ile açılır. Sağlayıcı bildirirse girdi/çıktı ve cache/reasoning
tokenları da görünür. Eksik ölçüm `—` olarak gösterilir; sıfır değildir. Kısmi
kullanımlardan tam tur toplamı ya da TPS üretilmez.

**Ortalama token/sn**, çıktı tokenı / toplam model çağrısı süresidir; saf üretim
hızı değildir. Model süresi ağ, kimlik doğrulama ve sağlayıcı beklemesini de
kapsar; araç süreleri ayrıdır. İlk metin süresi, ilk boş olmayan metin parçasına
kadardır (yalnız araç içeren yanıtta olmayabilir). Cache girdi, reasoning çıktı
tokenlarının alt kümesidir; toplam sayıya yeniden eklenmez. Bu ölçüm alanları
sahte HTTP yanıtlarıyla testlidir; mevcut hesabın canlı olarak hepsini döndürdüğü
varsayılmaz. Para maliyeti veya gizli düşünce metni gösterilmez.

**Gizlilik:** JSONL ve HTML, istem ve dosya içeriği gibi hassas bilgiler içerebilir;
otomatik gizli veri maskelemesi yoktur. Paylaşmadan önce kontrol edin.
**Tamamlandı**, modelin araç istemeyi bıraktığı anlamına gelir; testlerin geçtiği
veya istenen düzeltmenin doğrulandığı iddiası değildir.

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

`tasks/` dört katmanın dışındadır: her alt klasör bir değerlendirme görevi
(başlangıç durumu + gizli testler), biçimi `tasks/README.md`'de anlatılır.

## Geliştirme

Gereken tek araç [uv](https://docs.astral.sh/uv/):

```sh
uv sync                     # sanal ortamı ve bağımlılıkları kur
uv run pytest               # testler (katman kuralı dahil)
uv run ruff check           # lint
uv run ruff format --check  # biçim
uv run pyright              # tip kontrolü (strict)
```
