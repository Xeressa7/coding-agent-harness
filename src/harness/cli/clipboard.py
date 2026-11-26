"""Sistem panosu: terminalin kendisinden bağımsız okuma ve yazma.

Tam ekran arayüz panoya OSC 52 kaçış dizisiyle yazar; GNOME Terminal (VTE) gibi
terminaller bu diziyi yok sayar, kopyalanan metin sessizce kaybolur. Okumada da
terminal yalnız Ctrl+Shift+V ile gelen metni iletir; Ctrl+V uygulamanın iç
panosunu yapıştırır. Bu modül işletim sisteminin pano araçlarını doğrudan çağırır:
Linux'ta wl-copy/wl-paste (Wayland) ya da xclip (X11, XWayland), macOS'ta
pbcopy/pbpaste, Windows'ta clip/PowerShell. Hiçbiri yoksa sessizce False/None döner;
çağıran taraf kullanıcıya ne yapacağını söyler.
"""

import os
import shutil
import subprocess
import sys

TIMEOUT_SECONDS = 2.0
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")


def _wayland() -> bool:
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def _copy_commands() -> list[list[str]]:
    if sys.platform == "win32":
        return [["clip"]]
    if sys.platform == "darwin":
        return [["pbcopy"]]
    commands = [["wl-copy"]] if _wayland() else []
    return [*commands, ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]


def _paste_commands() -> list[list[str]]:
    if sys.platform == "win32":
        return [["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"]]
    if sys.platform == "darwin":
        return [["pbpaste"]]
    commands = [["wl-paste", "--no-newline", "--type", "text"]] if _wayland() else []
    return [*commands, ["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard"]]


def copy_text(text: str) -> bool:
    """Metni sistem panosuna yazar; yazabilen ilk araç kazanır."""
    for command in _copy_commands():
        if shutil.which(command[0]) is None:
            continue
        data = text.encode("utf-16-le") if command[0] == "clip" else text.encode()
        try:
            # wl-copy/xclip panoyu sahiplenmek için arka planda kalır; çıktısı beklenmez.
            subprocess.run(
                command,
                input=data,
                check=True,
                timeout=TIMEOUT_SECONDS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        return True
    return False


def paste_text() -> str | None:
    """Sistem panosundaki metin; araç yoksa ya da panoda metin yoksa None."""
    for command in _paste_commands():
        if shutil.which(command[0]) is None:
            continue
        try:
            done = subprocess.run(command, capture_output=True, check=True, timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError):
            continue
        return done.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")
    return None


def image_type() -> str | None:
    """Panoda görsel varsa türü (ör. image/png); yoksa ya da bilinemiyorsa None."""
    if sys.platform in {"win32", "darwin"}:
        return None  # bu sistemlerde görsel okuma henüz yok
    listings = [["wl-paste", "--list-types"]] if _wayland() else []
    listings.append(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"])
    for command in listings:
        if shutil.which(command[0]) is None:
            continue
        try:
            done = subprocess.run(command, capture_output=True, check=True, timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError):
            continue
        types = done.stdout.decode(errors="replace").split()
        return next((t for t in IMAGE_TYPES if t in types), None)
    return None


def paste_image() -> bytes | None:
    """Panodaki görselin baytları; panoda görsel yoksa ya da okunamıyorsa None."""
    kind = image_type()
    if kind is None:
        return None
    readers = [["wl-paste", "--no-newline", "--type", kind]] if _wayland() else []
    readers.append(["xclip", "-selection", "clipboard", "-t", kind, "-o"])
    for command in readers:
        if shutil.which(command[0]) is None:
            continue
        try:
            done = subprocess.run(command, capture_output=True, check=True, timeout=TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.stdout:
            return done.stdout
    return None
