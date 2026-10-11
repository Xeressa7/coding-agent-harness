---
name: ctx-ara
description: Göreve başlamadan kanonik hafızada (Markdown + Git) kavram ve dosya yoluyla ara; isabet eden 1-3 notu aç. Bütün korpusu asla topluca yükleme.
---

# ctx-ara — hafızada ara

Kanonik hafıza bu reponun `context.yaml → memory_path` adresindedir; bu repo
için retrieval filtresi `AGENTS.md` router bloğunda yazar. Harness-native
hafızalar (ajan/editör gömülü hafızası) kanonik DEĞİLDİR.

## Ne zaman

- Göreve başlarken: görev kavramları + ilgili dosya yollarıyla ara.
- `ctx index` haritasında ilgili satır görünce: o notu aç.

## Nasıl

```sh
ctx search "<sorgu>"              # kapsam filtresi uygulanır
ctx search "<sorgu>" --all        # arşiv dahil, filtresiz
ctx search "<sorgu>" --top 3      # ilk N sonuç
```

- Sorgu Türkçe karakterli de aksansız da olabilir (`şablon`/`sablon` aynı
  anahtara düşer); çekim eki katlanmaz (`izolasyonu` ≠ `izolasyon`), iki
  yazımı da dene.
- Sonuç satırı `yol · kapsam · [sebep] · açıklama` verir; sebepler
  (`yol/kod/başlık/etiket/açıklama/gövde`) neden isabet ettiğini söyler.
- İsabet eden **1-3 notu aç**, gerisini açma. Bütün korpusu topluca yükleme.
- Alt ajanlara ilgili notları prompt'a gömerek ver; ajanın gönüllü aramasına
  güvenme.

## Yasaklar

- Notla kod çelişiyorsa **kod kazanır** (`git log`/`git diff` otoriterdir).
- Eşleşme yoksa uydurma: `(eslesme yok)` geçerli bir cevaptır.
