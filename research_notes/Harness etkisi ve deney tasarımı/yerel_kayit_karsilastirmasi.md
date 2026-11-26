# Yerel kayıt karşılaştırması: harness ile OpenCode, aynı model, aynı görev

Kaynak: Linux deney makinesindeki Harbor koşuları, 2026-11-18
(`~/Dev/_arsiv-ana-dizin/harness-bench/jobs/`). Görev: SWE-rebench-07-2026
`mozilla-ai__any-llm-1121`, "OtariProvider drops cache control during Anthropic-to-OpenAI
message conversion" (Python; doğru çözüm +64 satır, 1 dosya; görevi tanımlayan 9 test, korunan 23 test).
Model: her iki kolda `opencode-go/glm-5.3-flash`, effort `max`. Her kol n=1 (istatistik
değil, mekanizma ipucu).

## Sonuç tablosu

| | coding_harness (harness-smoke2) | OpenCode 1.19.6 (oc-smoke) |
|---|---|---|
| Sonuç | **0** (çözülmedi) | **1** (çözüldü) |
| Görevi tanımlayan testler | 6/9 geçti, 3 akış (streaming) testi FAILED | 9/9 |
| Korunan testler | hepsi geçti | hepsi geçti |
| Model çağrısı / adım | 71 | 92 ajan adımı |
| Araç çağrısı | 82 (bash 54, read_file 13, edit_file 8, update_plan 3, list_dir 2, search 2) | 114 (bash 76, read 15, edit 15, glob 4, todowrite 3, grep 1) |
| pytest koşusu | 15 kez (ilki 40. adımda) | 12 kez (ilki 56. adımda) |
| Girdi tokenı (önbellekten) | 4.02M (3.90M, %97) | 6.39M (6.24M, %97.6) |
| Çıktı tokenı | 26.4k | 15.7k |
| Ajan süresi | 19:20–19:54 UTC (~34 dk) | 20:18–20:52 UTC (~34 dk) |
| Bitiş | `reason=done` (model kendisi "bitti" dedi) | model kendisi bitirdi |

Kaynak dosyalar: harness kaydı
`harness-smoke2/mozilla-ai__any-llm-1121__cZgLuau/agent/sessions/any-llm/20261118-192018-80006725.jsonl`;
OpenCode kaydı `oc-smoke/mozilla-ai__any-llm-1121__i9ibpXY/agent/trajectory.json`;
doğrulama `*/verifier/report.json` ve `*/verifier/test-stdout.txt`.

## Harness neden başarısız oldu: tek bir await, kendi kendini doğrulayan sahte nesne

**Fark "erken bitirmek" değil.** Harness da akış kısmını yazdı, testleri 15 kez koştu,
ruff ve mypy kontrolü yaptı, tam birim test takımını da denedi. Çözümleri neredeyse
aynı mimaride: ikisi de `_amessages()` + `_stream_messages_async()` yazdı. Fark tek satırda:

- **Harness** (`otari.py`, 29. adımdaki düzenleme):
  `async for raw_event in self.otari_client.message(**kwargs, stream=True):`
  `message()` bir `async def`; önce `await` edilmesi gerekiyordu. Gizli testlerde
  `TypeError: 'async for' requires an object with __aiter__ method, got coroutine`
  (`otari.py:279`) ile 3 akış testi düştü.
- **OpenCode** (46. adım):
  `response = await self.otari_client.message(stream=True, **kwargs)` ve ardından `async for event in response:`.

**Nereden ayrıştılar: SDK'nın imzasına bakıp bakmamak.**
- OpenCode 10. adımda `inspect.signature(AsyncOtariClient.message)` ve kaynağını yazdırdı.
  Çıktı açıkça `async def message(...)` ve "When stream=True, returns an async iterator"
  diyordu; dönüş satırı `return self._stream("/messages", body, "messages")`.
- Harness 10. adımda `grep -n "def messages\|def completion\|class AsyncOtariClient\|/v1/messages" .../otari/*.py`
  çalıştırdı. Desen çoğul `def messages` olduğu için tekil `async def message(` satırı
  eşleşmedi; harness metodun imzasını hiç görmedi. 12. ve 14. adımda `_streaming.py`
  başını okudu, ama `message()`'ın bir coroutine olduğunu görmedi.

