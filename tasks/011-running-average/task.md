`stats.py` dosyasındaki `running_averages` fonksiyonu her adımda o ana
kadarki elemanların ortalamasını (yani ilk eleman için ilk 1 elemanın,
ikinci eleman için ilk 2 elemanın ortalamasını, ...) hesaplamalı ama her
adımda listenin TÜM uzunluğuna bölüyor; bu yüzden ilk elemanlar için
olduğundan küçük değerler veriyor. Fonksiyonu düzelt.
