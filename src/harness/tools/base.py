"""Tool arayüzü ve ToolRegistry.

Her aracın adı, açıklaması, girdi şeması (Pydantic modelinden üretilen JSON
Schema) ve çalıştırma fonksiyonu vardır. Kayıt defteri araçları tutar,
şemalarını modele sunar ve çağrıyı doğru araca yönlendirir. Araç hatası
istisna olarak fırlamaz; hata mesajı araç sonucu olarak modele döner.
"""
