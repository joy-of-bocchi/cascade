from __future__ import annotations

import inspect
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from d2spec import (
    DecisionNode,
    DiagramSpec,
    Edge,
    Group,
    ModelNode,
    ModuleNode,
    TerminalNode,
)
from speclint import Violation, ViolationKind, validate

DEFAULT_GROUP_SUFFIX: str = "default"
DECISION_PREFIX: str = "decision"
TERMINAL_SUFFIX: str = "terminal"
GATED_ABSENCE_LABEL: str = "absent when gated out"
WHITESPACE_RE: re.Pattern[str] = re.compile(r"\s+")
ID_SAFE_RE: re.Pattern[str] = re.compile(r"[^A-Za-z0-9_]+")
FIRST_PARAGRAPH_INDEX: int = 0
INITIAL_SUFFIX: int = 2

AnySeedNode = ModelNode | TerminalNode | ModuleNode
AnySpecNode = ModelNode | DecisionNode | TerminalNode | ModuleNode


class StageSpecProtocol(Protocol):
    name: str
    input_types: Iterable[type]
    output_type: type
    when: Callable[..., bool] | None
    section: str | None
    collapse: bool
    marker: bool
    question: str | None
    reads_external: str | None
    sub_pipeline: BuiltPipelineProtocol | None


class BuiltPipelineProtocol(Protocol):
    root_types: Iterable[type]
    stages: Iterable[StageSpecProtocol]


class SpecgenLintError(ValueError):
    """Seeded and authored diagram pieces cannot merge."""


class ChildFragment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage_name: str
    fragment: SpecFragment


class TypeSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type_name: str
    source_id: str
    label: str = ""
    dashed: bool = False


