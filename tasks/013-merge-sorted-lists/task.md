`merge.py` dosyasındaki `merge_sorted` fonksiyonu iki sıralı listeyi
birleştirmeli ama karşılaştırma ikinci liste (`b`) lehine çıktığında bile
yine birinci listeden (`a`) eleman alıyor; `b` listesinden hiç eleman
almıyor. Bu yüzden sonuç doğru sıralı birleşmiyor. Fonksiyonu düzelt.
