"""`harness desktop`'ın pywebview'e dokunan tek yeri.

pywebview isteğe bağlı bir bağımlılıktır (`[project.optional-dependencies] desktop`);
bu modül kurulu olmadan da import edilebilir — `import webview` yalnız fonksiyon
içinde, çağrıldığı an denenir. `cli/app.py`'deki `desktop` komutu arka plan iş
parçacığında gerçek sunucuyu (`_web_app_runner`) başlatır, burada yalnız "kurulu mu",
"sunucu ayakta mı" ve "pencereyi aç" soruları/eylemleri var.
"""

import socket
import time
from typing import Any

MISSING = (
    "pywebview kurulu değil. Kurmak için: `uv sync --extra desktop` "
    "(ya da `uv pip install pywebview`). Şimdilik tarayıcıda açılıyor."
)


def available() -> bool:
    """pywebview import edilebiliyor mu (kurulu mu)."""
    try:
        import webview  # noqa: F401  # pyright: ignore[reportMissingImports,reportUnusedImport]
    except ImportError:
        return False
    return True


def wait_for_port(host: str, port: int, timeout: float) -> bool:
    """Arka planda başlayan sunucu dinlemeye başlayana kadar kısa aralıklarla dener."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def open_window(title: str, url: str) -> None:
    """Pencereyi açar; kullanıcı kapatana kadar bloke eder (pywebview'in kendi döngüsü)."""
    import webview  # pyright: ignore[reportMissingImports]

    module: Any = webview
    module.create_window(title, url)
    module.start()
