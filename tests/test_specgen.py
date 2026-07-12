from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field

from cascade.spec import ModelNode, TerminalNode
from cascade.spec import (
    AuthoredEdge,
    AuthoredExtra,
    Overlay,
    SpecgenLintError,
    TypeOverlay,
    fragment_from_pipeline,
    merge,
)


class ToyModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)


class InputRecord(ToyModel):
    value: int = Field(description="Input quantity.")


class PreparedRecord(ToyModel):
    """Prepared data available to later stages.

    Extra paragraphs are ignored by the node prose pickup.
    """

    value: int


class SummaryRecord(ToyModel):
    value: int


class AuditMarker(ToyModel):
    value: int


class RoutedRecord(ToyModel):
    value: int


class FinalRecord(ToyModel):
    value: int


class NestedRecord(ToyModel):
    value: int


class ToyStage(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    name: str
    input_types: tuple[type, ...] = ()
    output_type: type
    when: Callable[..., bool] | None = None
    section: str | None = None
    collapse: bool = False
    marker: bool = False
    question: str | None = None
    reads_external: str | None = None
    sub_pipeline: ToyPipeline | None = None


class ToyPipeline(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    root_types: tuple[type, ...]
    stages: tuple[ToyStage, ...]
    cadence: str = ""


def gate_one(_: Any) -> bool:
    """Use the first branch for the current input."""
    return True


def gate_two(_: Any) -> bool:
    """Use the second branch for the current input."""
    return False


def node_ids(spec_nodes: tuple[object, ...] | list[object]) -> set[str]:
    ids: set[str] = {getattr(node, "id") for node in spec_nodes}
    return ids


def test_collapse_routes_through_section_terminal() -> None:
    pipeline: ToyPipeline = ToyPipeline(
        root_types=(InputRecord,),
        stages=(
            ToyStage(
                name="prepare",
                input_types=(InputRecord,),
                output_type=PreparedRecord,
                section="Preparation",
                collapse=True,
            ),
            ToyStage(
                name="summarize",
                input_types=(PreparedRecord,),
                output_type=SummaryRecord,
                section="Summary",
            ),
        ),
    )

    fragment = fragment_from_pipeline(pipeline, "toy")

    assert "PreparedRecord" not in node_ids(fragment.nodes)
    assert "toy_Preparation_terminal" in node_ids(fragment.nodes)
    assert any(
        edge.src == "toy_Preparation_terminal" and edge.dst == "SummaryRecord"
        for edge in fragment.edges
    )


def test_marker_folds_into_section_terminal_and_tracks_marker_type() -> None:
    pipeline: ToyPipeline = ToyPipeline(
        root_types=(InputRecord,),
        stages=(
            ToyStage(
                name="audit",
                input_types=(InputRecord,),
                output_type=AuditMarker,
                section="Review",
                marker=True,
            ),
        ),
    )

    fragment = fragment_from_pipeline(pipeline, "toy")

    assert "AuditMarker" not in node_ids(fragment.nodes)
    assert "AuditMarker" in fragment.marker_types
    assert any(
        isinstance(node, TerminalNode) and node.id == "toy_Review_terminal"
        for node in fragment.nodes
    )


def test_gated_pair_creates_decision_and_dashed_conditional_consumers() -> None:
    pipeline: ToyPipeline = ToyPipeline(
        root_types=(InputRecord,),
        stages=(
            ToyStage(
                name="route one",
                input_types=(InputRecord,),
                output_type=RoutedRecord,
                section="Routing",
                when=gate_one,
                question="Which route applies?",
            ),
            ToyStage(
                name="route two",
                input_types=(InputRecord,),
                output_type=RoutedRecord,
                section="Routing",
                when=gate_two,
            ),
            ToyStage(
                name="finish",
                input_types=(RoutedRecord,),
                output_type=FinalRecord,
                section="Finish",
            ),
        ),
    )

    fragment = fragment_from_pipeline(pipeline, "toy")

    assert "decision_RoutedRecord" in {decision.id for decision in fragment.decisions}
    decision = next(
        decision
        for decision in fragment.decisions
        if decision.id == "decision_RoutedRecord"
    )
    assert decision.question == "Which route applies?"
    assert "Use the first branch" in decision.rationale
    assert any(
        edge.src == "RoutedRecord" and edge.dst == "FinalRecord" and edge.dashed
        for edge in fragment.edges
    )


def test_nested_pipeline_fragment_is_flattened_on_merge() -> None:
    child: ToyPipeline = ToyPipeline(
        root_types=(InputRecord,),
        stages=(
            ToyStage(
                name="nested",
                input_types=(InputRecord,),
                output_type=NestedRecord,
                section="Inside",
            ),
        ),
    )
    parent: ToyPipeline = ToyPipeline(
        root_types=(InputRecord,),
        stages=(
            ToyStage(
                name="delegate",
                input_types=(InputRecord,),
                output_type=NestedRecord,
                section="Outside",
                sub_pipeline=child,
            ),
        ),
    )

    fragment = fragment_from_pipeline(parent, "toy")
    spec = merge([fragment], overlay=Overlay(), extra=AuthoredExtra())

    assert len(fragment.children) == 1
    assert "NestedRecord" in node_ids(spec.nodes)
    assert any(group.id == "toy_delegate_Inside" for group in spec.groups)


def test_docstring_and_field_descriptions_populate_model_node() -> None:
    pipeline: ToyPipeline = ToyPipeline(root_types=(PreparedRecord,), stages=())

    fragment = fragment_from_pipeline(pipeline, "toy")

    node = next(
        node
        for node in fragment.nodes
        if isinstance(node, ModelNode) and node.id == "PreparedRecord"
    )
    assert node.prose == "Prepared data available to later stages."

    input_fragment = fragment_from_pipeline(
        ToyPipeline(root_types=(InputRecord,), stages=()), "toy"
    )
    input_node = next(
        node
        for node in input_fragment.nodes
        if isinstance(node, ModelNode) and node.id == "InputRecord"
    )
    assert input_node.notes == {"value": "Input quantity."}


def test_lint_errors_cover_stale_overlay_authored_edges_and_duplicate_ids() -> None:
    pipeline: ToyPipeline = ToyPipeline(
        root_types=(InputRecord,),
        stages=(
            ToyStage(
                name="prepare", input_types=(InputRecord,), output_type=PreparedRecord
            ),
        ),
    )
    fragment = fragment_from_pipeline(pipeline, "toy")

    with pytest.raises(SpecgenLintError, match="unknown type overlay"):
        merge(
            [fragment],
            overlay=Overlay(types={"MissingRecord": TypeOverlay(prose="missing")}),
            extra=AuthoredExtra(),
        )

    with pytest.raises(SpecgenLintError, match="intra_stage=True"):
        merge(
            [fragment],
            overlay=Overlay(),
            extra=AuthoredExtra(
                edges=(AuthoredEdge(src="InputRecord", dst="PreparedRecord"),)
            ),
        )

    with pytest.raises(SpecgenLintError, match="duplicate node id"):
        merge(
            [fragment],
            overlay=Overlay(),
            extra=AuthoredExtra(
                nodes=(TerminalNode(id="InputRecord", label="duplicate"),)
            ),
        )


def test_overlay_can_override_seeded_prose_notes_and_group_cadence() -> None:
    pipeline: ToyPipeline = ToyPipeline(
        root_types=(InputRecord,),
        stages=(
            ToyStage(
                name="prepare", input_types=(InputRecord,), output_type=PreparedRecord
            ),
        ),
    )
    fragment = fragment_from_pipeline(pipeline, "toy")

    spec = merge(
        [fragment],
        overlay=Overlay(
            types={
                "InputRecord": TypeOverlay(
                    prose="Authored prose.", notes={"value": "Authored note."}
                )
            },
            group_cadences={"toy_default": "per item"},
        ),
        extra=AuthoredExtra(),
    )

    node = next(
        node
        for node in spec.nodes
        if isinstance(node, ModelNode) and node.id == "InputRecord"
    )
    assert node.prose == "Authored prose."
    assert node.notes == {"value": "Authored note."}
    assert (
        next(group for group in spec.groups if group.id == "toy_default").cadence
        == "per item"
    )


def test_overlay_suppression_reroutes_edges_through_section_terminal() -> None:
    pipeline: ToyPipeline = ToyPipeline(
        root_types=(InputRecord,),
        stages=(
            ToyStage(
                name="prepare",
                input_types=(InputRecord,),
                output_type=PreparedRecord,
                section="Shared",
            ),
            ToyStage(
                name="record checkpoint",
                input_types=(InputRecord,),
                output_type=AuditMarker,
                section="Shared",
                collapse=True,
            ),
            ToyStage(
                name="summarize",
                input_types=(PreparedRecord,),
                output_type=SummaryRecord,
                section="Summary",
            ),
        ),
    )
    fragment = fragment_from_pipeline(pipeline, "toy")

    spec = merge(
        [fragment],
        overlay=Overlay(types={"PreparedRecord": TypeOverlay(suppress=True)}),
        extra=AuthoredExtra(),
    )

    assert "PreparedRecord" not in node_ids(spec.nodes)
    assert any(
        edge.src == "InputRecord" and edge.dst == "toy_Shared_terminal"
        for edge in spec.edges
    )
    assert any(
        edge.src == "toy_Shared_terminal" and edge.dst == "SummaryRecord"
        for edge in spec.edges
    )
