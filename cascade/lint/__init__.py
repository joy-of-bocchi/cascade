"""Cascade lint modules."""

from __future__ import annotations

import importlib
from types import ModuleType

__all__ = [
    "carrylint",
    "d2lint",
    "decllint",
    "derivlint",
    "namelint",
    "speclint",
    "structlint",
]

_MODULE_NAMES: frozenset[str] = frozenset(__all__)


def __getattr__(name: str) -> ModuleType:
    if name not in _MODULE_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module: ModuleType = importlib.import_module(f"{__name__}.{name}")
    globals()[name] = module
    return module


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
