#!/usr/bin/env python3
"""Shared substrate for the D2 toolkit: the typed diagram spec and the model
introspection helpers.

This module is deliberately neutral — it knows nothing about emitting `.d2`
(generation) or about violations (linting). Both `d2gen` (generation) and
`speclint` (linting) import from here, so the two concerns never depend on each
other; they only share this schema.
"""
from __future__ import annotations

from enum import StrEnum
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict
from pydantic.fields import FieldInfo

NONE_TYPE = type(None)


class NodeRole(StrEnum):
    MODEL = "model"
    MINTED = "minted"
    DECISION = "decision"
    TERMINAL = "terminal"


class ModelNode(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    kind: Literal["model"] = "model"
    id: str
    model: type[BaseModel]
    role: NodeRole = NodeRole.MODEL
    group: str | None = None


class DecisionNode(BaseModel):
    kind: Literal["decision"] = "decision"
    id: str
    question: str
    rationale: str = ""
    group: str | None = None


class TerminalNode(BaseModel):
    kind: Literal["terminal"] = "terminal"
    id: str
    label: str
    group: str | None = None


class Edge(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    src: str
    dst: str
    label: str = ""
    payload: type[BaseModel] | None = None
    dashed: bool = False


class Group(BaseModel):
    id: str
    label: str


AnyNode = Annotated[ModelNode | DecisionNode | TerminalNode, "kind"]


class DiagramSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    nodes: list[ModelNode | DecisionNode | TerminalNode]
    edges: list[Edge] = []
    groups: list[Group] = []
    direction: str = "down"


def type_str(annotation: Any) -> str:
    """Render a type annotation the way it reads in source: `float | None`,
    `list[Foo]`, `Literal['a', 'b']`, `dict[str, Bar]`."""
    if annotation is None or annotation is NONE_TYPE:
        return "None"
    origin = get_origin(annotation)
    if origin is None:
        return getattr(annotation, "__name__", str(annotation))
    args = get_args(annotation)
    if origin is Union or origin is UnionType:
        return " | ".join(type_str(arg) for arg in args)
    if origin is Literal:
        return "Literal[" + ", ".join(repr(arg) for arg in args) + "]"
    name = getattr(origin, "__name__", str(origin))
    return f"{name}[{', '.join(type_str(arg) for arg in args)}]"


def field_default(info: FieldInfo) -> str:
    if info.is_required():
        return "required"
    if info.default_factory is not None:
        try:
            return repr(info.default_factory())  # type: ignore[call-arg]
        except Exception:
            return f"{info.default_factory.__name__}()"
    return repr(info.default)


def mentioned_types(model: type[BaseModel]) -> set[type]:
    """Every concrete type the model produces: the model itself plus the
    concrete classes appearing in its field annotations."""
    found: set[type] = {model}

    def walk(annotation: Any) -> None:
        origin = get_origin(annotation)
        if origin is None:
            if isinstance(annotation, type):
                found.add(annotation)
            return
        for arg in get_args(annotation):
            walk(arg)

    for info in model.model_fields.values():
        walk(info.annotation)
    return found
