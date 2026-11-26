# harness

LLM tabanlı coding agent harness'i: büyük dil modeline dosya okuma, yazma,
düzenleme, arama ve terminal araçları verip bunları denetimli bir döngüde
çalıştıran komut satırı uygulaması. Tekirdağ Namık Kemal Üniversitesi
Bilgisayar Mühendisliği bitirme projesi.

> Durum: çekirdek döngü, bağlam kırpma, izin katmanı (araç türüne göre sorar),
> dokuz araç, dokuz sağlayıcı, tam ekran ve sade sohbet, `-p` ile tek seferlik görev,
> zaman çizelgeli koşu incelemesi ve küçük bir değerlendirme görev seti
> (`tasks/`, `core/evaluation.py`) var.
>
> Sunum akışı → [`docs/SUNUM.md`](docs/SUNUM.md).

## Kullanım

Alışılmış kodlama ajanı CLI'larının biçimi:

```sh
harness                          # sohbet: terminalde tam ekran, boruda sade akış
harness -p "soru"                # tek görev; yalnız cevap stdout'a (betiklerde kullanılır)
harness -p "düzelt" --allow write_file --allow bash   # -p'de izin önceden verilir
harness -c                       # bu projenin son oturumuna devam (-p ile de olur)
harness -r                       # açılışta geçmiş oturumlardan birini seç
harness -m opencode-go/glm-5.3 --effort high   # yalnız bu koşu için başka model
harness models [sağlayıcı]       # modeller ve effort seviyeleri (betikler için)
harness sessions                 # bu projenin oturumları
harness eval -m opencode-go/glm-5.3 --anatomy files --anatomy bash -n 3   # deney
harness eval --report <deney klasörü>  # biten deneyin özetini yeniden göster
harness watch [--evals]          # canlı panel: kayıtlar tarayıcıda akar
harness serve [-m ...]           # tarayıcıda sohbet (tek sayfa + WebSocket)
harness desktop [-m ...]         # serve ile aynı, tarayıcı yerine masaüstü penceresinde
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
- Yalnız `/` yazıp Enter: aranabilir komut listesi. `/` ile başlayınca eşleşen komutlar
  durum satırında görünür, Tab tamamlar. Bilgi komutları (`/help`, `/status`, `/tools`,
  `/usage`, `/diff`) sohbete yazılmaz, Esc ile kapanan pencerede açılır; onaylar
  ("Tema: açık", "Yeni oturum") kısa bildirim olarak çıkar.
- `/sessions`: geçmiş oturumlar gün gruplarıyla (Bugün, Dün, Bu hafta...); satır ilk
  görev, yazarak aranır. Seçilen oturum için **Devam et**, **Panelde aç** (defter ve
  dosya tablosuyla incele), **Dışa aktar** (Markdown). `/resume <arama>` görevinde o
  sözcük geçen oturuma doğrudan döner; birden çok eşleşme varsa süzülmüş liste açar.
- `/effort`, `/anatomy`: aynı biçimde liste ya da ad. `/memory` hafıza katmanını
  açıp kapatır (çalışmayacaksa nedenini söyler).
- `/diff`: son commit'ten beri çalışma dizinindeki değişiklikler. `/copy`: ajanın son
  cevabını panoya. `/export [dosya]`: oturumun Markdown dökümü (varsayılan kaydın yanı).
- `/watch`: canlı paneli tarayıcıda açar. Sohbet terminalde sürer, panel aynı
  kaydı okuyup akıtır; hiçbir şey kaybolmaz, onaylar terminalde verilir.
  **Akış** sekmesi bir defterdir: üstte koşunun durumu (tamamlandı / adım sınırı /
  hata) ve harness müdahaleleri, altında zaman çizelgesi (model çağrısı "ilk token
  bekleme" ve "üretim" diye ikiye bölünür; araçlar, harness işaretleri ve her adımda
  modele giden bağlamın boyutu; sürükleyince defter o aralığa süzülür). Defterde her
  kayıt bir satırdır (adım adım); satıra tıklayınca sağdaki denetçide model satırı
  için **Model ne gördü** (o adımda modele giden mesajların birebir kopyası, yeniler
  işaretli, kesik sonuçlar etiketli), araç satırı için **Modele giden** ve kesildiyse
  **Aracın tam çıktısı** ayrı sekmelerde. Eski kayıtlarda harness'ın sessizce kestiği
  sonuçlar "sessiz kesme" diye görünür.
  **Dosyalar** sekmesi bir tablodur: her satır ajanın dokunduğu bir dosya, her sütun
  bir adım; hücredeki harf ne yapıldığını söyler (O okudu, D düzenledi, Y yazdı,
  S sildi, • aramada çıktı). Üstte komut ve arama şeritleri, düz cümleyle özet
  ("12 adımda 6 dosyaya dokundu, 2'sini değiştirdi... okunan satırların %24'ü tekrar").
  Hücreye tıklayınca o adımda modelin ne dediği ve sonuç; oradan Akış'ta açılır.
  Biten bir kayıt doğrudan açılır: `harness watch <kayıt>.jsonl`. Defterde düzenleme
  ve yazma satırlarında **Fark** sekmesi var (yazmada eski hâl, dosya önceden tam
  okunduysa o okumadan çıkarılır).
- `/theme`: renk teması (koyu, açık, yüksek-kontrast, renk-körü; `~/.harness/theme.toml`
  ile kişisel). Tam ekranda Ctrl+T sıradakine geçer. `NO_COLOR` sade akışı renksiz yapar.
- `/panel` (`/flow`): sağ panel görünümü (özet, akış, kapalı; Ctrl+B dolaşır).
  `/thinking` (`/thoughts`): düşünce görünümü (katlı, açık, gizli). İkisi de kalıcıdır.
- `/status` · `/tools` · `/usage` (`/cost`) · `/new` (`/clear`) · `/inspect` · `/help` · `/exit` (`/quit`).

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

Varsayılan `bash+tool_guidance`'tır: `bash` paketi + araç arayüzü yönlendirmesi (komut
çalıştırır, her çağrıda izin sorar). `bash` paketinin kendisi değiştirilmez; deneylerin
tabanıdır. Her koşu `TaskStarted.fingerprint`'e talimat ve araç şemalarının özetini yazar:
aynı parmak izi = modele aynı harness ayarı gitti.
`read_file` bir seferde en çok 2.000 satır / ~30.000 karakter verir ve devamın
`start_line`'ını söyler.

Paketin üstüne ek katmanlar `+ad` ile açılır, `-ad` ile kapanır (`bash+loop_guard`;
sohbette `/anatomy` listesinde tek tıkla):

| Katman | Ne yapar |
|---|---|
| `loop_guard` | Aynı araç aynı argümanlarla art arda 3. kez çağrılınca sonuca hatırlatma ekler, 5.'de çağrıyı çalıştırmaz |
| `output_limit` (açık) | 40.000 karakteri aşan araç çıktısının ortasını keser; modele söyler, tam çıktı kayıtta kalır (`-output_limit` kapatır) |
| `agents_md` (açık) | Projedeki `AGENTS.md`'yi (yoksa `CLAUDE.md`, `@dosya` satırları izlenir) sistem talimatına ekler. `ctx init`'in yazdığı hafıza bölümü (harita bloğunu taşıyan başlık) bu katmana girmez; o bölüm `memory` katmanına aittir |
| `memory` | agent-context hafızası (`ctx`): görev başında ctx'in kendi kancası haritayı ve kullanım kurallarını talimata ekler, dosya okunup yazılınca o dosyayı anan notlar sonuca eklenir. Kapalıyken modele hafızadan hiç söz edilmez. `ctx` ya da `context.yaml` yoksa çalışmadığını söyler |
| `tool_guidance` (varsayılanda açık) | Araç arayüzü: `read_file` satır öneki numara + sekme, `edit_file` sonucu düzenlenen bölgeyi gösterir, bash açıklaması okuma için dosya araçlarını önerir, talimat bağımsız çağrıları birlikte istemeyi söyler |
| `timeout_hint` | bash zaman aşımında daha dar testi ve arka plan işini önerir; tam takımı koşmaktan caydırmaz |
| `scope_focus` | Talimata ekler: kurulu ortamı yeniden kurma, isteğin dışında biçim/tip/lint cilası yapma. Depo talimatıyla çatışabilir, ölçülmeden açılmaz |

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
konuşma, sağda panel, altta sabit mesaj editörü bulunur. Sağ panelin üç görünümü var
(`/panel`, Ctrl+B sıradakine geçer, seçim kalıcıdır):

| Görünüm | Sağda | Sohbette |
|---|---|---|
| `ozet` (varsayılan) | Tur/adım/süre sayacı, şu an ne çalıştığı, plan, dokunulan dosyalar (O okundu, D düzenlendi, Y yazıldı, S silindi; değişenler üstte) ve oturum ölçüleri (model çağrısı, araç/hata, token, bağlam) | Her araç tek, soluk bir satır: `✓ read_file · 2 satır` |
| `akis` | Her olay ayrı satır: eğitim ve izleme için | Araç satırı yok (akıştadır) |
| `kapali` | Panel yok | Her araç tek satır |

Araç satırı başlarken `● ad …` yazar, bitince aynı satır sonuca döner (iki ayrı satır
olmaz). Yol, komut ve çıktı canlı ekranda görünmez; bunlar rapordadır. 100 sütundan dar
terminalde panel kendiliğinden gizlenir, araçlar sohbete geçer. Konuşmanın sağ
kenarındaki scrollbar, fare tekerleği veya PageUp/PageDown ile kaydırılır; yukarı
okurken yeni metin görünümü aşağı çekmez. **En alta** düğmesi veya Ctrl+End son
yanıta döner. Sağ panel ayrıca kaydırılabilir.

Enter ya da Gönder mesajı yollar. Yeni satır: **Ctrl+J** ya da satır sonunda `\` +
Enter (her terminalde çalışır); Shift+Enter yalnız gelişmiş klavye protokolünü
destekleyen terminallerde (kitty, WezTerm, foot, Ghostty) ayrıdır, GNOME Terminal ve
Konsole onu düz Enter olarak yollar. **Ctrl+V** sistem panosunu yapıştırır (Linux'ta
`wl-paste`/`xclip`); kopyalama (`/copy`, seçip Ctrl+C) terminalin OSC 52 desteğine ek
olarak `wl-copy`/`xclip` ile de yazar, çünkü VTE tabanlı terminaller OSC 52'yi yok sayar.
Wayland'da `wl-clipboard` paketi önerilir. **Ctrl+C** sırayla: seçili metni kopyalar,
çalışan turu durdurur, taslağı siler; boştayken art arda iki kez basılırsa çıkar.

Kısayollar `~/.harness/keybindings.json` ile değiştirilir: `{"panel": "f2",
"quit": "ctrl+q,ctrl+d"}`. Adlar: `quit`, `stop`, `interrupt`, `panel`, `theme`,
`latest`, `page_up`, `page_down`; değer Textual tuş adıdır (virgül: birden çok tuş).
Alt satırdaki kısayol listesi değişikliği gösterir.

**Görsel ekleme.** Mesaja görsel üç yoldan eklenir:

- Panoda metin yoksa **Ctrl+V** panodaki görseli ekler (ekran görüntüsü). Linux'ta `wl-paste` ya da `xclip` gerekir.
- Görsel dosyasını terminale **sürükleyip bırakmak** onu ekler. Terminal yolu yapıştırır; tırnaklı, `file://` ya da kaçışlı boşluklu yollar tanınır. Yapıştırılanın tamamı var olan görsel dosyaları değilse metin olarak kalır.
- Tek görevde `harness -p "bu ne?" -i ekran.png` (`--image` tekrarlanabilir; sohbette değil, yalnız `-p` ile).

