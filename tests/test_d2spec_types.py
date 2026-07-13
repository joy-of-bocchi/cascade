from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from cascade.spec.d2spec import type_str


class FuncMetadata:
    def __init__(self, func: Callable[[int], int]) -> None:
        self.func: Callable[[int], int] = func


class AddressMetadata:
    def __repr__(self) -> str:
        return "<AddressMetadata object at 0xABCDEF>"


def normalize(value: int) -> int:
    return value


def test_type_str_renders_annotated_callable_metadata() -> None:
    rendered: str = type_str(Annotated[int, FuncMetadata(normalize)])

    assert rendered == "Annotated[int, FuncMetadata(normalize)]"


def test_type_str_scrubs_memory_addresses_from_annotated_metadata() -> None:
    rendered: str = type_str(Annotated[str, AddressMetadata()])

    assert rendered == "Annotated[str, <AddressMetadata object>]"