**Kendi testleri yanlışı neden yakalamadı: sahte nesne aynı yanlış varsayımı taşıyordu.**
Harness'ın yazdığı test `mocked_client.message = MagicMock(return_value=raw_stream())`
kullandı. Bu sahte metot `await` gerektirmeyen bir async iterator döndürüyor, yani kodun
yanlış varsayımını aynen kopyalıyor. "28 otari testi geçti" (41. adımdaki plan notu)
doğru ama yanıltıcı bir sinyaldi. Gizli testler gerçek imzaya uygun `AsyncMock` kullanınca
hata ortaya çıktı.

**Deney tasarımı için çıkarım:** Bu başarısızlık "model kendi testini koşmadı" türünden
değil. "Model, kendi yanlış varsayımını taşıyan testle kendini doğruladı" türünden.
Görev sonunda modelin kendi testlerini koşturan bir "bilgi" katmanı burada yardım
etmezdi; o testler zaten geçiyordu. Yardım edebilecek deterministik bilgiler:
1. Gerçek kütüphane imzası ya da tipi: `async def` olduğu, dönüş tipi (tip denetleyicinin
   ya da `inspect`'in harness tarafından sunulması).
2. Mock'suz bir duman testi: gerçek SDK nesnesiyle, ağ yerine yerel sahte sunucuyla
   bir akış çağrısı.
3. mypy'nin sıkı modu. Harness mypy koştu ve temiz çıktı: SDK `-> Any` döndürdüğü için
   tip bilgisi kaybolmuştu. Yani tip denetimi de bu hatayı yakalayamazdı.

Bu, n=1'lik bir ipucu ve stokastik: aynı model ve aynı harness başka bir koşuda imzaya
bakabilir. Ama hata türü (kendi kendini doğrulayan sahte nesne) bilinen, tekrar eden bir
ajan hatası. Ölçülmeye değer.

## Diğer gözlemler

- **Bağlam ve önbellek:** İki kolda da girdinin ~%97'si önbellekten geldi. OpenCode
  %59 daha fazla girdi tokenı harcadı (daha çok adım, 92'ye karşı 71), ama çıktı tokenı
  %40 daha azdı. Harness'ın düşünce metni 51k karakter.
- **Talimat dosyası:** Harness görevin başında deponun kendi AGENTS.md'sini (4.746
  karakter) talimata ekledi (`agents_md` katmanı). OpenCode'un sistem talimatı trajectory
  dosyasında görünmüyor; `agent/opencode/xdg-data/opencode/opencode.db` içinde olabilir
  (çıkarılmadı).
- **Planlama:** Harness `update_plan`'i 3 kez kullandı (27, 41, 70. adımlar), OpenCode
  `todowrite`'ı 3 kez (32, 60, 92). İkisi de görevi aynı üç alt adıma böldü.
- **Uzun test takımı:** Harness tam birim takımını iki kez koşmaya çalıştı, ikisi de
  300 ve 420 sn'lik zaman aşımına takıldı (55–56. adımlar). Sonra alt kümelere geçti;
  4 adım ve yaklaşık 12 dakika gitti. OpenCode da `timeout 570 pytest tests/unit`
  denedi (62–63. adımlar). Arka plan iş aracı ve akıllı zaman aşımı burada ölçülebilir
  bir fark yaratabilir.

## Çöken koşular

- **harness-smoke (ilk deneme):** 2 görev. `langchain4j__langchain4j-5315` (Java)
  **çözüldü (1.0)**. `any-llm-1121` kurulumda düştü (NonZeroAgentExitCodeError):
  `uv tool install` görev imajında PyPI'dan `pytest-9.2.0` indiremedi
  ("Failed to fetch: https://files.pythonhosted.org/..."). Bundan sonra harness,
  ağ gerektirmeyen `payload.tgz` (wheelhouse + taşınabilir Python 3.13) ile kurulmaya
  başlandı (`harness_agent.py`).
- **msa-smoke (mini-swe-agent):** Ajan, LiteLLM üzerinden `openai/glm-5.3-flash` olarak
  çağrıldı. Bağlantı hatası verdi
  (`litellm.InternalServerError: OpenAIException - Connection error`) ve hiç token
  harcanmadı. Büyük ihtimalle `OPENAI_BASE_URL` (OpenCode Go uç noktası) verilmemişti.
  Model değil, yapılandırma hatası.
- **install-smoke:** Yalnız kurulum denemesi, 18 sn, sorun yok.
- **oracle-30:** Seçilen 30 görev doğru çözümle 30/30 geçti. İlk koşu 13/30'da iptal
  edildi (lexical-8676'da Docker konteyneri bulunamadı), sonra devam ettirilerek tamamlandı.
