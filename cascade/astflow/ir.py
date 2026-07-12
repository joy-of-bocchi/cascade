#!/usr/bin/env python3
"""Fact IR: the deterministic intermediate representation between the AST
extractor and the diagram builder.

The extractor reads Python source and emits a `FactIR` — construction sites,
field reads, post-construction mutations, dormancy, and control-flow gates, each
carrying a source citation. The diagram builder turns those facts into a
`DiagramSpec`; the verifier checks that every non-inferred diagram edge maps back
to a fact here. Types (the ER) say what the entities are; this IR says how data
actually flows between them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EdgeKind(StrEnum):
    BUILD_IN = "build_in"  # a part flows into the whole (constructor arg resolves to an entity)
    DERIVED_AFTER = (
        "derived_after"  # a field populated after construction (post-ctor mutation)
    )
    CONSUMED = "consumed"  # an entity's field is read elsewhere
    DORMANT = "dormant"  # a field exists but nothing ever produces it
    GATED_BY = "gated_by"  # construction sits inside a control-flow branch


class Site(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    file: str
    line: int
    function: str = ""


class ArgResolution(BaseModel):
    """One keyword argument of a construction call and what its value resolves to."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    field: str
    expr: str  # the source expression, e.g. "leaf", "items", "r['a']"
    resolves_to: str | None = (
        None  # entity name (bare or wrapped in list/dict/… ), or None if unresolved
    )


class ConstructionFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: Site
    args: list[ArgResolution] = Field(default_factory=list)


class ReadFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: Site
    attr: str


class MutationFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: Site
    via: str  # the method/function that performs the post-construction write
    targets: list[str] = Field(
        default_factory=list
    )  # attribute paths written, e.g. "summary.total"


class EntityFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    defined_at: Site | None = None
    constructed_at: list[ConstructionFact] = Field(default_factory=list)
    returned_by: list[str] = Field(default_factory=list)  # "function@file:line"
    mutated_after: list[MutationFact] = Field(default_factory=list)
    fields_read: list[ReadFact] = Field(default_factory=list)
    serialized_at: list[Site] = Field(default_factory=list)

    @property
    def is_dormant(self) -> bool:
        return not self.constructed_at


class DecisionFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    condition: str  # the branch test as source, e.g. "flag", "is_offcycle"
    site: Site
    gates: list[str] = Field(
        default_factory=list
    )  # entity names whose construction is inside this branch


class FactIR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entities: dict[str, EntityFacts] = Field(default_factory=dict)
    decisions: list[DecisionFact] = Field(default_factory=list)
    unresolved: list[str] = Field(
        default_factory=list
    )  # notes where type resolution went dark (untyped seams)
