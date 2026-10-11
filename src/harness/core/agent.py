"""Döngü yöneticisi: Agent sınıfı (rapor Bölüm 7 ve 11).

Görevi alır ve modeli çağırır. Yanıtta araç çağrısı varsa çağrıyı izin
katmanından geçirip çalıştırır, sonucu mesaj geçmişine ekler ve modeli yeniden
çağırır. Model araç çağırmayı bıraktığında ya da adım/token limiti dolduğunda
durur.

Ekrana yazmaz, kullanıcıdan girdi almaz: her adımda events.py'deki bir olayı
üretir. O olayı ekrana çizmek ön yüzün (cli/) işidir.
"""
