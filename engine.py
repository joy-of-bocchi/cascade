from __future__ import annotations

import inspect
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from enum import Enum
from typing import Protocol, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

LIST_CONTAINER: str = "list"
SINGLE_CONTAINER: str = "single"
TUPLE_CONTAINER: str = "tuple"
ELLIPSIS_ARGS: tuple[object, ...] = (Ellipsis,)
NO_SKIP_REASON: str | None = None


class PipelineRegistrationError(ValueError):
    """A stage cannot be registered in the pipeline."""


class InvalidStageInputError(PipelineRegistrationError):
    """A stage input annotation is missing or unsupported."""


class InvalidStageOutputError(PipelineRegistrationError):
    """A stage output type is not a Pydantic model."""


class PipelineBuildError(ValueError):
    """A registered pipeline cannot be built."""


class DuplicateStageNameError(PipelineBuildError):
    """Two stages share one name."""


class DuplicateProducerError(PipelineBuildError):
    """Two stages produce one output type without an exclusive gate contract."""


class MissingStageInputError(PipelineBuildError):
    """A stage consumes a type that is neither a root nor a stage output."""


class PipelineCycleError(PipelineBuildError):
    """Stage dependencies contain a cycle."""


class InvalidChildOutputError(PipelineBuildError):
    """An included child pipeline does not produce the requested lifted type."""


class PipelineRunError(RuntimeError):
    """A built pipeline failed while running."""


class MissingRootError(PipelineRunError):
    """A declared root value was not provided."""


class UnknownRootError(PipelineRunError):
    """A provided root type is not declared by the pipeline."""


class WrongRootTypeError(PipelineRunError):
    """A root value does not match its type key."""


class WrongOutputTypeError(PipelineRunError):
    """A stage returned a value that does not match its declared output type."""


class StoreOverwriteError(PipelineRunError):
    """A stage tried to replace a value already present in the run store."""


class GatedProducerConflictError(StoreOverwriteError):
    """Two gated producers for one type both passed their gates."""


class MissingChildOutputError(PipelineRunError):
    """An included child pipeline did not produce the lifted output value."""


class SnapshotSinkError(PipelineRunError):
    """A snapshot sink failed after a successful stage updated the store."""

    def __init__(
        self,
        stage_name: str,
        original: Exception,
        partial_result: RunResult,
    ) -> None:
        super().__init__(f"snapshot sink failed for stage {stage_name!r}: {original}")
        self.stage_name: str = stage_name
        self.original: Exception = original
        self.partial_result: RunResult = partial_result


class StageStatus(str, Enum):
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    SKIPPED_MISSING_INPUT = "SKIPPED_MISSING_INPUT"


class SnapshotSinkProtocol(Protocol):
    def on_stage(
        self,
        stage_name: str,
        inputs: dict[type[BaseModel], BaseModel],
        output: BaseModel,
    ) -> None: ...


