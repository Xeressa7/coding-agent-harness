"""Paketteki her modül hatasız import edilebiliyor mu?"""

import importlib
import pkgutil

import harness


def test_every_module_imports() -> None:
    for module in pkgutil.walk_packages(harness.__path__, prefix="harness."):
        importlib.import_module(module.name)
