# Sunum akışı

Hocaya canlı demo için adım adım plan. Hedef: `harness serve` ile tarayıcıda sohbet,
yanında `harness watch` ile canlı panel; ücretsiz ve internetsiz (Ollama, yerel).

## Hazırlık (sunumdan önce)

```sh
ollama list   # qwen2.5-coder:7b (ya da :3b) kurulu olmalı
uv run pytest -q   # 412 passed, 1 skipped bekleniyor
```

İki terminal açık tutulacak: biri `serve`, biri `watch` için.

## Canlı akış

1. **Terminal 1 — tarayıcıda sohbet:**

   ```sh
   uv run harness serve -m ollama/qwen2.5-coder:7b --effort medium
   ```

   `http://127.0.0.1:8765` tarayıcıda açılır (komut bunu otomatik yapmaz; `--demo`
   vermediğimiz için gerçek yerel modelle konuşuruz).

2. **Terminal 2 — canlı panel:**

   ```sh
   uv run harness watch
   ```

   Tarayıcıda ikinci bir sekme açılır; `serve` bağlantısı başladığı an burada bir
   kayıt belirir, **Akış** defteri canlı akar.

3. **Örnek görev** (tarayıcıdaki sohbete yazılır): küçük, dosya okuyup küçük bir
   düzenleme yapan bir şey, örn.

   > `README.md` dosyasının ilk satırını oku ve bana söyle.

   Bu modelle (qwen2.5-coder:7b, Ollama) iki şey canlı görülebilir, ikisi de bu
   sürümde düzeltildi:

   - **Düşünme parametresi:** `--effort` seçiliyken bu model "thinking"
     desteklemediği için Ollama 400 döner; harness bunu modele hiç göstermeden,
     `effort` olmadan bir kez sessizce yeniden dener (`llm/chat_completions.py`).
     Panelde bir şey görünmez — tam da beklenen budur (kullanıcıya hiç yansımaz).
   - **Düz metin araç çağrısı:** model zayıfsa `read_file` çağrısını yapılandırılmış
     alana değil cevap metnine JSON olarak yazabilir. Bu durumda **watch** panelinin
     Akış defterinde `Araç çağrısı yakalandı · tool_call_fallback` satırı belirir —
     harness'ın bunu yakalayıp gerçek bir araç çağrısı gibi çalıştırdığının kanıtı
     (`core/tool_call_fallback.py`; `bash` anatomisinde varsayılan açık).

4. Panelde **Dosyalar** sekmesine geçip hangi dosyaya dokunulduğunu, **Akış**
   defterinde adım adım model/araç sürelerini göstermek sunuma iyi gider.

## Yedek plan (bağlantı ya da model sorunu olursa)

- `uv run harness demo --tui` — tamamen sahte modelle, hiç ağ/sağlayıcı çağrısı
  yapmadan aynı arayüzü gösterir. Önceden bir kere çalıştırılıp denenmesi önerilir.
- Önceden kaydedilmiş bir oturumu tekrar oynatmak için: sunumdan önce bir kez
  `serve`/`chat` ile gerçek bir görev çalıştırıp kaydı not edin
  (`~/.harness/sessions/<proje>/...jsonl`), sonra:

  ```sh
  uv run harness replay <kayıt>.jsonl
  ```

  Bu da ağ gerektirmez, terminalde adım adım o koşuyu yeniden gösterir.

## Hata durumunda okunması gereken yer

Bir görev gerçekten hata ile biterse (TUI'de "Hata: …" satırı, sade akışta aynı
satır) — ayrıntı her zaman oturum kaydında (`.jsonl`) ve `harness inspect
<kayıt>.jsonl --open` ile açılan HTML raporunda da durur.
