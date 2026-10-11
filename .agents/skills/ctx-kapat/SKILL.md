---
name: ctx-kapat
description: Oturumu capture kararıyla kapat. Kalıcı bulgu varsa nota yazıp `ctx close --not`, yoksa gerekçeyle `ctx close --yok`. Karar vermemek geçersizdir.
---

# ctx-kapat — oturumu kapat

Kapanışta not yazmayı hatırlamak disiplindir ve güvenilmez; bu skill
mekanizmayı işletir. Yargı senin, disiplin aracın.

## Akış

1. Bu oturumda kalıcı bulgu var mı? (`ctx-yaz` testini uygula.)
2. Varsa: önce notu yaz (uygun klasöre), sonra:
   ```sh
   ctx close --not <hafızaya-göreli-yol> [--not ...]
   ```
   Yeni not haritayı bayatlatır; `close` tüm tüketicilerin `ctx:index`
   bloklarını tazeler.
3. Yoksa:
   ```sh
   ctx close --yok "<neden durable bulgu yok>"
   ```
   "Durable bulgu yok" GEÇERLİ bir karardır; karar VERMEMEK geçersizdir.
4. `sessions/CURRENT.md`'yi güncelle — yalnız son iki tur, beş başlık:
   **ne değişti · nasıl doğrulandı · neyi test etmedim · doğrulanmamış
   varsayımlar · durum** (commit+push'lu / çalışma ağacı / canlı test edilmedi).
5. Önceki oturumdan `sessions/.pending/` altında ham döküm kaldıysa: bulguyu
   nota taşı, sonra dökümü SİL (gitignore'lu, commitlenmez).

## Kontrol

```sh
ctx close --kontrol   # önceki oturum kararlı kapandı mı? (kancalar da bunu koşar)
ctx validate          # kapanış kapısı; hata varsa iş kapalı sayılmaz
```

Talimatsız commit/push YOK — diff gösterilir, karar kullanıcınındır.
