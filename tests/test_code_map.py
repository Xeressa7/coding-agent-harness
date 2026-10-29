"""İçe aktarma grafiği (haritadaki "içe aktarmalar" katmanı) ve tek kayıtla `harness watch`."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from harness.cli import app as cli
from harness.cli.code_map import import_graph
from harness.cli.watch import WatchServer
from harness.core.events import TaskStarted
from harness.core.session_log import SessionLogger

MAP_JS = Path(__file__).resolve().parent.parent / "src" / "harness" / "cli" / "watch_map.js"


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sample_project(root: Path) -> None:
    write(root, "src/pkg/__init__.py", "")
    write(root, "src/pkg/core/__init__.py", "")
    write(root, "src/pkg/core/agent.py", "import json\nfrom pkg.core import events\n")
    write(root, "src/pkg/core/events.py", "from . import util\nfrom ..cli.app import main\n")
    write(root, "src/pkg/core/util.py", "")
    write(root, "src/pkg/cli/__init__.py", "")
    write(
        root, "src/pkg/cli/app.py", "import pkg.core.agent as agent\nfrom pkg.core.util import x\n"
    )
    write(root, "src/pkg/broken.py", "def (:\n")  # ayrıştırılamayan dosya atlanır
    write(root, "web/main.ts", "import { a } from './lib/a';\nconst b = require('../web/b.js');\n")
    write(root, "web/lib/a.ts", "import x from 'react';\nexport * from './index';\n")
    write(root, "web/lib/index.ts", "")
    write(root, "web/b.js", "")
    write(root, ".venv/lib/x.py", "import pkg\n")  # araç klasörü taranmaz


def test_import_graph_resolves_src_layout_relative_and_js_paths(tmp_path: Path) -> None:
    sample_project(tmp_path)
    graph = import_graph(tmp_path)
    assert graph.complete and graph.files == 12
    assert graph.edges == [
        ("src/pkg/cli/app.py", "src/pkg/core/agent.py"),
        ("src/pkg/cli/app.py", "src/pkg/core/util.py"),  # `from m import ad`: modül m
        ("src/pkg/core/agent.py", "src/pkg/core/events.py"),  # `from paket import modül`
        ("src/pkg/core/events.py", "src/pkg/cli/app.py"),  # `..cli.app`
        ("src/pkg/core/events.py", "src/pkg/core/util.py"),  # `. import util`
        ("web/lib/a.ts", "web/lib/index.ts"),
        ("web/main.ts", "web/b.js"),
        ("web/main.ts", "web/lib/a.ts"),  # uzantısız göreli yol
    ]


def test_imports_endpoint_reads_the_folder_of_the_log(tmp_path: Path) -> None:
    sample_project(tmp_path / "proje")
    log = SessionLogger(tmp_path / "kayit" / "k.jsonl")
    log.write(TaskStarted(task="t", root=str(tmp_path / "proje")))
    server = WatchServer(tmp_path / "kayit")
    try:
        body = httpx.get(server.start() + "api/imports?path=k.jsonl").json()
        assert body["files"] == 12 and ["web/main.ts", "web/b.js"] in body["edges"]
        assert httpx.get(server.start() + "api/imports?path=yok.jsonl").status_code == 404
    finally:
        server.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")
def test_map_counts_file_jumps_that_follow_an_import() -> None:
    edges = [["src/a.py", "src/b.py"], ["src/c.py", "src/a.py"]]
    visits = [
        {"node": "file:src/a.py"},
        {"node": "search:3"},  # arama araya girer ama geçişi bölmez
        {"node": "file:src/a.py"},  # aynı dosya: geçiş sayılmaz
        {"node": "file:src/b.py"},  # a -> b: içe aktarma
        {"node": "file:src/d.py"},  # b -> d: bağlantı yok
        {"node": "file:src/c.py"},  # d -> c: yok
        {"node": "file:src/a.py"},  # c -> a: ters yönde de sayılır
    ]
    script = (
        "globalThis.window = globalThis;"
        f"require({json.dumps(str(MAP_JS))});"
        f"const g = window.HarnessMap.importIndex({json.dumps(edges)});"
        f"const jumps = window.HarnessMap.fileJumps({json.dumps(visits)});"
        "process.stdout.write(JSON.stringify(jumps.map(([a, b]) => g.linked(a.node, b.node))));"
    )
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [True, False, False, True]


def test_watch_with_a_single_log_opens_it_on_the_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = SessionLogger(tmp_path / "kayitlar" / "k.jsonl")
    log.write(TaskStarted(task="t"))
    opened: list[str] = []

    def stop_waiting() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "open_in_browser", opened.append)
    monkeypatch.setattr(
        cli, "threading", SimpleNamespace(Event=lambda: SimpleNamespace(wait=stop_waiting))
    )
    result = CliRunner().invoke(cli.app, ["watch", str(log.path)], env={"COLUMNS": "300"})
    assert result.exit_code == 0, result.output
    assert opened and opened[0].endswith("/?path=k.jsonl&view=map")

    (tmp_path / "not.txt").write_text("", encoding="utf-8")
    refused = CliRunner().invoke(cli.app, ["watch", str(tmp_path / "not.txt")])
    assert refused.exit_code != 0
