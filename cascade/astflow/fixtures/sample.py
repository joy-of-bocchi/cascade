#!/usr/bin/env python3
"""Self-contained fixture mirroring the cross-case build pattern, for testing the
AST dataflow extractor without any Django/domain dependency.

Shapes the extractor must recover:
  - Leaf  -> Mid          (build-in: Mid(key=leaf))
  - Mid   -> Whole        (build-in: Whole(items=[...Mid...]))
  - Whole -> Summary      (derived-after: compute() mutates self.summary post-build)
  - Summary consumed      (consume() reads w.summary.total)
  - Dormant               (never constructed anywhere -> dormant)
  - decision `if flag:`   (gates construction of Leaf/Mid)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Leaf(BaseModel):
    a: int
    b: str


class Mid(BaseModel):
    key: Leaf
    weight: float


class Summary(BaseModel):
    total: int = 0


class Dormant(BaseModel):
    note: str = ""


class Whole(BaseModel):
    items: list[Mid] = Field(default_factory=list)
    summary: Summary = Field(default_factory=Summary)
    leftovers: list[Dormant] = Field(default_factory=list)

    def compute(self) -> None:
        self.summary.total = len(self.items)


def build(rows: list[dict[str, Any]], flag: bool) -> Whole:
    items: list[Mid] = []
    if flag:
        for row in rows:
            leaf = Leaf(a=row["a"], b=row["b"])
            items.append(Mid(key=leaf, weight=row["w"]))
    whole = Whole(items=items)
    whole.compute()
    return whole


def consume(w: Whole) -> int:
    return w.summary.total