class StageParameter(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    name: str
    model_type: type[BaseModel]
    container: str = SINGLE_CONTAINER


class StageSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    name: str
    input_types: tuple[type[BaseModel], ...] = ()
    output_type: type[BaseModel]
    when: Callable[..., bool] | None = None
    section: str | None = None
    collapse: bool = False
    marker: bool = False
    question: str | None = None
    reads_external: str | None = None
    sub_pipeline: BuiltPipeline | None = None
    func: Callable[..., BaseModel] | None = None
    parameters: tuple[StageParameter, ...] = ()


class BuiltPipeline(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    root_types: tuple[type[BaseModel], ...]
    stages: tuple[StageSpec, ...]
    cadence: str = ""
    produced_types: tuple[type[BaseModel], ...] = ()


class StageRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    status: StageStatus
    elapsed_ms: float
    output_type_name: str
    skip_reason: str | None = None
    sub_runs: tuple[StageRun, ...] = ()


class RunResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    stages: tuple[StageRun, ...]
    wall_time_ms: float
    final_store: dict[type[BaseModel], BaseModel] = Field(default_factory=dict)


class _PendingStage(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    name: str
    output_type: type[BaseModel]
    when: Callable[..., bool] | None = None
    section: str | None = None
    collapse: bool = False
    marker: bool = False
    question: str | None = None
    reads_external: str | None = None
    func: Callable[..., BaseModel] | None = None
    parameters: tuple[StageParameter, ...] = ()
    child: Pipeline | None = None


class Pipeline(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    root_types: tuple[type[BaseModel], ...]
    cadence: str | None = None
    _stages: list[_PendingStage] = PrivateAttr(default_factory=list)

    def __init__(
        self,
        root_types: tuple[type[BaseModel], ...],
        cadence: str | None = None,
    ) -> None:
        _validate_model_types(root_types, "root")
        super().__init__(root_types=root_types, cadence=cadence)

    def stage(
        self,
        *,
        output: type[BaseModel],
        when: Callable[..., bool] | None = None,
        section: str | None = None,
        collapse: bool = False,
        marker: bool = False,
        question: str | None = None,
        reads_external: str | None = None,
    ) -> Callable[[Callable[..., BaseModel]], Callable[..., BaseModel]]:
        _validate_model_type(output, "stage output")
        if question is not None and when is None:
            raise PipelineRegistrationError("question requires a when predicate")

        def register(func: Callable[..., BaseModel]) -> Callable[..., BaseModel]:
            parameters: tuple[StageParameter, ...] = _parameters_from_function(func)
            self._stages.append(
                _PendingStage(
                    name=func.__name__,
                    output_type=output,
                    when=when,
                    section=section,
                    collapse=collapse,
                    marker=marker,
                    question=question,
                    reads_external=reads_external,
                    func=func,
                    parameters=parameters,
                )
            )
            return func

        return register

    def include(
        self,
        child: Pipeline,
        *,
        output: type[BaseModel],
        name: str,
        when: Callable[..., bool] | None = None,
        section: str | None = None,
    ) -> None:
        _validate_model_type(output, "child output")
        self._stages.append(
            _PendingStage(
                name=name,
                output_type=output,
                when=when,
                section=section,
                child=child,
            )
        )

    def build(self) -> BuiltPipeline:
        stages: list[StageSpec] = []
        for pending in self._stages:
            sub_pipeline: BuiltPipeline | None = None
            parameters: tuple[StageParameter, ...] = pending.parameters
            input_types: tuple[type[BaseModel], ...]
            if pending.child is not None:
                sub_pipeline = pending.child.build()
                if pending.output_type not in sub_pipeline.produced_types:
                    raise InvalidChildOutputError(
                        f"child pipeline for {pending.name!r} does not produce "
                        f"{pending.output_type.__name__}"
                    )
                input_types = sub_pipeline.root_types
                parameters = tuple(
                    StageParameter(name=root.__name__, model_type=root)
                    for root in input_types
                )
            else:
                input_types = tuple(parameter.model_type for parameter in parameters)
            stages.append(
                StageSpec(
                    name=pending.name,
                    input_types=input_types,
                    output_type=pending.output_type,
                    when=pending.when,
                    section=pending.section,
                    collapse=pending.collapse,
                    marker=pending.marker,
                    question=pending.question,
                    reads_external=pending.reads_external,
                    sub_pipeline=sub_pipeline,
                    func=pending.func,
                    parameters=parameters,
                )
            )

        _validate_duplicate_stage_names(stages)
        _validate_duplicate_producers(stages)
        _validate_consumed_types(self.root_types, stages)
        sorted_stages: tuple[StageSpec, ...] = _topological_sort(stages)
        return BuiltPipeline(
            root_types=self.root_types,
            stages=sorted_stages,
            cadence=self.cadence or "",
            produced_types=tuple(stage.output_type for stage in sorted_stages),
        )


def run(
    built: BuiltPipeline,
    roots: dict[type[BaseModel], BaseModel],
    snapshots: SnapshotSinkProtocol | None = None,
) -> RunResult:
    return _run_pipeline(built, roots, snapshots, snapshot_prefix="")


def _run_pipeline(
    built: BuiltPipeline,
    roots: dict[type[BaseModel], BaseModel],
    snapshots: SnapshotSinkProtocol | None,
    *,
    snapshot_prefix: str,
) -> RunResult:
    started: float = time.perf_counter()
    store: dict[type[BaseModel], BaseModel] = _validated_root_store(built, roots)
    records: list[StageRun] = []
    gated_output_types: set[type[BaseModel]] = _gated_duplicate_output_types(
        built.stages
    )

    for stage in built.stages:
        stage_started: float = time.perf_counter()
        gate_result: bool | None = _evaluate_gate(stage, store)
        if gate_result is False:
            records.append(
                _stage_run(stage, StageStatus.SKIPPED, stage_started, "gate was false")
            )
            continue

        missing_input: type[BaseModel] | None = _first_missing_input(stage, store)
        if missing_input is not None:
            records.append(
                _stage_run(
                    stage,
                    StageStatus.SKIPPED_MISSING_INPUT,
                    stage_started,
                    f"missing input {missing_input.__name__}",
                )
            )
            continue

        inputs: dict[type[BaseModel], BaseModel] = _input_store(stage, store)
        if stage.sub_pipeline is not None:
            child_result: RunResult = _run_pipeline(
                stage.sub_pipeline,
                inputs,
                snapshots,
                snapshot_prefix=f"{snapshot_prefix}{stage.name}/",
            )
            if stage.output_type not in child_result.final_store:
                raise MissingChildOutputError(
                    f"child pipeline for {stage.name!r} did not produce "
                    f"{stage.output_type.__name__}"
                )
            output: BaseModel = child_result.final_store[stage.output_type]
            _store_output(stage, output, store, gated_output_types)
            record: StageRun = _stage_run(
                stage,
                StageStatus.SUCCESS,
                stage_started,
                NO_SKIP_REASON,
                sub_runs=child_result.stages,
            )
            records.append(record)
            _snapshot_after_store_update(
                snapshots,
                f"{snapshot_prefix}{stage.name}",
                inputs,
                output,
                records,
                store,
                started,
            )
            continue

        if stage.func is None:
            raise PipelineRunError(f"stage {stage.name!r} has no callable")
        args: dict[str, object] = _stage_call_args(stage, store)
        raw_output: BaseModel = stage.func(**args)
        if not isinstance(raw_output, stage.output_type):
            raise WrongOutputTypeError(
                f"stage {stage.name!r} returned {type(raw_output).__name__}; "
                f"expected {stage.output_type.__name__}"
            )
        _store_output(stage, raw_output, store, gated_output_types)
        records.append(_stage_run(stage, StageStatus.SUCCESS, stage_started))
        _snapshot_after_store_update(
            snapshots,
            f"{snapshot_prefix}{stage.name}",
            inputs,
            raw_output,
            records,
            store,
            started,
        )

    return RunResult(
        stages=tuple(records),
        wall_time_ms=_elapsed_ms(started),
        final_store=dict(store),
    )


def _parameters_from_function(
    func: Callable[..., BaseModel],
) -> tuple[StageParameter, ...]:
    signature: inspect.Signature = inspect.signature(func)
    hints: dict[str, object] = get_type_hints(func)
    parameters: list[StageParameter] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            raise InvalidStageInputError(
                f"stage {func.__name__!r} cannot use variadic parameters"
            )
        annotation: object | None = hints.get(parameter.name)
        if annotation is None:
            raise InvalidStageInputError(
                f"stage {func.__name__!r} parameter {parameter.name!r} "
                "must be annotated"
            )
        parameters.append(_parameter_from_annotation(parameter.name, annotation))
    return tuple(parameters)


def _parameter_from_annotation(name: str, annotation: object) -> StageParameter:
    if _is_model_type(annotation):
        return StageParameter(name=name, model_type=annotation)

    origin: object = get_origin(annotation)
    args: tuple[object, ...] = get_args(annotation)
    if origin is list and len(args) == 1 and _is_model_type(args[0]):
        return StageParameter(name=name, model_type=args[0], container=LIST_CONTAINER)
    if (
        origin is tuple
        and len(args) == 2
        and _is_model_type(args[0])
        and args[1:] == ELLIPSIS_ARGS
    ):
        return StageParameter(name=name, model_type=args[0], container=TUPLE_CONTAINER)
    raise InvalidStageInputError(
        f"parameter {name!r} must be a BaseModel, list[BaseModel], "
        "or tuple[BaseModel, ...]"
    )


def _validate_model_types(types: Iterable[type[BaseModel]], label: str) -> None:
    for model_type in types:
        _validate_model_type(model_type, label)


def _validate_model_type(model_type: object, label: str) -> None:
    if not _is_model_type(model_type):
        raise InvalidStageOutputError(f"{label} must be a BaseModel subclass")


def _is_model_type(value: object) -> bool:
    return inspect.isclass(value) and issubclass(value, BaseModel)


def _validate_duplicate_stage_names(stages: list[StageSpec]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for stage in stages:
        if stage.name in seen:
            duplicates.add(stage.name)
        seen.add(stage.name)
    if duplicates:
        raise DuplicateStageNameError(
            f"duplicate stage names: {', '.join(sorted(duplicates))}"
        )


def _validate_duplicate_producers(stages: list[StageSpec]) -> None:
    producers: dict[type[BaseModel], list[StageSpec]] = defaultdict(list)
    for stage in stages:
        producers[stage.output_type].append(stage)
    for output_type, output_producers in producers.items():
        if len(output_producers) <= 1:
            continue
        if not _has_exclusive_gate_contract(output_producers):
            raise DuplicateProducerError(
                f"duplicate producers for {output_type.__name__}"
            )


def _has_exclusive_gate_contract(stages: list[StageSpec]) -> bool:
    first: StageSpec = stages[0]
    return all(stage.when is not None for stage in stages) and all(
        (
            stage.marker,
            stage.section,
            stage.collapse,
            stage.question,
        )
        == (first.marker, first.section, first.collapse, first.question)
        for stage in stages[1:]
    )


def _validate_consumed_types(
    root_types: tuple[type[BaseModel], ...], stages: list[StageSpec]
) -> None:
    available: set[type[BaseModel]] = set(root_types)
    available.update(stage.output_type for stage in stages)
    for stage in stages:
        for input_type in stage.input_types:
            if input_type not in available:
                raise MissingStageInputError(
                    f"stage {stage.name!r} consumes missing type {input_type.__name__}"
                )


def _topological_sort(stages: list[StageSpec]) -> tuple[StageSpec, ...]:
    stage_count: int = len(stages)
    producers: dict[type[BaseModel], list[int]] = defaultdict(list)
    for index, stage in enumerate(stages):
        producers[stage.output_type].append(index)

    outgoing: dict[int, set[int]] = defaultdict(set)
    indegree: dict[int, int] = {index: 0 for index in range(stage_count)}
    for consumer_index, stage in enumerate(stages):
        dependencies: set[int] = set()
        for input_type in stage.input_types:
            dependencies.update(producers.get(input_type, []))
        dependencies.discard(consumer_index)
        for producer_index in dependencies:
            if consumer_index not in outgoing[producer_index]:
                outgoing[producer_index].add(consumer_index)
                indegree[consumer_index] += 1

    ready: deque[int] = deque(
        index for index in range(stage_count) if indegree[index] == 0
    )
    ordered: list[StageSpec] = []
    while ready:
        index = ready.popleft()
        ordered.append(stages[index])
        for dependent in sorted(outgoing[index]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)

    if len(ordered) != stage_count:
        raise PipelineCycleError("pipeline contains a dependency cycle")
    return tuple(ordered)


def _validated_root_store(
    built: BuiltPipeline,
    roots: dict[type[BaseModel], BaseModel],
) -> dict[type[BaseModel], BaseModel]:
    declared_roots: set[type[BaseModel]] = set(built.root_types)
    for root_type, root_value in roots.items():
        if root_type not in declared_roots:
            raise UnknownRootError(f"unknown root type {root_type.__name__}")
        if not isinstance(root_value, root_type):
            raise WrongRootTypeError(
                f"root {root_type.__name__} received {type(root_value).__name__}"
            )
    for root_type in built.root_types:
        if root_type not in roots:
            raise MissingRootError(f"missing root {root_type.__name__}")
    return dict(roots)


def _evaluate_gate(
    stage: StageSpec, store: dict[type[BaseModel], BaseModel]
) -> bool | None:
    if stage.when is None:
        return None
    gate_args: list[object] | None = _gate_args(stage, store)
    if gate_args is None:
        return None
    return bool(stage.when(*gate_args))


def _gate_args(
    stage: StageSpec, store: dict[type[BaseModel], BaseModel]
) -> list[object] | None:
    if stage.when is None:
        return []
    signature: inspect.Signature = inspect.signature(stage.when)
    if not signature.parameters:
        return []

    hints: dict[str, object] = get_type_hints(stage.when)
    args: list[object] = []
    unannotated_index: int = 0
    for parameter in signature.parameters.values():
        annotation: object | None = hints.get(parameter.name)
        if annotation is not None:
            parsed: StageParameter = _parameter_from_annotation(
                parameter.name, annotation
            )
            if parsed.model_type not in store:
                return None
            args.append(_value_for_parameter(parsed, store))
            continue
        if unannotated_index >= len(stage.parameters):
            return None
        parsed = stage.parameters[unannotated_index]
        unannotated_index += 1
        if parsed.model_type not in store:
            return None
        args.append(_value_for_parameter(parsed, store))
    return args


def _first_missing_input(
    stage: StageSpec, store: dict[type[BaseModel], BaseModel]
) -> type[BaseModel] | None:
    for input_type in stage.input_types:
        if input_type not in store:
            return input_type
    return None


def _input_store(
    stage: StageSpec,
    store: dict[type[BaseModel], BaseModel],
) -> dict[type[BaseModel], BaseModel]:
    return {input_type: store[input_type] for input_type in stage.input_types}


def _stage_call_args(
    stage: StageSpec,
    store: dict[type[BaseModel], BaseModel],
) -> dict[str, object]:
    return {
        parameter.name: _value_for_parameter(parameter, store)
        for parameter in stage.parameters
    }


def _value_for_parameter(
    parameter: StageParameter,
    store: dict[type[BaseModel], BaseModel],
) -> object:
    value: BaseModel = store[parameter.model_type]
    if parameter.container == LIST_CONTAINER:
        return [value]
    if parameter.container == TUPLE_CONTAINER:
        return (value,)
    return value


def _store_output(
    stage: StageSpec,
    output: BaseModel,
    store: dict[type[BaseModel], BaseModel],
    gated_output_types: set[type[BaseModel]],
) -> None:
    if stage.output_type in store:
        if stage.output_type in gated_output_types and stage.when is not None:
            raise GatedProducerConflictError(
                f"gated producer {stage.name!r} also produced "
                f"{stage.output_type.__name__}"
            )
        raise StoreOverwriteError(
            f"stage {stage.name!r} tried to replace {stage.output_type.__name__}"
        )
    store[stage.output_type] = output


def _gated_duplicate_output_types(
    stages: Iterable[StageSpec],
) -> set[type[BaseModel]]:
    counts: dict[type[BaseModel], int] = defaultdict(int)
    for stage in stages:
        if stage.when is not None:
            counts[stage.output_type] += 1
    return {output_type for output_type, count in counts.items() if count > 1}


def _stage_run(
    stage: StageSpec,
    status: StageStatus,
    started: float,
    skip_reason: str | None = None,
    *,
    sub_runs: tuple[StageRun, ...] = (),
) -> StageRun:
    return StageRun(
        name=stage.name,
        status=status,
        elapsed_ms=_elapsed_ms(started),
        output_type_name=stage.output_type.__name__,
        skip_reason=skip_reason,
        sub_runs=sub_runs,
    )


def _snapshot_after_store_update(
    snapshots: SnapshotSinkProtocol | None,
    stage_name: str,
    inputs: dict[type[BaseModel], BaseModel],
    output: BaseModel,
    records: list[StageRun],
    store: dict[type[BaseModel], BaseModel],
    run_started: float,
) -> None:
    if snapshots is None:
        return
    try:
        snapshots.on_stage(stage_name, inputs, output)
    except Exception as error:
        partial_result: RunResult = RunResult(
            stages=tuple(records),
            wall_time_ms=_elapsed_ms(run_started),
            final_store=dict(store),
        )
        raise SnapshotSinkError(stage_name, error, partial_result) from error


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000
