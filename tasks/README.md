# Değerlendirme görevleri (rapor Bölüm 20)

Her alt klasör bir görevdir:

```
<id>/
  task.md     # agent'a verilecek görev metni
  repo/       # başlangıç durumu (agent yalnız bunu görür)
  tests/      # gizli testler — agent görmez; çalışma bitince repo
              # kopyasının üzerine eklenip pytest ile koşulur
```

Koşu, `src/harness/core/evaluation.py` içindeki `run_task`/`run_all`
tarafından yürütülür: `repo/` geçici bir dizine kopyalanır, agent orada
çalışır, bitince `tests/` aynı dizine eklenip `pytest` koşulur. Asıl
`repo/` ve `tests/` klasörleri değişmez.

Yeni görev eklerken:

- `repo/` küçük ve kendi kendine yeten olsun (dış bağımlılık gerektirmesin).
- `tests/`deki testler yalnız `repo/`nin kök dizinindeki modülleri import
  etsin (ör. `from calc import divide`) — testler koşulurken o dizin
  çalışma dizinidir.
- `task.md` görevi doğal dilde, modele ne yapması gerektiğini net biçimde
  anlatır; testlerin ne kontrol ettiğini açık etmez.
