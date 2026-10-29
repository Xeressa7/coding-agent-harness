"""Canlı panel: oturum kayıtlarını (JSONL) tarayıcıda akarken gösteren yerel sunucu.

Ana sohbet TUI'de ya da sade akışta yürür; panel yalnız kaydı okur. Ön yüzler her
olayı (akan metin parçaları dahil) anında kayda yazdığı için hiçbir şey kaybolmaz:
sayfa açıldığında kaydı baştan oynatır, sonra yeni satırları izler. Bağlantı koparsa
tarayıcı kaldığı satırdan devam eder (SSE `Last-Event-ID` = satır numarası).

Çekirdeğe ve ön yüze dokunmaz, salt-okurdur (onaylar TUI'de verilir). Kendi iş
parçacığında çalışır: ön yüz kullanıcıyı beklerken de akış sürer. Yalnız
127.0.0.1'e bağlanır ve yalnız verilen klasörün altındaki .jsonl dosyalarını açar.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from harness.tools.search import SKIPPED_DIRS

_POLL_SECONDS = 0.25
_HEARTBEAT_SECONDS = 10.0
_TREE_LIMIT = 3000
_STATIC = {"/map.js": ("watch_map.js", "text/javascript; charset=utf-8")}


def project_tree(log: Path, limit: int = _TREE_LIMIT) -> dict[str, Any]:
    """Kaydın çalıştığı klasörün bugünkü ağacı (yalnız adlar; içerik okunmaz).

    Harita ajanın dokunmadığı dosyaları da soluk gösterir. Klasör artık yoksa (ör.
    deneyin geçici kopyası) ağaç boş döner; harita kayıttaki yollarla yetinir.
    """
    root: str | None = None
    try:
        with log.open(encoding="utf-8") as f:
            for line in f:
                event = json.loads(line)
                if event.get("kind") == "task_started" and event.get("root"):
                    root = str(event["root"])
                    break
    except (OSError, ValueError):
        pass
    if root is None or not Path(root).is_dir():
        return {
            "root": root,
            "name": Path(root).name if root else "",
            "entries": [],
            "complete": False,
        }
    base = Path(root)
    entries: list[str] = []
    complete = True
    for folder, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS and not d.startswith("."))
        relative = Path(folder).relative_to(base)
        prefix = "" if relative == Path(".") else relative.as_posix() + "/"
        entries += [f"{prefix}{d}/" for d in dirs]
        entries += [f"{prefix}{n}" for n in sorted(names)]
        if len(entries) >= limit:
            complete = False
            break
    return {"root": root, "name": base.name, "entries": entries[:limit], "complete": complete}


def list_logs(folder: Path) -> list[dict[str, Any]]:
    """Klasör altındaki kayıtlar, en yenisi önce; ilk görev metni listede ad olarak görünür."""
    found: list[dict[str, Any]] = []
    for path in folder.rglob("*.jsonl"):
        if path.name == "results.jsonl":  # deney özeti, oturum kaydı değil
            continue
        try:
            stat = path.stat()
            with path.open(encoding="utf-8") as f:
                first = json.loads(f.readline() or "{}")
        except (OSError, ValueError):
            continue
        found.append(
            {
                "path": path.relative_to(folder).as_posix(),
                "modified": stat.st_mtime,
                "size": stat.st_size,
                "task": str(first.get("task") or "")[:120],
                "model": first.get("model"),
            }
        )
    return sorted(found, key=lambda item: item["modified"], reverse=True)


class WatchServer:
    """`start()` adresi döner; sunucu arka plan iş parçacığında, `close()` ile kapanır."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder.resolve()
        self._server: ThreadingHTTPServer | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self, port: int = 0) -> str:
        if self._server is None:
            self.folder.mkdir(parents=True, exist_ok=True)
            server = ThreadingHTTPServer(("127.0.0.1", port), _handler(self.folder))
            server.daemon_threads = True
            threading.Thread(target=server.serve_forever, name="harness-watch", daemon=True).start()
            self._server = server
        return f"http://127.0.0.1:{self._server.server_address[1]}/"

    def url_for(self, log: Path | None) -> str:
        """Sayfa adresi; kayıt verilirse o kayıt açılır, verilmezse en yenisi izlenir."""
        base = self.start()
        if log is None:
            return base
        try:
            relative = log.resolve().relative_to(self.folder).as_posix()
        except ValueError:
            return base
        return f"{base}?path={quote(relative)}"

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def _handler(folder: Path) -> type[BaseHTTPRequestHandler]:
    page = files("harness.cli").joinpath("watch.html").read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass  # istek satırları sohbet ekranına basılmasın

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = parse_qs(url.query)
            match url.path:
                case "/":
                    self._send(200, "text/html; charset=utf-8", page.encode())
                case "/api/sessions":
                    body = json.dumps(list_logs(folder), ensure_ascii=False).encode()
                    self._send(200, "application/json; charset=utf-8", body)
                case "/api/tree":
                    log = _inside(folder, (query.get("path") or [""])[0])
                    if log is None:
                        self._send(404, "text/plain; charset=utf-8", "kayıt yok".encode())
                        return
                    body = json.dumps(project_tree(log), ensure_ascii=False).encode()
                    self._send(200, "application/json; charset=utf-8", body)
                case path if path in _STATIC:
                    name, content_type = _STATIC[path]
                    body = files("harness.cli").joinpath(name).read_text(encoding="utf-8")
                    self._send(200, content_type, body.encode())
                case "/api/stream":
                    log = _inside(folder, (query.get("path") or [""])[0])
                    if log is None:
                        self._send(404, "text/plain; charset=utf-8", "kayıt yok".encode())
                        return
                    after = self.headers.get("Last-Event-ID") or (query.get("after") or ["0"])[0]
                    self._stream(log, int(after) if after.isdigit() else 0)
                case _:
                    self._send(404, "text/plain; charset=utf-8", b"bulunamadi")

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _stream(self, log: Path, after: int) -> None:
            """Kaydın `after`. satırından sonrasını gönderir, sonra yeni satırları bekler."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            line_number = 0
            last_beat = time.monotonic()
            try:
                with log.open(encoding="utf-8") as f:
                    while True:
                        position = f.tell()
                        line = f.readline()
                        if line.endswith("\n"):
                            line_number += 1
                            if line_number > after and line.strip():
                                message = f"id: {line_number}\ndata: {line.strip()}\n\n"
                                self.wfile.write(message.encode())
                                self.wfile.flush()
                            continue
                        f.seek(position)  # yarım satır: yazan bitirene kadar bekle
                        if time.monotonic() - last_beat > _HEARTBEAT_SECONDS:
                            self.wfile.write(b": canli\n\n")
                            self.wfile.flush()
                            last_beat = time.monotonic()
                        time.sleep(_POLL_SECONDS)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return  # sekme kapandı

    return Handler


def _inside(folder: Path, relative: str) -> Path | None:
    """Klasörün dışına çıkan ya da .jsonl olmayan yol reddedilir."""
    if not relative:
        return None
    path = (folder / relative).resolve()
    if path.suffix != ".jsonl" or not path.is_file() or not path.is_relative_to(folder):
        return None
    return path
