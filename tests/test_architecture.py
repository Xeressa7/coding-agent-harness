"""Mimari kurallar: katmanlar arası import yönü.

Kural pyproject.toml'daki [tool.importlinter] bölümünde tanımlı. Biri yanlış
yönde import eklerse (örneğin core'dan cli'ye) bu test kırmızıya döner.
"""

from importlinter.cli import lint_imports


def test_layer_contracts_hold() -> None:
    assert lint_imports(no_logo=True) == 0
