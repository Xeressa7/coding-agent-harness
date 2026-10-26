"""Seviye 0: araçsız model dosyaları cevabında verir, harness kod bloklarını diske yazar."""

from pathlib import Path

import pytest

from harness.cli.app import load_system_prompt
from harness.core.agent import Agent
from harness.core.anatomy import get_preset
from harness.core.code_blocks import CodeBlock, extract_code_blocks
from harness.core.events import CodeBlocksSaved, ModelReplied, TaskFinished
from harness.llm.scripted import ScriptedLLM
from harness.messages import AssistantMessage

pytestmark = pytest.mark.anyio

ANSWER = """Gemi sahnesi hazır.

```html index.html
<canvas id="sahne"></canvas>
<script src="src/sahne.js"></script>
```

```js src/sahne.js
const yelken = "turuncu";
```

Çalıştırmak için örnek:

```bash
npx serve .
```

```style.css
body { margin: 0; }
```
"""


def test_named_blocks_become_files_and_unnamed_ones_are_counted() -> None:
    blocks, unnamed = extract_code_blocks(ANSWER)

    assert blocks == [
        CodeBlock(
            path="index.html",
            content='<canvas id="sahne"></canvas>\n<script src="src/sahne.js"></script>\n',
        ),
        CodeBlock(path="src/sahne.js", content='const yelken = "turuncu";\n'),
        CodeBlock(path="style.css", content="body { margin: 0; }\n"),
    ]
    assert unnamed == 1


def test_text_without_blocks_gives_nothing() -> None:
    assert extract_code_blocks("Sadece açıklama.") == ([], 0)


async def run(anatomy_name: str, text: str, root: Path) -> list[object]:
    anatomy = get_preset(anatomy_name)
    model = ScriptedLLM([AssistantMessage(text=text)])
    agent = Agent(model, anatomy.tool_registry(root), "talimat", anatomy=anatomy, root=root)
    return [event async for event in agent.run("gemi yap")]


async def test_raw_level_saves_the_answer_files_and_reports_them(tmp_path: Path) -> None:
    text = ANSWER + "\n```txt ../dışarı.txt\nkaçak\n```\n"

    events = await run("raw", text, tmp_path)

    kinds = [type(e) for e in events]
    assert kinds[-3:] == [ModelReplied, CodeBlocksSaved, TaskFinished]
    saved = events[-2]
    assert isinstance(saved, CodeBlocksSaved)
    assert saved.saved == ("index.html", "src/sahne.js", "style.css")
    assert saved.rejected == ("../dışarı.txt",)
    assert saved.unnamed == 1
    assert (tmp_path / "src" / "sahne.js").read_text("utf-8") == 'const yelken = "turuncu";\n'
    assert not (tmp_path.parent / "dışarı.txt").exists()


async def test_agentic_levels_do_not_write_blocks_from_the_answer(tmp_path: Path) -> None:
    events = await run("files", ANSWER, tmp_path)

    assert not any(isinstance(e, CodeBlocksSaved) for e in events)
    assert not (tmp_path / "index.html").exists()


def test_raw_level_gets_its_own_prompt_with_the_file_convention() -> None:
    raw = load_system_prompt(get_preset("raw"))
    agentic = load_system_prompt(get_preset("files"))

    assert "araç kullanamazsın" in raw.lower() and "```html index.html" in raw
    assert raw != agentic and "<!--" not in raw
