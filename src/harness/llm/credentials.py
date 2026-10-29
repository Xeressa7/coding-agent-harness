"""Kayıtlı API anahtarları: sohbette `/connect` ile bir kez girilen anahtar diskte durur.

Anahtar ortam değişkeninin adıyla saklanır (`OPENCODE_API_KEY` gibi); bu yüzden
aynı anahtarı kullanan sağlayıcılar (OpenCode Zen ve Go) bir kez bağlanır. Ortam
değişkeni tanımlıysa o kazanır: geçici bir deneme dosyaya dokunmadan başka anahtar
kullanabilir. Dosya yalnız sahibinin okuyacağı izinle yazılır; değer hiçbir yerde
gösterilmez.
"""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, cast

AUTH_PATH = Path.home() / ".harness" / "auth.json"

KeySource = Literal["env", "file"]


def load_keys(path: Path | None = None) -> dict[str, str]:
    """Kayıtlı anahtarlar; dosya yoksa ya da okunamıyorsa boş."""
    try:
        raw: Any = json.loads((path or AUTH_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    keys = cast(dict[str, Any], raw)
    return {name: value for name, value in keys.items() if isinstance(value, str) and value}


def save_key(name: str, value: str, path: Path | None = None) -> None:
    _write({**load_keys(path), name: value}, path or AUTH_PATH)


def remove_key(name: str, path: Path | None = None) -> bool:
    """Kayıtlı anahtarı siler; silinecek bir şey varsa True."""
    keys = load_keys(path)
    if keys.pop(name, None) is None:
        return False
    _write(keys, path or AUTH_PATH)
    return True


def environment(path: Path | None = None) -> dict[str, str]:
    """Sağlayıcıların okuduğu ortam: kayıtlı anahtarlar, üstünde gerçek ortam değişkenleri."""
    return {**load_keys(path), **os.environ}


def key_source(
    name: str, env: Mapping[str, str] | None = None, path: Path | None = None
) -> KeySource | None:
    """Anahtar nereden geliyor: ortam değişkeni, kayıtlı dosya ya da hiçbir yerden (None)."""
    if (os.environ if env is None else env).get(name):
        return "env"
    return "file" if name in load_keys(path) else None


def _write(keys: dict[str, str], path: Path) -> None:
    # Yarım yazılmış dosya kalmasın: önce geçici dosya, sonra yerine konur.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(keys, indent=2), encoding="utf-8")
    temporary.chmod(0o600)  # Unix'te yalnız sahibi okur; Windows'ta etkisi sınırlı.
    os.replace(temporary, path)