class SpecFragment(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    pipeline_id: str
    nodes: tuple[AnySeedNode, ...] = ()
    decisions: tuple[DecisionNode, ...] = ()
    edges: tuple[Edge, ...] = ()
    groups: tuple[Group, ...] = ()
    marker_types: tuple[str, ...] = ()
    children: tuple[ChildFragment, ...] = ()
    root_type_names: tuple[str, ...] = ()
    type_sources: tuple[TypeSource, ...] = ()


class TypeOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prose: str | None = None
    notes: dict[str, str] = Field(default_factory=dict)
    suppress: bool = False


class DecisionOverlay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str | None = None
    rationale: str | None = None


class Overlay(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    types: dict[str, TypeOverlay] = Field(default_factory=dict)
    decisions: dict[str, DecisionOverlay] = Field(default_factory=dict)
    group_cadences: dict[str, str] = Field(default_factory=dict)


class AuthoredEdge(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    src: str
    dst: str
    label: str = ""
    payload: type | None = None
    dashed: bool = False
    intra_stage: bool = False

    def to_edge(self) -> Edge:
        edge: Edge = Edge(
            src=self.src,
            dst=self.dst,
            label=self.label,
            payload=self.payload,
            dashed=self.dashed,
        )
        return edge


class AuthoredExtra(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    nodes: tuple[AnySpecNode, ...] = ()
    edges: tuple[AuthoredEdge, ...] = ()
    groups: tuple[Group, ...] = ()
    roots: tuple[str, ...] = ()


class _StageSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str


class _FragmentState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    pipeline_id: str
    cadence: str
    nodes_by_id: dict[str, AnySeedNode] = Field(default_factory=dict)
    decisions_by_id: dict[str, DecisionNode] = Field(default_factory=dict)
    edges: list[Edge] = Field(default_factory=list)
    groups_by_id: dict[str, Group] = Field(default_factory=dict)
    marker_types: set[str] = Field(default_factory=set)
    children: list[ChildFragment] = Field(default_factory=list)
    type_groups: dict[str, str] = Field(default_factory=dict)
    collapsed_terminals_by_group: dict[str, TerminalNode] = Field(default_factory=dict)
    sources_by_type: dict[str, list[TypeSource]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_default_group(self) -> Self:
        section: _StageSection = _section_for_stage(self.pipeline_id, None)
        self.groups_by_id.setdefault(
            section.id,
            Group(id=section.id, label=section.label, cadence=self.cadence),
        )
        return self


def fragment_from_pipeline(
    built: BuiltPipelineProtocol, pipeline_id: str
) -> SpecFragment:
    root_types: tuple[type, ...] = tuple(getattr(built, "root_types"))
    stages: tuple[StageSpecProtocol, ...] = tuple(getattr(built, "stages"))
    cadence: str = getattr(built, "cadence", "") or ""
    state: _FragmentState = _FragmentState(pipeline_id=pipeline_id, cadence=cadence)
    for root_type in root_types:
        section: _StageSection = _section_for_stage(pipeline_id, None)
        _ensure_group(state, section)
        _ensure_model_node(state, root_type, section.id)
        _register_type_source(state, root_type, _type_name(root_type))

    gated_pairs_by_output: dict[type, tuple[StageSpecProtocol, StageSpecProtocol]] = (
        _gated_pairs_by_output(stages)
    )
    seeded_gated_output_types: set[type] = set()

    for stage in stages:
        output_type: type = _stage_output_type(stage)
        gated_pair: tuple[StageSpecProtocol, StageSpecProtocol] | None = (
            gated_pairs_by_output.get(output_type)
        )
        if gated_pair is not None:
            if output_type not in seeded_gated_output_types:
                _seed_gated_pair(state, gated_pair)
                seeded_gated_output_types.add(output_type)
            continue
        _seed_stage(state, stage)

    fragment: SpecFragment = SpecFragment(
        pipeline_id=pipeline_id,
        nodes=tuple(state.nodes_by_id.values()),
        decisions=tuple(state.decisions_by_id.values()),
        edges=tuple(_dedupe_edges(state.edges)),
        groups=tuple(state.groups_by_id.values()),
        marker_types=tuple(sorted(state.marker_types)),
        children=tuple(state.children),
        root_type_names=tuple(_type_name(root_type) for root_type in root_types),
        type_sources=tuple(
            source for sources in state.sources_by_type.values() for source in sources
        ),
    )
    return fragment


def merge(
    fragments: list[SpecFragment], overlay: Overlay, extra: AuthoredExtra
) -> DiagramSpec:
    flat_fragments: list[SpecFragment] = _flatten_fragments(fragments)
    seeded_nodes_by_id: dict[str, AnySpecNode] = {}
    seeded_model_ids: set[str] = set()
    seeded_decision_ids: set[str] = set()
    seeded_group_ids: set[str] = set()
    seeded_edges: list[Edge] = []
    marker_types: set[str] = set()

    for fragment in flat_fragments:
        marker_types.update(fragment.marker_types)
        for group in fragment.groups:
            seeded_group_ids.add(group.id)
        for node in (*fragment.nodes, *fragment.decisions):
            if isinstance(node, ModelNode):
                seeded_model_ids.add(node.id)
                seeded_nodes_by_id.setdefault(node.id, node)
            elif isinstance(node, DecisionNode):
                seeded_decision_ids.add(node.id)
                seeded_nodes_by_id.setdefault(node.id, node)
            else:
                seeded_nodes_by_id.setdefault(node.id, node)
        seeded_edges.extend(fragment.edges)

    _validate_overlay_keys(
        overlay, seeded_model_ids, seeded_decision_ids, seeded_group_ids
    )
    suppressed_type_ids: set[str] = _suppressed_type_ids(overlay)
    suppression_replacements: dict[str, str] = _suppression_replacements(
        suppressed_type_ids, seeded_nodes_by_id
    )
    _validate_suppression(
        suppressed_type_ids, seeded_edges, marker_types, suppression_replacements
    )
    _validate_authored_edges(extra.edges, seeded_model_ids)

    nodes: list[AnySpecNode] = []
    for node in seeded_nodes_by_id.values():
        if node.id in suppressed_type_ids:
            continue
        nodes.append(_apply_node_overlay(node, overlay))

    groups_by_id: dict[str, Group] = {}
    for fragment in flat_fragments:
        for group in fragment.groups:
            groups_by_id.setdefault(group.id, _apply_group_overlay(group, overlay))
    groups: list[Group] = list(groups_by_id.values())
    groups.extend(extra.groups)

    edges: list[Edge] = _reroute_suppressed_edges(
        seeded_edges, suppressed_type_ids, suppression_replacements
    )
    edges.extend(authored_edge.to_edge() for authored_edge in extra.edges)
    nodes.extend(extra.nodes)

    _raise_on_duplicate_node_ids(nodes)

    spec: DiagramSpec = DiagramSpec(
        nodes=nodes,
        edges=_dedupe_edges(edges),
        groups=groups,
        roots=list(extra.roots),
    )
    _raise_on_lint_violations(spec)
    return spec


def _seed_stage(state: _FragmentState, stage: StageSpecProtocol) -> None:
    section: _StageSection = _section_for_stage(
        state.pipeline_id, _stage_section(stage)
    )
    _ensure_group(state, section)

    sub_pipeline: BuiltPipelineProtocol | None = _stage_sub_pipeline(stage)
    if sub_pipeline is not None:
        child_pipeline_id: str = f"{state.pipeline_id}_{_safe_id(_stage_name(stage))}"
        child_fragment: SpecFragment = fragment_from_pipeline(
            sub_pipeline, child_pipeline_id
        )
        state.children.append(
            ChildFragment(stage_name=_stage_name(stage), fragment=child_fragment)
        )
        for source in _child_sources_for_type(
            child_fragment, _stage_output_type(stage), _stage_when(stage) is not None
        ):
            _register_source(state, source)
        for input_type in _distinct_types(_stage_input_types(stage)):
            for child_root_source in _child_sources_for_type(
                child_fragment, input_type, False
            ):
                _append_edges_for_input(
                    state,
                    input_type=input_type,
                    dst=child_root_source.source_id,
                    group_id=section.id,
                    label=_stage_name(stage),
                )
        return

    if _stage_marker(stage):
        terminal: TerminalNode = _ensure_terminal(state, section)
        _append_external_read(state, stage, terminal.id, section)
        state.marker_types.add(_type_name(_stage_output_type(stage)))
        _register_type_source(
            state,
            _stage_output_type(stage),
            terminal.id,
            gated=_stage_when(stage) is not None,
        )
        for input_type in _distinct_types(_stage_input_types(stage)):
            _append_edges_for_input(
                state,
                input_type=input_type,
                dst=terminal.id,
                group_id=section.id,
                label=_stage_name(stage),
            )
        return

    if _stage_collapse(stage):
        terminal = _ensure_terminal(state, section)
        _append_external_read(state, stage, terminal.id, section)
        _register_type_source(
            state,
            _stage_output_type(stage),
            terminal.id,
            gated=_stage_when(stage) is not None,
        )
        for input_type in _distinct_types(_stage_input_types(stage)):
            _append_edges_for_input(
                state,
                input_type=input_type,
                dst=terminal.id,
                group_id=section.id,
                label=_stage_name(stage),
            )
        return

    output_node: ModelNode = _ensure_model_node(
        state, _stage_output_type(stage), section.id
    )
    _append_external_read(state, stage, output_node.id, section)
    _register_type_source(
        state,
        _stage_output_type(stage),
        output_node.id,
        gated=_stage_when(stage) is not None,
    )

    for input_type in _distinct_types(_stage_input_types(stage)):
        _append_edges_for_input(
            state,
            input_type=input_type,
            dst=output_node.id,
            group_id=section.id,
            label=_stage_name(stage),
        )


def _gated_pairs_by_output(
    stages: Iterable[StageSpecProtocol],
) -> dict[type, tuple[StageSpecProtocol, StageSpecProtocol]]:
    stages_by_output: dict[type, list[StageSpecProtocol]] = defaultdict(list)
    for stage in stages:
        if _stage_when(stage) is not None:
            stages_by_output[_stage_output_type(stage)].append(stage)

    pairs_by_output: dict[type, tuple[StageSpecProtocol, StageSpecProtocol]] = {}
    for output_type, producers in stages_by_output.items():
        if len(producers) == 2:
            pairs_by_output[output_type] = (producers[0], producers[1])
    return pairs_by_output


def _seed_gated_pair(
    state: _FragmentState,
    producers: tuple[StageSpecProtocol, StageSpecProtocol],
) -> None:
    output_type: type = _stage_output_type(producers[0])
    section: _StageSection = _section_for_stage(
        state.pipeline_id, _stage_section(producers[0])
    )
    _ensure_group(state, section)
    decision: DecisionNode = DecisionNode(
        id=_decision_id(output_type),
        question=_gate_question(list(producers)),
        rationale=_gate_rationale(list(producers)),
        group=section.id,
    )
    state.decisions_by_id.setdefault(decision.id, decision)
    for producer in producers:
        _append_external_read(state, producer, decision.id, section)
    input_types: tuple[type, ...] = tuple(
        input_type
        for producer in producers
        for input_type in _stage_input_types(producer)
    )
    for input_type in _distinct_types(input_types):
        _append_edges_for_input(
            state,
            input_type=input_type,
            dst=decision.id,
            group_id=section.id,
        )
    for producer in producers:
        output_source: TypeSource = _output_source_for_stage(
            state, producer, gated=True
        )
        state.edges.append(
            Edge(
                src=decision.id,
                dst=output_source.source_id,
                label=_stage_name(producer),
            )
        )


def _ensure_model_node(
    state: _FragmentState,
    model_type: type,
    group_id: str,
) -> ModelNode:
    node_id: str = _type_name(model_type)
    existing: AnySeedNode | None = state.nodes_by_id.get(node_id)
    if isinstance(existing, ModelNode):
        return existing
    node: ModelNode = ModelNode(
        id=node_id,
        model=model_type,
        prose=_model_prose(model_type),
        notes=_field_notes(model_type),
        group=group_id,
    )
    state.nodes_by_id[node_id] = node
    state.type_groups.setdefault(node_id, group_id)
    return node


def _ensure_terminal(state: _FragmentState, section: _StageSection) -> TerminalNode:
    existing: TerminalNode | None = state.collapsed_terminals_by_group.get(section.id)
    if existing is not None:
        return existing
    terminal: TerminalNode = TerminalNode(
        id=f"{section.id}_{TERMINAL_SUFFIX}",
        label=section.label,
        group=section.id,
    )
    state.collapsed_terminals_by_group[section.id] = terminal
    state.nodes_by_id[terminal.id] = terminal
    return terminal


def _output_source_for_stage(
    state: _FragmentState,
    stage: StageSpecProtocol,
    *,
    gated: bool,
) -> TypeSource:
    section: _StageSection = _section_for_stage(
        state.pipeline_id, _stage_section(stage)
    )
    _ensure_group(state, section)
    if _stage_marker(stage) or _stage_collapse(stage):
        terminal: TerminalNode = _ensure_terminal(state, section)
        if _stage_marker(stage):
            state.marker_types.add(_type_name(_stage_output_type(stage)))
        _register_type_source(
            state, _stage_output_type(stage), terminal.id, gated=gated
        )
        return _source_for_id(state, _stage_output_type(stage), terminal.id)
    output_node: ModelNode = _ensure_model_node(
        state, _stage_output_type(stage), section.id
    )
    _register_type_source(state, _stage_output_type(stage), output_node.id, gated=gated)
    return _source_for_id(state, _stage_output_type(stage), output_node.id)


def _append_external_read(
    state: _FragmentState,
    stage: StageSpecProtocol,
    dst: str,
    section: _StageSection,
) -> None:
    reads_external: str | None = _stage_reads_external(stage)
    if reads_external is None:
        return
    external: TerminalNode = _ensure_external_terminal(state, reads_external, section)
    _append_edge_if_distinct(
        state,
        src=external.id,
        dst=dst,
        label=_stage_name(stage),
        dashed=True,
    )


def _ensure_external_terminal(
    state: _FragmentState,
    label: str,
    section: _StageSection,
) -> TerminalNode:
    terminal_id: str = _external_terminal_id(state, label, section)
    existing: AnySeedNode | None = state.nodes_by_id.get(terminal_id)
    if (
        isinstance(existing, TerminalNode)
        and existing.label == label
        and existing.group == section.id
    ):
        return existing
    terminal: TerminalNode = TerminalNode(id=terminal_id, label=label, group=section.id)
    state.nodes_by_id[terminal.id] = terminal
    return terminal


def _external_terminal_id(
    state: _FragmentState,
    label: str,
    section: _StageSection,
) -> str:
    base_id: str = _safe_id(f"{section.id}_external_{label}")
    candidate: str = base_id
    suffix: int = INITIAL_SUFFIX
    while True:
        existing: AnySeedNode | None = state.nodes_by_id.get(candidate)
        if existing is None:
            return candidate
        if (
            isinstance(existing, TerminalNode)
            and existing.label == label
            and existing.group == section.id
        ):
            return candidate
        candidate = f"{base_id}_{suffix}"
        suffix += 1


def _register_type_source(
    state: _FragmentState,
    model_type: type,
    source_id: str,
    *,
    gated: bool = False,
) -> None:
    label: str = GATED_ABSENCE_LABEL if gated else ""
    _register_source(
        state,
        TypeSource(
            type_name=_type_name(model_type),
            source_id=source_id,
            label=label,
            dashed=gated,
        ),
    )


def _register_source(state: _FragmentState, source: TypeSource) -> None:
    sources: list[TypeSource] = state.sources_by_type.setdefault(source.type_name, [])
    for index, existing in enumerate(sources):
        if existing.source_id != source.source_id:
            continue
        label: str = existing.label or source.label
        dashed: bool = existing.dashed or source.dashed
        sources[index] = existing.model_copy(update={"label": label, "dashed": dashed})
        return
    sources.append(source)


def _source_for_id(
    state: _FragmentState,
    model_type: type,
    source_id: str,
) -> TypeSource:
    for source in state.sources_by_type.get(_type_name(model_type), []):
        if source.source_id == source_id:
            return source
    raise SpecgenLintError(
        f"missing source registration for {_type_name(model_type)}: {source_id}"
    )


def _sources_for_input(
    state: _FragmentState,
    model_type: type,
    group_id: str,
) -> tuple[TypeSource, ...]:
    type_name: str = _type_name(model_type)
    sources: list[TypeSource] | None = state.sources_by_type.get(type_name)
    if sources:
        return tuple(sources)
    _ensure_model_node(state, model_type, state.type_groups.get(type_name, group_id))
    _register_type_source(state, model_type, type_name)
    return tuple(state.sources_by_type[type_name])


def _child_sources_for_type(
    child_fragment: SpecFragment,
    model_type: type,
    parent_gated: bool,
) -> tuple[TypeSource, ...]:
    sources: list[TypeSource] = []
    for source in child_fragment.type_sources:
        if source.type_name != _type_name(model_type):
            continue
        if parent_gated:
            sources.append(
                source.model_copy(
                    update={
                        "label": source.label or GATED_ABSENCE_LABEL,
                        "dashed": True,
                    }
                )
            )
        else:
            sources.append(source)
    if sources:
        return tuple(sources)
    fallback: TypeSource = TypeSource(
        type_name=_type_name(model_type),
        source_id=_type_name(model_type),
        label=GATED_ABSENCE_LABEL if parent_gated else "",
        dashed=parent_gated,
    )
    return (fallback,)


def _append_edges_for_input(
    state: _FragmentState,
    *,
    input_type: type,
    dst: str,
    group_id: str,
    label: str = "",
) -> None:
    for source in _sources_for_input(state, input_type, group_id):
        _append_edge_if_distinct(
            state,
            src=source.source_id,
            dst=dst,
            label=_label_with_source_context(label, source),
            dashed=source.dashed,
        )


def _label_with_source_context(label: str, source: TypeSource) -> str:
    if not source.label:
        return label
    if not label:
        return source.label
    return f"{label} ({source.label})"


def _append_edge_if_distinct(
    state: _FragmentState,
    *,
    src: str,
    dst: str,
    label: str = "",
    dashed: bool = False,
) -> None:
    if src == dst:
        return
    state.edges.append(Edge(src=src, dst=dst, label=label, dashed=dashed))


def _ensure_group(state: _FragmentState, section: _StageSection) -> None:
    state.groups_by_id.setdefault(
        section.id, Group(id=section.id, label=section.label, cadence=state.cadence)
    )


def _section_for_stage(pipeline_id: str, section: str | None) -> _StageSection:
    if section is None:
        return _StageSection(
            id=_safe_id(f"{pipeline_id}_{DEFAULT_GROUP_SUFFIX}"),
            label=pipeline_id,
        )
    return _StageSection(
        id=_safe_id(f"{pipeline_id}_{section}"),
        label=section,
    )


def _model_prose(model_type: type) -> str:
    doc: str | None = getattr(model_type, "__doc__", None)
    if not doc:
        return ""
    paragraphs: list[str] = inspect.cleandoc(doc).split("\n\n")
    if not paragraphs:
        return ""
    return WHITESPACE_RE.sub(" ", paragraphs[FIRST_PARAGRAPH_INDEX]).strip()


def _field_notes(model_type: type) -> dict[str, str]:
    notes: dict[str, str] = {}
    model_fields: Any = getattr(model_type, "model_fields", {})
    for name, field_info in model_fields.items():
        description: str | None = getattr(field_info, "description", None)
        if description:
            notes[name] = description
    return notes


def _gate_question(producers: list[StageSpecProtocol]) -> str:
    for producer in producers:
        question: str | None = _stage_question(producer)
        if question is not None:
            return question
    predicate_names: list[str] = [
        _callable_name(_stage_when(producer)) for producer in producers
    ]
    return "Which gate is true: " + " or ".join(predicate_names) + "?"


def _gate_rationale(producers: list[StageSpecProtocol]) -> str:
    rationales: list[str] = []
    for producer in producers:
        predicate: Callable[..., bool] | None = _stage_when(producer)
        if predicate is None or predicate.__doc__ is None:
            continue
        rationale: str = WHITESPACE_RE.sub(
            " ",
            inspect.cleandoc(predicate.__doc__).split("\n\n")[FIRST_PARAGRAPH_INDEX],
        ).strip()
        if rationale:
            rationales.append(rationale)
    return " ".join(rationales)


def _callable_name(function: Callable[..., bool] | None) -> str:
    if function is None:
        return "always"
    return getattr(function, "__name__", "anonymous")


def _decision_id(model_type: type) -> str:
    return _safe_id(f"{DECISION_PREFIX}_{_type_name(model_type)}")


def _safe_id(value: str) -> str:
    cleaned: str = ID_SAFE_RE.sub("_", value).strip("_")
    return cleaned or "diagram"


def _distinct_types(types: Iterable[type]) -> tuple[type, ...]:
    ordered: dict[type, None] = {}
    for model_type in types:
        ordered.setdefault(model_type, None)
    return tuple(ordered)


def _dedupe_edges(edges: Iterable[Edge]) -> list[Edge]:
    seen: set[tuple[str, str, str, type | None, bool]] = set()
    deduped: list[Edge] = []
    for edge in edges:
        key: tuple[str, str, str, type | None, bool] = (
            edge.src,
            edge.dst,
            edge.label,
            edge.payload,
            edge.dashed,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    return deduped


def _flatten_fragments(fragments: list[SpecFragment]) -> list[SpecFragment]:
    flattened: list[SpecFragment] = []
    for fragment in fragments:
        flattened.append(fragment)
        flattened.extend(
            _flatten_fragments([child.fragment for child in fragment.children])
        )
    return flattened


def _validate_overlay_keys(
    overlay: Overlay,
    seeded_model_ids: set[str],
    seeded_decision_ids: set[str],
    seeded_group_ids: set[str],
) -> None:
    stale_types: set[str] = set(overlay.types) - seeded_model_ids
    stale_decisions: set[str] = set(overlay.decisions) - seeded_decision_ids
    stale_groups: set[str] = set(overlay.group_cadences) - seeded_group_ids
    messages: list[str] = []
    if stale_types:
        messages.append(f"unknown type overlay(s): {', '.join(sorted(stale_types))}")
    if stale_decisions:
        messages.append(
            f"unknown decision overlay(s): {', '.join(sorted(stale_decisions))}"
        )
    if stale_groups:
        messages.append(f"unknown group overlay(s): {', '.join(sorted(stale_groups))}")
    if messages:
        raise SpecgenLintError("; ".join(messages))


def _suppressed_type_ids(overlay: Overlay) -> set[str]:
    return {
        type_name
        for type_name, type_overlay in overlay.types.items()
        if type_overlay.suppress
    }


def _validate_suppression(
    suppressed_type_ids: set[str],
    seeded_edges: list[Edge],
    marker_types: set[str],
    replacement_ids: dict[str, str],
) -> None:
    for type_id in sorted(suppressed_type_ids):
        for edge in seeded_edges:
            if (
                edge.src == type_id
                and edge.dst not in marker_types
                and type_id not in replacement_ids
            ):
                raise SpecgenLintError(
                    f"cannot suppress {type_id}; it has seeded consumers"
                )
            if (
                edge.dst == type_id
                and edge.src not in marker_types
                and type_id not in replacement_ids
            ):
                raise SpecgenLintError(
                    f"cannot suppress {type_id}; it has seeded producers"
                )


def _suppression_replacements(
    suppressed_type_ids: set[str],
    seeded_nodes_by_id: dict[str, AnySpecNode],
) -> dict[str, str]:
    terminals_by_group: dict[str, str] = {}
    for node in seeded_nodes_by_id.values():
        if isinstance(node, TerminalNode) and node.group is not None:
            terminals_by_group.setdefault(node.group, node.id)

    replacements: dict[str, str] = {}
    for type_id in suppressed_type_ids:
        node: AnySpecNode | None = seeded_nodes_by_id.get(type_id)
        if not isinstance(node, ModelNode) or node.group is None:
            continue
        terminal_id: str | None = terminals_by_group.get(node.group)
        if terminal_id is not None:
            replacements[type_id] = terminal_id
    return replacements


def _reroute_suppressed_edges(
    seeded_edges: list[Edge],
    suppressed_type_ids: set[str],
    replacement_ids: dict[str, str],
) -> list[Edge]:
    edges: list[Edge] = []
    for edge in seeded_edges:
        src: str = replacement_ids.get(edge.src, edge.src)
        dst: str = replacement_ids.get(edge.dst, edge.dst)
        if src in suppressed_type_ids or dst in suppressed_type_ids or src == dst:
            continue
        edges.append(edge.model_copy(update={"src": src, "dst": dst}))
    return edges


def _validate_authored_edges(
    edges: tuple[AuthoredEdge, ...], seeded_model_ids: set[str]
) -> None:
    for edge in edges:
        if (
            edge.src in seeded_model_ids
            and edge.dst in seeded_model_ids
            and not edge.intra_stage
        ):
            raise SpecgenLintError(
                f"authored edge {edge.src}->{edge.dst} connects seeded types without intra_stage=True"
            )


def _raise_on_lint_violations(spec: DiagramSpec) -> None:
    violations: list[Violation] = [
        violation
        for violation in validate(spec)
        if violation.kind == ViolationKind.DANGLING_REFERENCE
    ]
    if not violations:
        return
    messages: list[str] = []
    for violation in violations:
        nodes: str = ", ".join(violation.nodes)
        messages.append(f"{violation.kind}: {violation.detail} ({nodes})")
    raise SpecgenLintError("; ".join(messages))


def _apply_node_overlay(node: AnySpecNode, overlay: Overlay) -> AnySpecNode:
    if isinstance(node, ModelNode):
        type_overlay: TypeOverlay | None = overlay.types.get(node.id)
        if type_overlay is None:
            return node
        prose: str = (
            type_overlay.prose if type_overlay.prose is not None else node.prose
        )
        notes: dict[str, str] = type_overlay.notes or node.notes
        return node.model_copy(update={"prose": prose, "notes": notes})
    if isinstance(node, DecisionNode):
        decision_overlay: DecisionOverlay | None = overlay.decisions.get(node.id)
        if decision_overlay is None:
            return node
        question: str = (
            decision_overlay.question
            if decision_overlay.question is not None
            else node.question
        )
        rationale: str = (
            decision_overlay.rationale
            if decision_overlay.rationale is not None
            else node.rationale
        )
        return node.model_copy(update={"question": question, "rationale": rationale})
    return node


def _apply_group_overlay(group: Group, overlay: Overlay) -> Group:
    cadence: str | None = overlay.group_cadences.get(group.id)
    if cadence is None:
        return group
    return group.model_copy(update={"cadence": cadence})


def _raise_on_duplicate_node_ids(nodes: list[AnySpecNode]) -> None:
    counts: dict[str, int] = defaultdict(int)
    for node in nodes:
        counts[node.id] += 1
    duplicates: list[str] = sorted(
        node_id for node_id, count in counts.items() if count > 1
    )
    if duplicates:
        raise SpecgenLintError(f"duplicate node id(s): {', '.join(duplicates)}")


def _stage_name(stage: StageSpecProtocol) -> str:
    name: str | None = getattr(stage, "name", None)
    if name:
        return name
    return _type_name(_stage_output_type(stage))


def _stage_input_types(stage: StageSpecProtocol) -> tuple[type, ...]:
    input_types: Iterable[type] = getattr(stage, "input_types", ())
    return tuple(input_types)


def _stage_output_type(stage: StageSpecProtocol) -> type:
    output_type: type = getattr(stage, "output_type")
    return output_type


def _stage_when(stage: StageSpecProtocol) -> Callable[..., bool] | None:
    when: Callable[..., bool] | None = getattr(stage, "when", None)
    return when


def _stage_section(stage: StageSpecProtocol) -> str | None:
    section: str | None = getattr(stage, "section", None)
    return section


def _stage_collapse(stage: StageSpecProtocol) -> bool:
    collapse: bool = bool(getattr(stage, "collapse", False))
    return collapse


def _stage_marker(stage: StageSpecProtocol) -> bool:
    marker: bool = bool(getattr(stage, "marker", False))
    return marker


def _stage_question(stage: StageSpecProtocol) -> str | None:
    question: str | None = getattr(stage, "question", None)
    return question


def _stage_reads_external(stage: StageSpecProtocol) -> str | None:
    reads_external: str | None = getattr(stage, "reads_external", None)
    return reads_external


def _stage_sub_pipeline(stage: StageSpecProtocol) -> BuiltPipelineProtocol | None:
    sub_pipeline: BuiltPipelineProtocol | None = getattr(stage, "sub_pipeline", None)
    return sub_pipeline


def _type_name(model_type: type) -> str:
    name: str = getattr(model_type, "__name__", str(model_type))
    return name


SpecFragment.model_rebuild()
ChildFragment.model_rebuild()
