# harness

LLM tabanlı coding agent harness'i: büyük dil modeline dosya okuma, yazma,
düzenleme, arama ve terminal araçları verip bunları denetimli bir döngüde
çalıştıran komut satırı uygulaması. Tekirdağ Namık Kemal Üniversitesi
Bilgisayar Mühendisliği bitirme projesi.

> Durum: çekirdek döngü, bağlam kırpma, izin katmanı (araç türüne göre sorar),
> dokuz araç, dokuz sağlayıcı, tam ekran ve sade sohbet, `-p` ile tek seferlik görev,
> zaman çizelgeli koşu incelemesi ve küçük bir değerlendirme görev seti
> (`tasks/`, `core/evaluation.py`) var.

## Kullanım

Alışılmış kodlama ajanı CLI'larının biçimi:

```sh
harness                          # sohbet: terminalde tam ekran, boruda sade akış
harness -p "soru"                # tek görev; yalnız cevap stdout'a (betiklerde kullanılır)
harness -p "düzelt" --allow write_file --allow bash   # -p'de izin önceden verilir
harness -c                       # bu projenin son oturumuna devam (-p ile de olur)
harness -m opencode-go/glm-5.3 --effort high   # yalnız bu koşu için başka model
harness models [sağlayıcı]       # modeller ve effort seviyeleri (betikler için)
harness sessions                 # bu projenin oturumları
harness eval -m opencode-go/glm-5.3 --anatomy files --anatomy bash -n 3   # deney
harness eval --report <deney klasörü>  # biten deneyin özetini yeniden göster
harness watch [--evals]          # canlı panel: kayıtlar tarayıcıda akar
harness inspect <kayıt>.jsonl --open   # koşu incelemesini (HTML) üret ve aç
harness replay <kayıt>.jsonl     # ayrıntılı terminal gösterimiyle yeniden oynat
harness demo [--tui]             # sahte modelle hazır görev (eğitim; sağlayıcı çağrısı yok)
harness login | logout           # ChatGPT girişi terminalden (sohbette: /connect chatgpt)
harness -V                       # sürüm
```

`-m` sağlayıcı adıyla başlarsa sağlayıcı da değişir; OpenRouter'ın kendi `/`'lu
model adları için tam yol yazılır (`openrouter/openai/gpt-oss-120b`). `ask` ve
`chat` eski adlarıyla da çalışır. Geliştirme sırasında komutların başına `uv run`
eklenir; `uv tool install -e .` ile `harness` her klasörden çağrılır.

### Sohbet içinde seçim ve bağlantı

Sağlayıcı bağlamak ve model seçmek sohbetin içindedir (Claude Code, Codex ve
OpenCode gibi). Argümansız yazılan seçim komutu bir liste açar: tam ekranda
yazdıkça süzülen bir pencere (↑/↓, Enter, Esc), sade akışta numaralı tablo.

- `/model`: bağlı bütün sağlayıcıların modelleri tek listede, sağlayıcıya göre
  gruplu; bağlı olmayan sağlayıcılar sonda, seçilince bağlanmaya gider. Model
  seçilince düşünme seviyesi sorulur. Doğrudan: `/model opencode-go/glm-5.3`.
- `/connect`: sağlayıcılar ve durumları. ChatGPT seçilince tarayıcıda giriş açılır;
  diğerlerinde API anahtarı gizli bir alana girilir, `~/.harness/auth.json`'a
  (yalnız sahibinin okuyacağı izinle) kaydedilir ve o sağlayıcının modelleri gelir.
  `/disconnect <ad>` kaydı siler.
- `/effort`, `/anatomy`, `/resume` (`/sessions`): aynı biçimde liste ya da ad.
- `/watch`: canlı paneli tarayıcıda açar. Sohbet terminalde sürer, panel aynı
  kaydı okuyup akıtır (modele giden bağlam, düşünce, araçlar, token ve önbellek
  payı); hiçbir şey kaybolmaz, onaylar terminalde verilir.
  **Harita** sekmesi ajanın proje ağacında nerede dolaştığını gösterir: okunan,
  aranan, düzenlenen dosyalar sırayla yaylarla bağlanır, aramalar eşleştiği
  dosyalara kesik çizgiyle açılır, dosya yanındaki şerit okunan satır aralıklarını
  gösterir (koyu yer: tekrar okunmuş). Zaman çubuğuyla video gibi geri sarılır
  (boşluk: oynat, ←/→: önceki/sonraki eylem); konum adrese yazılır (`&t=8`),
  o an link olarak paylaşılabilir. Sağda o adımda modelin ne düşündüğü durur.
  "İçe aktarmalar" açıkken odaktaki dosyanın proje içi importları yeşil yaylarla
  görünür; üst şerit geçişlerin kaçının bir import bağlantısını izlediğini ve okunan
  satırların tekrar payını sayar. Biten bir kayıt doğrudan haritada açılır:
  `harness watch <kayıt>.jsonl`.
