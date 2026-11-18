"""Kontrol noktası: her turdan önce çalışma dizininin anlık görüntüsü; `/undo` son turu geri alır.

Görüntü projenin kendi git deposuna yazılmaz; `~/.harness/checkpoints` altında ayrı
(gölge) bir depoya yazılır. Kullanıcının dalı, indeksi ve geçmişi değişmez, proje git deposu
olmasa da çalışır. Projenin `.gitignore` kuralları geçerlidir: yok sayılan dosyalar
(node_modules, derleme çıktısı) görüntüye girmez, geri de alınmaz. Görüntü araçtan değil
diskten alındığı için bash ile yapılan değişiklikler de yakalanır.

Sınır: geri alma, turun başından bu yana diskteki bütün değişiklikleri geri alır; tur
bittikten sonra kullanıcının elle yaptığı değişiklikler de buna dahildir.
"""

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

CHECKPOINTS_HOME = Path.home() / ".harness" / "checkpoints"
GIT_TIMEOUT_SECONDS = 60  # büyük ve .gitignore'suz klasörde ilk görüntü uzun sürebilir
# Görüntü baytı baytına aynı kalsın; Türkçe dosya adları kaçışsız gelsin; bakım çalışmasın.
_GIT_CONFIG = (
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.safecrlf=false",
    "-c",
    "gc.auto=0",
    "-c",
    "core.quotepath=false",
    "-c",
    "core.longpaths=true",
)


class CheckpointUnavailable(Exception):
    """git yok, zaman aşımı ya da git hatası: kontrol noktası bu oturumda kullanılamaz."""


@dataclass(frozen=True)
class Restored:
    label: str  # geri alınan turun isteği (kısaltılmış)
    rewritten: tuple[str, ...]  # tur öncesi içeriği geri yazılan dosyalar
    removed: tuple[str, ...]  # turda yaratılıp silinen dosyalar

    def summary(self) -> str:
        parts: list[str] = []
        if self.rewritten:
            parts.append(f"{len(self.rewritten)} dosya eski hâline döndü")
        if self.removed:
            parts.append(f"{len(self.removed)} yeni dosya silindi")
        return ", ".join(parts)


@dataclass(frozen=True)
class _Point:
    tree: str
    label: str


class Checkpoints:
    """Tur öncesi görüntülerin yığını: `save` turdan önce, `undo` son değişen turu geri alır."""

    def __init__(self, root: Path, home: Path | None = None) -> None:
        self.root = root.resolve()
        key = hashlib.sha256(str(self.root).lower().encode()).hexdigest()[:16]
        self._git_dir = (home or CHECKPOINTS_HOME) / key
        self._points: list[_Point] = []
        self.broken: str | None = None  # ilk hatadan sonra bu oturumda bir daha denenmez

    def __len__(self) -> int:
        return len(self._points)

    def save(self, label: str) -> str:
        """Şu anki hâli yığına koyar; görüntünün ağaç kimliğini döner."""
        try:
            tree = self._snapshot()
        except CheckpointUnavailable as exc:
            self.broken = str(exc)
            raise
        self._points.append(_Point(tree, _short(label)))
        return tree

    def undo(self) -> Restored | None:
        """Dosya değiştiren son turu geri alır; değişiklik yapan tur yoksa None.

        Dosyaya dokunmayan turlar atlanır: `/undo` her zaman bir değişikliği geri alır.
        """
        now = self._snapshot()
        while self._points:
            point = self._points.pop()
            changes = self._diff(point.tree, now)
            if not changes:
                continue
            removed = [path for status, path in changes if status == "A"]
            rewritten = [path for status, path in changes if status != "A"]
            for path in removed:
                (self.root / path).unlink(missing_ok=True)
                _prune_empty_dirs(self.root, (self.root / path).parent)
            if rewritten:
                self._git(
                    "checkout",
                    point.tree,
                    "--pathspec-from-file=-",
                    "--pathspec-file-nul",
                    stdin="\0".join(rewritten).encode(),
                )
            self._snapshot()  # gölge indeks geri alınmış hâli bilsin
            return Restored(point.label, tuple(rewritten), tuple(removed))
        return None

    def _snapshot(self) -> str:
        if not (self._git_dir / "HEAD").is_file():
            self._git_dir.mkdir(parents=True, exist_ok=True)
            self._git("init", "--quiet")
        self._git("add", "--all", "--", ".")
        return self._git("write-tree").strip()

    def _diff(self, before: str, after: str) -> list[tuple[str, str]]:
        out = self._git("diff-tree", "-r", "--name-status", "--no-renames", "-z", before, after)
        fields = out.split("\0")
        return [(fields[i][0], fields[i + 1]) for i in range(0, len(fields) - 1, 2) if fields[i]]

    def _git(self, *args: str, stdin: bytes | None = None) -> str:
        env = {**os.environ, "GIT_DIR": str(self._git_dir), "GIT_WORK_TREE": str(self.root)}
        try:
            done = subprocess.run(
                ["git", *_GIT_CONFIG, *args],
                cwd=self.root,
                env=env,
                input=stdin,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError as e:
            raise CheckpointUnavailable("git bulunamadı; geri alma için git kurulu olmalı.") from e
        except subprocess.TimeoutExpired as e:
            raise CheckpointUnavailable(
                f"görüntü {GIT_TIMEOUT_SECONDS} sn'de alınamadı (klasör çok büyük olabilir; "
                ".gitignore ile büyük klasörleri dışarıda bırak)."
            ) from e
        if done.returncode != 0:
            error = done.stderr.decode("utf-8", "replace").strip().splitlines()
            raise CheckpointUnavailable(f"git {args[0]}: {error[-1] if error else 'hata'}")
        return done.stdout.decode("utf-8", "replace")


def _short(text: str, limit: int = 60) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _prune_empty_dirs(root: Path, folder: Path) -> None:
    """Turda yaratılan dosya silinince geride boş kalan klasörler de gider (kök hariç)."""
    while folder != root and root in folder.parents:
        try:
            folder.rmdir()  # yalnız boşsa siler
        except OSError:
            return
        folder = folder.parent
