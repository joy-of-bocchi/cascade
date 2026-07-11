from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict, Field

from engine import (
    DuplicateProducerError,
    DuplicateStageNameError,
    GatedProducerConflictError,
    InvalidChildOutputError,
    InvalidStageInputError,
    MissingChildOutputError,
    MissingRootError,
    MissingStageInputError,
    Pipeline,
    PipelineCycleError,
    PipelineRegistrationError,
    SnapshotSinkError,
    StageStatus,
    StoreOverwriteError,
    UnknownRootError,
    WrongRootTypeError,
    WrongOutputTypeError,
    run,
)
from specgen import fragment_from_pipeline


class ToyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Root(ToyModel):
    value: int


class OtherRoot(ToyModel):
    value: int


class Prepared(ToyModel):
    value: int


class Combined(ToyModel):
    value: int


class Choice(ToyModel):
    value: int


class Alternative(ToyModel):
    value: int


class Final(ToyModel):
    value: int


class ChildOut(ToyModel):
    value: int


class SnapshotCollector(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    calls: list[tuple[str, tuple[str, ...], str]] = Field(default_factory=list)

    def on_stage(
        self,
        stage_name: str,
        inputs: dict[type[BaseModel], BaseModel],
        output: BaseModel,
    ) -> None:
        self.calls.append(
            (
                stage_name,
                tuple(sorted(input_type.__name__ for input_type in inputs)),
                type(output).__name__,
            )
        )


class FailingSnapshotSink(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    def on_stage(
        self,
        stage_name: str,
        inputs: dict[type[BaseModel], BaseModel],
        output: BaseModel,
    ) -> None:
        raise RuntimeError(f"cannot snapshot {stage_name}")


def always_true(root: Root) -> bool:
    return root.value >= 0


def always_false() -> bool:
    return False


def node_ids(nodes: tuple[object, ...]) -> set[str]:
    return {getattr(node, "id") for node in nodes}


def edge_ids(edges: tuple[object, ...]) -> set[tuple[str, str, str]]:
    return {
        (getattr(edge, "src"), getattr(edge, "dst"), getattr(edge, "label"))
        for edge in edges
    }


def test_decorator_parses_model_and_container_inputs() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root, OtherRoot, Prepared))

    @pipeline.stage(output=Combined)
    def combine(
        root: Root,
        others: list[OtherRoot],
        prepared: tuple[Prepared, ...],
    ) -> Combined:
        return Combined(value=root.value + others[0].value + prepared[0].value)

    built = pipeline.build()

    assert built.stages[0].name == "combine"
    assert built.stages[0].input_types == (Root, OtherRoot, Prepared)


def test_decorator_rejects_unannotated_params() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    with pytest.raises(InvalidStageInputError):

        @pipeline.stage(output=Prepared)
        def prepare(root) -> Prepared:
            return Prepared(value=root.value)


def test_decorator_rejects_non_model_params() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    with pytest.raises(InvalidStageInputError):

        @pipeline.stage(output=Prepared)
        def prepare(count: int) -> Prepared:
            return Prepared(value=count)


def test_question_requires_gate() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    with pytest.raises(PipelineRegistrationError):
        pipeline.stage(output=Prepared, question="Which branch?")


def test_duplicate_stage_names_raise_distinct_build_error() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    def first(root: Root) -> Prepared:
        return Prepared(value=root.value)

    def second(root: Root) -> Alternative:
        return Alternative(value=root.value)

    second.__name__ = "first"
    pipeline.stage(output=Prepared)(first)
    pipeline.stage(output=Alternative)(second)

    with pytest.raises(DuplicateStageNameError):
        pipeline.build()


def test_duplicate_producers_raise_distinct_build_error() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    @pipeline.stage(output=Choice)
    def one(root: Root) -> Choice:
        return Choice(value=root.value)

    @pipeline.stage(output=Choice)
    def two(root: Root) -> Choice:
        return Choice(value=root.value + 1)

    with pytest.raises(DuplicateProducerError):
        pipeline.build()


def test_mismatched_gated_producer_metadata_raises_duplicate_producer() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    @pipeline.stage(
        output=Choice,
        when=always_true,
        section="Route",
        question="Which route?",
    )
    def one(root: Root) -> Choice:
        return Choice(value=root.value)

    @pipeline.stage(
        output=Choice,
        when=always_true,
        section="Other route",
        question="Which route?",
    )
    def two(root: Root) -> Choice:
        return Choice(value=root.value + 1)

    with pytest.raises(DuplicateProducerError):
        pipeline.build()


