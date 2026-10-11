"""İzin katmanı: PermissionManager (rapor Bölüm 16).

Her araç çağrısı çalışmadan önce buradan geçer ve üç karardan birini alır:
izin ver, kullanıcıya sor, reddet. Çalışma dizini dışına çıkan yollar
reddedilir. "Sor" kararında soruyu bu modül değil ön yüz sorar.
"""
