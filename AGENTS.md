## Hafıza

Canonical ürün hafızası: `.memory` (Git + Markdown; ayrıntı: oradaki README).
Bu repo için retrieval filtresi **product + current**. Göreve başlamadan görev
kavramları/dosya yolları ile orada ara (`ctx search` varsa onu kullan, yoksa rg);
bütün korpusu asla yükleme. Harness-native hafızalar (ajan/editör gömülü hafızası)
canonical DEĞİLDİR. Alt ajanlara ilgili notlar prompt'a gömülerek verilir —
ajanın gönüllü aramasına güvenilmez.

Hafıza canlı kodun yerine geçmez: `git log` ve `git diff` her ikisinden de otoriterdir.
Oturum sonunda capture kararı ver: kalıcı bulgu varsa nota yazıp `ctx close --not <yol>`,
yoksa `ctx close --yok "<gerekçe>"` — karar vermemek geçersizdir.

<!-- ctx:index:basla -->
### Hafıza haritası — coding_harness · kapsam: current · 10 not

Bu bir **harita**, notun kendisi değil: ilgili satırı görünce **o notu aç**. Hedefli arama `ctx search "<sorgu>"`; bütün korpusu topluca yükleme.
Açık işler `.memory/tasks/OPEN.md` · oturum devamı `.memory/sessions/CURRENT.md`.

**facts/**
- `gorev-sonrasi-inceleme.md` — chat tam ekran TUI veya sade akışta aynı süreçte geçmişi korur; ayrıntılar çevrimdışı HTML…
- `harness-karsilastirmasi.md` — mini-swe-agent, pi, Aider, Claude Code, Codex, OpenCode, Hermes, WrongStack; boyutlar, ortak…

**decisions/**
- `async-cekirdek.md` — Model, araç ve onay beklemeleri async/await ile yazılır; masaüstü "Dur" butonu ve onay…
- `calisma-bicimi.md` — Kullanıcı kod yazmaz; kodu ajan yazar, her parça önce sade benzetmeyle anlatılır, teknik…
- `dil-python.md` — Harness Python ile yazılır; okunabilirlik, ekibin aşinalığı ve ölçüm ekosistemi dağıtım…
- `mimari-katmanlar.md` — cli -> core -> llm|tools -> environments -> messages; çekirdek arayüzden habersiz, kural…
- `model-erisimi.md` — İlk gerçek model ChatGPT planı üzerinden resmi OAuth akışıyla bağlanır; ilk adaptör Responses…
- `urun-ilkeleri.md` — İki dönemlik, masaüstüne uzanan, anlaşılması kolay harness; özellik adları ne yaptığını…

**pitfalls/**
- `ev-klasoru-git-deposu.md` — C:\Users\onur5 commit'siz bir git deposu; "yukarı çıkıp .git ara" mantığı ev klasörünü proje…
- `windows-konsol-turkce.md` — Git Bash üzerinden çalıştırılan Python betiğinin çıktısında ş/ı/ğ bozuk göründü; CLI adımında…
<!-- ctx:index:bitir -->