def test_missing_consumed_type_raises_distinct_build_error() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    @pipeline.stage(output=Final)
    def finish(choice: Choice) -> Final:
        return Final(value=choice.value)

    with pytest.raises(MissingStageInputError):
        pipeline.build()


def test_cycles_raise_distinct_build_error() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    @pipeline.stage(output=Prepared)
    def make_prepared(final: Final) -> Prepared:
        return Prepared(value=final.value)

    @pipeline.stage(output=Final)
    def make_final(prepared: Prepared) -> Final:
        return Final(value=prepared.value)

    with pytest.raises(PipelineCycleError):
        pipeline.build()


def test_child_output_must_be_produced_by_child() -> None:
    child: Pipeline = Pipeline(root_types=(Root,))

    @child.stage(output=Prepared)
    def child_prepare(root: Root) -> Prepared:
        return Prepared(value=root.value)

    parent: Pipeline = Pipeline(root_types=(Root,))
    parent.include(child, output=ChildOut, name="delegate")

    with pytest.raises(InvalidChildOutputError):
        parent.build()


def test_build_topologically_sorts_registered_stages() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    @pipeline.stage(output=Final)
    def finish(prepared: Prepared) -> Final:
        return Final(value=prepared.value)

    @pipeline.stage(output=Prepared)
    def prepare(root: Root) -> Prepared:
        return Prepared(value=root.value)

    built = pipeline.build()

    assert [stage.name for stage in built.stages] == ["prepare", "finish"]


def test_run_happy_path_with_container_inputs() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root, OtherRoot))

    @pipeline.stage(output=Prepared)
    def prepare(root: Root) -> Prepared:
        return Prepared(value=root.value + 1)

    @pipeline.stage(output=Combined)
    def combine(
        other_roots: list[OtherRoot],
        prepared_values: tuple[Prepared, ...],
    ) -> Combined:
        return Combined(value=other_roots[0].value + prepared_values[0].value)

    result = run(
        pipeline.build(),
        {Root: Root(value=2), OtherRoot: OtherRoot(value=5)},
    )

    assert result.final_store[Combined] == Combined(value=8)
    assert [record.status for record in result.stages] == [
        StageStatus.SUCCESS,
        StageStatus.SUCCESS,
    ]


def test_run_validates_declared_roots_loudly() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))
    built = pipeline.build()

    with pytest.raises(MissingRootError):
        run(built, {})

    with pytest.raises(UnknownRootError):
        run(built, {Root: Root(value=1), OtherRoot: OtherRoot(value=2)})

    with pytest.raises(WrongRootTypeError):
        run(built, {Root: OtherRoot(value=1)})  # type: ignore[dict-item]


def test_gate_skip_then_missing_input_skip_are_recorded() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    @pipeline.stage(output=Choice, when=always_false)
    def choose(root: Root) -> Choice:
        return Choice(value=root.value)

    @pipeline.stage(output=Final)
    def finish(choice: Choice) -> Final:
        return Final(value=choice.value)

    result = run(pipeline.build(), {Root: Root(value=1)})

    assert [(record.name, record.status) for record in result.stages] == [
        ("choose", StageStatus.SKIPPED),
        ("finish", StageStatus.SKIPPED_MISSING_INPUT),
    ]
    assert result.stages[1].skip_reason == "missing input Choice"


def test_wrong_output_type_raises_distinct_runtime_error() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    @pipeline.stage(output=Prepared)
    def prepare(root: Root) -> Prepared:
        return Alternative(value=root.value)  # type: ignore[return-value]

    with pytest.raises(WrongOutputTypeError):
        run(pipeline.build(), {Root: Root(value=1)})


def test_stage_cannot_silently_replace_root_value() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    @pipeline.stage(output=Root)
    def replace_root(root: Root) -> Root:
        return Root(value=root.value + 1)

    with pytest.raises(StoreOverwriteError):
        run(pipeline.build(), {Root: Root(value=1)})


def test_second_passing_gated_producer_is_runtime_exclusivity_error() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    @pipeline.stage(
        output=Choice,
        when=always_true,
        section="Route",
        question="Which route?",
    )
    def first(root: Root) -> Choice:
        return Choice(value=root.value)

    @pipeline.stage(
        output=Choice,
        when=always_true,
        section="Route",
        question="Which route?",
    )
    def second(root: Root) -> Choice:
        return Choice(value=root.value + 1)

    with pytest.raises(GatedProducerConflictError):
        run(pipeline.build(), {Root: Root(value=1)})


