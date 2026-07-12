#!/usr/bin/env python3
"""Shared substrate for the D2 toolkit: the typed diagram spec and the model
introspection helpers.

This module is deliberately neutral — it knows nothing about emitting `.d2`
(generation) or about violations (linting). Both `d2gen` (generation) and
`speclint` (linting) import from here, so the two concerns never depend on each
other; they only share this schema.
"""

from __future__ import annotations

import dataclasses
from enum import StrEnum
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.fields import FieldInfo

NONE_TYPE = type(None)


def is_entity(obj: Any) -> bool:
    """True for a type the toolkit can introspect: a Pydantic model or a
    dataclass. These are the two structured shapes `entity_fields` understands."""
    if not isinstance(obj, type):
        return False
    if issubclass(obj, BaseModel) and obj is not BaseModel:
        return True
    return dataclasses.is_dataclass(obj)


def is_frozen(entity: type) -> bool:
    """Whether an entity is immutable: a frozen Pydantic config or a
    `@dataclass(frozen=True)`."""
    if issubclass(entity, BaseModel):
        return bool(entity.model_config.get("frozen", False))
    params = getattr(entity, "__dataclass_params__", None)
    return bool(getattr(params, "frozen", False))


class NodeRole(StrEnum):
    MODEL = "model"
    MINTED = "minted"
    DECISION = "decision"
    TERMINAL = "terminal"
    MODULE = "module"


class ModelNode(BaseModel):
    """A payload card: the introspected field table of one entity, optionally
    enriched with an authored role sentence (`prose` — who writes it, who reads
    it, when) and per-field `notes` that fill the table's note column."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    kind: Literal["model"] = "model"
    id: str
    model: type
    role: NodeRole = NodeRole.MODEL
    prose: str = ""
    notes: dict[str, str] = Field(default_factory=dict)
    group: str | None = None

    @field_validator("model")
    @classmethod
    def model_must_be_entity(cls, value: type) -> type:
        if not is_entity(value):
            raise ValueError(
                f"{value!r} is not an introspectable entity "
                "(Pydantic model or dataclass)"
            )
        return value


class DecisionNode(BaseModel):
    """A decision. With a `rationale`, both backends render the DECIDES/WHY text
    inline in the node; without one, the node is a plain short-question diamond."""

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


class ModuleNode(BaseModel):
    """A collapsed neighboring subsystem drawn only as its boundary: a name,
    what it is (`prose`), and the products it hands the rest of the pipeline."""

    kind: Literal["module"] = "module"
    id: str
    label: str
    prose: str = ""
    products: list[str] = Field(default_factory=list)
    group: str | None = None


class Edge(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    src: str
    dst: str
    label: str = ""
    payload: type | None = None
    dashed: bool = False


class Group(BaseModel):
    """A stage container. With a `cadence` ("once per report", "per case") the
    backends render the header as `STAGE: <label> — <cadence>`."""

    id: str
    label: str
    cadence: str = ""


AnyNode = Annotated[ModelNode | DecisionNode | TerminalNode | ModuleNode, "kind"]


class DiagramSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    nodes: list[ModelNode | DecisionNode | TerminalNode | ModuleNode]
    edges: list[Edge] = []
    groups: list[Group] = []
    direction: str = "down"
    # Reachability roots for the connectivity lint (external inputs / the entry).
    # Empty means: every node with edge in-degree zero counts as a root.
    roots: list[str] = Field(default_factory=list)


def type_str(annotation: Any) -> str:
    """Render a type annotation the way it reads in source: `float | None`,
    `list[Foo]`, `Literal['a', 'b']`, `dict[str, Bar]`."""
    if annotation is None or annotation is NONE_TYPE:
        return "None"
    if annotation is Ellipsis:
        return "..."
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


class FieldView(BaseModel):
    """Uniform view of one field on an introspectable entity: its name, resolved
    type annotation, rendered default, optional description, and whether it is a
    computed (derived) field. Lets every consumer iterate Pydantic models and
    dataclasses the same way."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")
    name: str
    annotation: Any
    default: str
    description: str = ""
    computed: bool = False


def _dataclass_default(field: dataclasses.Field) -> str:
    if field.default is not dataclasses.MISSING:
        return repr(field.default)
    if field.default_factory is not dataclasses.MISSING:
        try:
            return repr(field.default_factory())
        except Exception:
            return f"{field.default_factory.__name__}()"
    return "required"


def entity_fields(entity: type) -> list[FieldView]:
    """Field views for a Pydantic model or a dataclass, in declaration order.

    Pydantic computed fields are appended and marked `computed`. Dataclass
    annotations are resolved with `get_type_hints` so stringized annotations
    (`from __future__ import annotations`) and forward references resolve back
    to real classes — without that, referenced entities read as bare strings
    and no relationship edge is detected."""
    if issubclass(entity, BaseModel):
        views: list[FieldView] = [
            FieldView(
                name=name,
                annotation=info.annotation,
                default=field_default(info),
                description=info.description or "",
            )
            for name, info in entity.model_fields.items()
        ]
        for name, info in (getattr(entity, "model_computed_fields", {}) or {}).items():
            views.append(
                FieldView(
                    name=name,
                    annotation=info.return_type,
                    default="computed",
                    description=info.description or "",
                    computed=True,
                )
            )
        return views
    try:
        hints: dict[str, Any] = get_type_hints(entity)
    except Exception:
        hints = {}
    return [
        FieldView(
            name=field.name,
            annotation=hints.get(field.name, field.type),
            default=_dataclass_default(field),
        )
        for field in dataclasses.fields(entity)
    ]


def mentioned_types(entity: type) -> set[type]:
    """Every concrete type the entity produces: the entity itself plus the
    concrete classes appearing in its field annotations."""
    found: set[type] = {entity}

    def walk(annotation: Any) -> None:
        origin = get_origin(annotation)
        if origin is None:
            if isinstance(annotation, type):
                found.add(annotation)
            return
        for arg in get_args(annotation):
            walk(arg)

    for view in entity_fields(entity):
        walk(view.annotation)
    return found