- `/theme`: renk teması (koyu, açık, yüksek-kontrast, renk-körü; `~/.harness/theme.toml`
  ile kişisel). Tam ekranda Ctrl+T sıradakine geçer. `NO_COLOR` sade akışı renksiz yapar.
- `/status` · `/tools` · `/usage` (`/cost`) · `/new` (`/clear`) · `/inspect` · `/exit` (`/quit`).

Model, effort, anatomi ve tema seçimleri kalıcıdır: bir sonraki `harness` aynı
seçimlerle açılır. Geçiş sırasında geçmiş korunur; "oturum boyu" izinler `/new`,
`/resume` ve anatomi değişiminde sıfırlanır.

## Sağlayıcılar

| Ad | API | Kimlik |
|---|---|---|
| `chatgpt` | Responses (ChatGPT planı) | tarayıcıyla giriş |
| `openai` | Chat Completions | `OPENAI_API_KEY` |
| `anthropic` | Messages (resmi SDK) | `ANTHROPIC_API_KEY` |
| `openrouter` | Chat Completions | `OPENROUTER_API_KEY` |
| `deepseek` | Chat Completions | `DEEPSEEK_API_KEY` |
| `opencode` | OpenCode Zen; model başına Responses / Messages / Chat | `OPENCODE_API_KEY` |
| `opencode-go` | OpenCode Go aboneliği; aynı yönlendirme | `OPENCODE_API_KEY` |
| `ollama` | Chat Completions (yerel) | yok; adres `OLLAMA_HOST` |
| `lmstudio` | Chat Completions (yerel) | yok |

Kimlik sütunundaki ad, `/connect` ile girilen anahtarın kayıt adıdır; aynı adlı
ortam değişkeni tanımlıysa o geçerlidir. Yeni bir OpenAI uyumlu sağlayıcı `llm/providers.py` tablosuna bir satırdır. Sağlayıcı düşünce
özeti veriyorsa (Responses `reasoning.summary`, Claude `display: summarized`,
DeepSeek `reasoning_content`) sohbette canlı görünür, kayda ve incelemeye yazılır.

**OpenCode** tek anahtarla birçok sağlayıcının modelini sunar ama her modeli kendi
API biçimiyle konuşur: GPT/Grok Responses, Claude ve bazı Qwen/MiniMax modelleri
Anthropic Messages, geri kalanı Chat Completions. Hangi modelin hangi biçimle
gittiği [models.dev](https://models.dev) kataloğundan okunur (OpenCode da oradan
okur); katalog `~/.harness/cache/` altında bir gün saklanır, ağ yoksa model
ailesine bakan kurallar kullanılır. Gemini modelleri (Google API biçimi) henüz
desteklenmez ve listede çıkmaz. Ücretsiz modeller yalnız OpenCode'un kendi
uygulamasında çalışır; harness gibi dış istemciler Zen bakiyesi ya da Go
aboneliği ister. `/model` listesi ve `harness models opencode-go` her modelin biçimini gösterir.

## Anatomi ve araçlar

| Anatomi | Seviye | Araçlar |
|---|---|---|
| `raw` | 0 | yok; harness cevaptaki adlı kod bloklarını dosyaya yazar |
| `files` | 1 | `read_file`, `list_dir`, `search`, `write_file`, `edit_file`, `delete_file`, `update_plan` |
| `bash` | 2 | `files` + `bash` |
| `full` | 3 | `bash` + `fetch_url` |

Paketin üstüne ek katmanlar `+ad` ile açılır, `-ad` ile kapanır (`bash+loop_guard`;
sohbette `/anatomy` listesinde tek tıkla):

| Katman | Ne yapar |
|---|---|
| `loop_guard` | Aynı araç aynı argümanlarla art arda 3. kez çağrılınca sonuca hatırlatma ekler, 5.'de çağrıyı çalıştırmaz |

Her araya giriş `HarnessIntervened` olayı olarak kayda geçer; `/usage`, inceleme
sayfası, canlı panel ve `harness eval` "harness kaç kez müdahale etti" sorusunu
buradan sayar.

### Deney: `harness eval`

`tasks/` altındaki her görev (`task.md` + `repo/` + gizli `tests/`) her varyantla
(model × anatomi) `-n` kez koşulur, gizli testlerle puanlanır. Tekrarlar iç içe
koşar (önce her şeyin 1. tekrarı), yarıda kesilen deney de dengeli kalır. Özet:
geçme oranı ve %95 Wilson aralığı, "bitti" diyen koşu sayısı (geçmekten ayrı),
ortalama adım, süre, token, önbellek payı, araç hatası, müdahale ve tekrar okuma
(okunan satırların aynı dosyada daha önce okunmuş payı). Her koşunun
kaydı `~/.harness/evals/<deney>/runs/` altındadır; `--watch` deneyi canlı izletir.

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
araç JSON argümanları ve sonuçları gösterilmez. Sağlayıcının verdiği düşünce
(özet ya da açık düşünce) cevabın üstünde soluk bir **Düşünce** bloğunda canlı akar;
tam ekranda başlığına tıklayınca katlanır, sade akışta `düşünce ·` ile başlar.

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