def test_nested_pipeline_lifts_output_records_sub_runs_and_prefixes_snapshots() -> None:
    child: Pipeline = Pipeline(root_types=(Root,))

    @child.stage(output=ChildOut)
    def child_stage(root: Root) -> ChildOut:
        return ChildOut(value=root.value + 10)

    parent: Pipeline = Pipeline(root_types=(Root,))
    parent.include(child, output=ChildOut, name="delegate", section="Child")

    @parent.stage(output=Final)
    def finish(child_out: ChildOut) -> Final:
        return Final(value=child_out.value + 1)

    snapshots = SnapshotCollector()
    result = run(parent.build(), {Root: Root(value=1)}, snapshots=snapshots)

    assert result.final_store[Final] == Final(value=12)
    assert result.stages[0].name == "delegate"
    assert [record.name for record in result.stages[0].sub_runs] == ["child_stage"]
    assert snapshots.calls == [
        ("delegate/child_stage", ("Root",), "ChildOut"),
        ("delegate", ("Root",), "ChildOut"),
        ("finish", ("ChildOut",), "Final"),
    ]


def test_false_gate_skips_whole_child_pipeline() -> None:
    child: Pipeline = Pipeline(root_types=(Root,))

    @child.stage(output=ChildOut)
    def child_stage(root: Root) -> ChildOut:
        return ChildOut(value=root.value)

    parent: Pipeline = Pipeline(root_types=(Root,))
    parent.include(child, output=ChildOut, name="delegate", when=always_false)

    snapshots = SnapshotCollector()
    result = run(parent.build(), {Root: Root(value=1)}, snapshots=snapshots)

    assert result.stages[0].status == StageStatus.SKIPPED
    assert result.stages[0].sub_runs == ()
    assert snapshots.calls == []


def test_missing_child_output_is_distinct_runtime_error() -> None:
    child: Pipeline = Pipeline(root_types=(Root,))

    @child.stage(output=ChildOut, when=always_false)
    def child_stage(root: Root) -> ChildOut:
        return ChildOut(value=root.value)

    parent: Pipeline = Pipeline(root_types=(Root,))
    parent.include(child, output=ChildOut, name="delegate")

    with pytest.raises(MissingChildOutputError):
        run(parent.build(), {Root: Root(value=1)})


def test_snapshot_sink_errors_surface_after_store_update() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Root,))

    @pipeline.stage(output=Prepared)
    def prepare(root: Root) -> Prepared:
        return Prepared(value=root.value + 1)

    with pytest.raises(SnapshotSinkError) as error:
        run(
            pipeline.build(),
            {Root: Root(value=1)},
            snapshots=FailingSnapshotSink(),
        )

    assert error.value.stage_name == "prepare"
    assert error.value.partial_result.final_store[Prepared] == Prepared(value=2)
    assert error.value.partial_result.stages[0].status == StageStatus.SUCCESS


def test_engine_built_pipeline_feeds_specgen_fragment() -> None:
    child: Pipeline = Pipeline(root_types=(Root,))

    @child.stage(output=ChildOut, section="Inside")
    def child_stage(root: Root) -> ChildOut:
        return ChildOut(value=root.value + 10)

    parent: Pipeline = Pipeline(root_types=(Root,), cadence="daily")

    @parent.stage(
        output=Choice,
        when=always_true,
        section="Routing",
        question="Which route?",
    )
    def route_a(root: Root) -> Choice:
        return Choice(value=root.value)

    @parent.stage(
        output=Choice,
        when=always_false,
        section="Routing",
        question="Which route?",
    )
    def route_b(root: Root) -> Choice:
        return Choice(value=root.value + 1)

    parent.include(child, output=ChildOut, name="delegate", section="Delegate")

    @parent.stage(output=Final, section="Finish")
    def finish(choice: Choice, child_out: ChildOut) -> Final:
        return Final(value=choice.value + child_out.value)

    fragment = fragment_from_pipeline(parent.build(), "engine")

    assert {"Root", "Choice", "Final"}.issubset(node_ids(fragment.nodes))
    assert "decision_Choice" in {decision.id for decision in fragment.decisions}
    assert len(fragment.children) == 1
    assert "ChildOut" in node_ids(fragment.children[0].fragment.nodes)
    assert {
        ("Root", "decision_Choice", ""),
        ("decision_Choice", "Choice", "route_a"),
        ("decision_Choice", "Choice", "route_b"),
        ("Choice", "Final", "finish (absent when gated out)"),
        ("ChildOut", "Final", "finish"),
    }.issubset(edge_ids(fragment.edges))