Ek, imlecin yerine `[Görsel 1]` etiketi olarak girer. Mesaj kutusunun üstündeki satır ekleri gösterir. Etiket metinden silinirse ek de düşer.

PNG, JPEG, GIF ve WebP kabul edilir; tür dosya adından değil ilk baytlardan anlaşılır. Görsel başına sınır 5 MB'tır (Anthropic API'sinin sınırı).

Baytlar mesajın içine yazılmaz: görsel, içeriğinin özetiyle `~/.harness/attachments/` altına kaydedilir, mesaj ve kayıt yalnız yolu taşır. `/resume` görselleri de geri yükler; dosya silinmişse modele bunu söyleyen bir not gider.

Model listesi görsel desteğini bildiriyorsa ona bakılır: OpenCode modellerinde models.dev, ChatGPT ve OpenRouter'da API'nin giriş türleri; Claude modelleri her zaman görsel okur. Görsel desteklemeyen modelde mesaj gönderilmez, taslak korunur ve durum satırı `/model` önerir. Destek bilinmiyorsa görsel gönderilir.

Model çalışırken yeni taslak yazılabilir ama ikinci görev başlatılmaz. Model `ask_user`
ile soru sorduğunda soru konuşmaya bir mesaj olarak düşer; cevap alt editörden yazılır
(Enter ya da Gönder cevabı yollar, boş Enter cevap yok demektir; soru beklerken de bilgi
komutları çalışır). Esc/Durdur
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
(özet ya da açık düşünce) cevabın üstünde soluk bir **Düşünce** bloğunda durur.
Tam ekranda görünümü `/thinking` seçer: `katli` (varsayılan; tek satır "Düşünce · 37
kelime", akarken "Düşünüyor…", tıklayınca açılır), `acik` (akarken tamamen görünür) ya
da `gizli` (çizilmez; kayıtta ve incelemede kalır). Görünüm değişince geçmiş düşünceler
de uyar. Sade akışta `düşünce ·` ile başlar.

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
