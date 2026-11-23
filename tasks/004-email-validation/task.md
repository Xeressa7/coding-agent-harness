`validate.py` dosyasındaki `is_valid_email` fonksiyonu yalnızca `@`
işareti olup olmadığına bakıyor; bu yüzden `a@b` veya `@test.com` gibi
geçersiz adresleri de doğru kabul ediyor. Fonksiyonu, en azından şu
kuralları kontrol edecek şekilde güçlendir:

- Adres tam olarak bir `@` içermeli.
- `@`'den önce ve sonra boş olmayan bir metin olmalı.
- `@`'den sonraki kısımda (alan adında) en az bir `.` bulunmalı.
