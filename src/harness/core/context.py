"""Çıktı sınırı: uzun araç sonucunun ortasını keser (anatomi katmanı `output_limit`).

Sonuç geçmişe EKLENİRKEN bir kez kesilir; kesildiyse ajan `HarnessIntervened`
yazar ve aracın tam çıktısını `ToolFinished.full_content`'te saklar. Böylece
modelin gördüğü (kayıttaki `result`) ile aracın döndürdüğü ayrı ayrı görünür.

Sınır bilerek geniştir: read_file kendi bütçesiyle (satır/karakter) sonucunu
zaten bu sınırın altında tutar; kesme ancak dev bash çıktısı gibi durumlarda
çalışır. Önceki sürüm her sonucu 2.000 + 2.000 karaktere sessizce kesiyordu ve
modeli aynı dosyayı çakışan aralıklarla tekrar okumaya itiyordu.
"""

OUTPUT_LIMIT_CHARS = 40_000


def grouped(number: int) -> str:
    return f"{number:,}".replace(",", ".")


def limit_output(text: str, limit: int = OUTPUT_LIMIT_CHARS) -> str | None:
    """Sınırı aşan metnin baş ve sonunu tutar, ortayı atar; sığıyorsa None.

    Modele kesildiği ve ne kadar atıldığı söylenir, tahmin ettirilmez.
    """
    if len(text) <= limit:
        return None
    head = text[: limit // 2]
    tail = text[-(limit // 2) :]
    omitted = len(text) - len(head) - len(tail)
    note = (
        f"[harness: çıktı {grouped(len(text))} karakterdi; ortadaki {grouped(omitted)} karakter "
        "atıldı. Gerekirse daha dar bir aralık ya da filtreyle yeniden iste.]"
    )
    return f"{head}\n\n{note}\n\n{tail}"
