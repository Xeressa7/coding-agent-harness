# harness

LLM tabanlı coding agent harness'i: büyük dil modeline dosya okuma, yazma,
düzenleme, arama ve terminal araçları verip bunları denetimli bir döngüde
çalıştıran komut satırı uygulaması. Tekirdağ Namık Kemal Üniversitesi
Bilgisayar Mühendisliği bitirme projesi.

> Durum: çekirdek döngü, bağlam kırpma, izin katmanı (araç türüne göre sorar),
> dokuz araç, yedi sağlayıcı, tam ekran ve sade sohbet, `-p` ile tek seferlik görev,
> zaman çizelgeli koşu incelemesi ve küçük bir değerlendirme görev seti
> (`tasks/`, `core/evaluation.py`) var.

## Kullanım

Alışılmış kodlama ajanı CLI'larının biçimi:

```sh
harness                          # sohbet: terminalde tam ekran, boruda sade akış
harness -p "soru"                # tek görev; yalnız cevap stdout'a (betiklerde kullanılır)
harness -p "düzelt" --allow write_file --allow bash   # -p'de izin önceden verilir
harness -c                       # bu projenin son oturumuna devam (-p ile de olur)
harness --provider anthropic -m claude-opus-5-5 --effort high   # yalnız bu koşu için
harness -V                       # sürüm
```

Kalıcı seçimler ayrı ayrı yapılır (numaralı menü ya da doğrudan ad):

```sh
harness provider [ad]    # sağlayıcı; her sağlayıcıda son kullanılan model hatırlanır
harness model [ad]       # yalnız model (geçerli effort korunur)
harness effort [seviye]  # yalnız düşünme seviyesi; "varsayılan" modelin kendi seçimine döner
harness anatomy [ad]     # açık katmanlar: raw, files, bash, full
```

Bilgi ve inceleme:

```sh
harness status           # seçimler, proje, kayıt klasörü, sağlayıcıların hazırlığı
harness providers        # sağlayıcılar ve anahtar/giriş durumu (ağa çıkmadan)
harness models           # sağlayıcının modelleri ve effort seviyeleri
harness sessions         # bu projenin oturumları
harness inspect <kayıt>.jsonl --open   # koşu incelemesini (HTML) üret ve aç
harness replay <kayıt>.jsonl           # ayrıntılı terminal gösterimiyle yeniden oynat
harness demo [--tui]     # sahte modelle hazır görev (eğitim; sağlayıcı çağrısı yok)
harness login | logout   # ChatGPT planı girişi
```

`ask` ve `chat` eski adlarıyla da çalışır. Geliştirme sırasında komutların başına
`uv run` eklenir; `uv tool install -e .` ile `harness` her klasörden çağrılır.

### Sohbet içi komutlar

`/status` · `/provider` · `/model` · `/effort` · `/anatomy` · `/tools` · `/usage`
(`/cost`) · `/new` (`/clear`) · `/sessions` · `/resume <no>` · `/inspect` · `/exit`
(`/quit`). Değişiklikler yalnız o sohbet içindir; sağlayıcı/model/effort değişince
geçmiş korunur, "oturum boyu" izinler `/new`, `/resume` ve anatomi değişiminde sıfırlanır.

## Sağlayıcılar

| Ad | API | Kimlik |
|---|---|---|
| `chatgpt` | Responses (ChatGPT planı) | `harness login` |
| `openai` | Chat Completions | `OPENAI_API_KEY` |
| `anthropic` | Messages (resmi SDK) | `ANTHROPIC_API_KEY` |
| `openrouter` | Chat Completions | `OPENROUTER_API_KEY` |
| `deepseek` | Chat Completions | `DEEPSEEK_API_KEY` |
| `ollama` | Chat Completions (yerel) | yok; adres `OLLAMA_HOST` |
| `lmstudio` | Chat Completions (yerel) | yok |

Anahtarlar yalnız ortam değişkeninden okunur, diske yazılmaz. Yeni bir OpenAI
uyumlu sağlayıcı `llm/providers.py` tablosuna bir satırdır. Sağlayıcı düşünce
özeti veriyorsa (Responses `reasoning.summary`, Claude `display: summarized`,
DeepSeek `reasoning_content`) kayda ve incelemeye yazılır.

## Anatomi ve araçlar

| Anatomi | Seviye | Araçlar |
|---|---|---|
| `raw` | 0 | yok; harness cevaptaki adlı kod bloklarını dosyaya yazar |
| `files` | 1 | `read_file`, `list_dir`, `search`, `write_file`, `edit_file`, `delete_file`, `update_plan` |
| `bash` | 2 | `files` + `bash` |
| `full` | 3 | `bash` + `fetch_url` |

Okuma, arama ve plan araçları sormadan çalışır; yazma, komut ve ağ araçları izin
ister (çalışma dizini dışı her zaman reddedilir). Her araç ne yapacağını
("README.md · satır 1–40") ve sonucunu ("40 satır", "çıkış kodu 1") kayda yazar.

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

Sohbet araç argümanlarını/çıktılarını ekrana dökmez; her araç için yalnız ne olduğu
tek satırda görünür ("✓ read_file · 40 satır"). Tur özeti tam ekranda sağ akış
panelinde, sade çıktıda rapor yolu ile birlikte gösterilir.
JSONL olayları geldikçe saklanır;
her tur sonunda bütün oturumun yeni bir HTML anlık görüntüsü oluşturulur.
`/inspect` son görüntüyü tarayıcıda açar. `inspect` komutu tarayıcıyı yalnız
`--open` verilirse açar; `--output yeni-dosya.html` ile hedef seçilebilir.
Var olan dosyalar ezilmez; otomatik hedef doluysa numaralı yeni ad kullanılır.
HTML yereldir; sunucu, internet, JavaScript veya harici kaynak gerektirmez.
`demo` açıkça sahte senaryodur; eski ayrıntılı terminal gösterimini ve HTML raporunu sunar.

İnceleme her tur için bir **koşu sayfasıdır**: özet kutuları; kayıttan otomatik
çıkan bulgular (sürenin ne kadarı model beklemesi, izin beklemesi, en uzun adım,
ne yaptığını yazmayan adımlar, geçmişin her adımda yeniden gönderilmesi ve
önbellek, hatalar, değişen dosyalar); model, araç türleri ve izin için ayrı
şeritli bir **zaman çizelgesi**; adım başına girdi tokenının ne kadarının tekrar,
ne kadarının yeni olduğu; düşünce özeti ve araç çipleriyle adım kartları; planın
ilerleyişi ve dosya tablosu. Ham olay kaydının tamamı (bağlam, argümanlar, tam
araç sonuçları) her turun altında kapalı durur. Dosya başına token **tahmindir**
(≈); adım toplamları sağlayıcının ölçümüdür. Eksik ölçüm `—` olarak gösterilir;
sıfır değildir. Kısmi kullanımlardan tam toplam üretilmez, "en az" denir.

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
