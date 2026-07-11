from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from engine import BuiltPipeline, RunResult, StageRun, StageSpec, StageStatus

MAX_VALUE_WIDTH: int = 40
TRUNCATION_MARK: str = "…"
ROOT_PRODUCER: str = "<root>"
ROOT_STATUS: str = "-"
EMPTY_VALUE: str = "—"
COLUMN_GAP: str = "  "
NESTED_PATH_SEPARATOR: str = "/"
KEY_SEPARATOR: str = "."
EMPTY_CELL: str = ""
ELAPSED_DECIMALS: int = 1
HEADER_KEY: str = "KEY"
HEADER_VALUE: str = "VALUE"
HEADER_PRODUCER: str = "PRODUCER"
HEADER_STATUS: str = "STATUS"


class _Producer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    run: StageRun


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    value: str
    producer: str
    status: str


def dump_run(built: BuiltPipeline, result: RunResult) -> str:
    producers: dict[type[BaseModel], _Producer] = _leaf_producers(
        built.stages, result.stages, EMPTY_CELL
    )
    rows: list[_Row] = []
    for root_type in built.root_types:
        entity: BaseModel | None = result.final_store.get(root_type)
        if entity is None:
            continue
        rows.extend(_field_rows(root_type, entity, ROOT_PRODUCER, ROOT_STATUS))

    for stage, stage_run in zip(built.stages, result.stages):
        if stage_run.status is StageStatus.SUCCESS:
            produced: BaseModel | None = result.final_store.get(stage.output_type)
            if produced is None:
                continue
            producer: _Producer | None = producers.get(stage.output_type)
            path: str = producer.path if producer is not None else stage.name
            status: str = _status_text(
                producer.run if producer is not None else stage_run
            )
            rows.extend(_field_rows(stage.output_type, produced, path, status))
            continue
        rows.append(
            _Row(
                key=stage.output_type.__name__,
                value=EMPTY_VALUE,
                producer=stage.name,
                status=_skip_status_text(stage_run),
            )
        )

    return _render_table(rows, include_producer=True)


def dump_store(store: dict[type[BaseModel], BaseModel]) -> str:
    rows: list[_Row] = []
    for model_type, entity in store.items():
        rows.extend(_field_rows(model_type, entity, EMPTY_CELL, EMPTY_CELL))
    return _render_table(rows, include_producer=False)


def _leaf_producers(
    stages: tuple[StageSpec, ...],
    runs: tuple[StageRun, ...],
    prefix: str,
) -> dict[type[BaseModel], _Producer]:
    # `runs` mirrors `stages` one-to-one (the engine appends exactly one StageRun
    # per stage, in order), and StageRun.sub_runs mirrors sub_pipeline.stages the
    # same way. We therefore match each produced type on the actual output_type of
    # the zipped StageSpec rather than on StageRun.output_type_name (the name-only
    # fallback), which lets us recurse into included sub-pipelines and report the
    # real leaf producer path (e.g. "parent_stage/child_stage").
    producers: dict[type[BaseModel], _Producer] = {}
    for stage, stage_run in zip(stages, runs):
        if stage_run.status is not StageStatus.SUCCESS:
            continue
        if stage.sub_pipeline is not None:
            child_prefix: str = f"{prefix}{stage.name}{NESTED_PATH_SEPARATOR}"
            producers.update(
                _leaf_producers(
                    stage.sub_pipeline.stages, stage_run.sub_runs, child_prefix
                )
            )
            continue
        producers[stage.output_type] = _Producer(
            path=f"{prefix}{stage.name}", run=stage_run
        )
    return producers


def _field_rows(
    model_type: type[BaseModel],
    entity: BaseModel,
    producer: str,
    status: str,
) -> list[_Row]:
    rows: list[_Row] = []
    for field_name in model_type.model_fields:
        rows.append(
            _Row(
                key=f"{model_type.__name__}{KEY_SEPARATOR}{field_name}",
                value=_render_value(getattr(entity, field_name)),
                producer=producer,
                status=status,
            )
        )
    return rows


def _render_value(value: object) -> str:
    if isinstance(value, BaseModel):
        return f"{type(value).__name__}({TRUNCATION_MARK})"
    if isinstance(value, (list, tuple)):
        return f"[{len(value)} items]"
    return _truncate(repr(value))


def _truncate(text: str) -> str:
    if len(text) <= MAX_VALUE_WIDTH:
        return text
    return text[: MAX_VALUE_WIDTH - 1] + TRUNCATION_MARK


def _status_text(run: StageRun) -> str:
    return f"{run.status.value} {run.elapsed_ms:.{ELAPSED_DECIMALS}f}ms"


def _skip_status_text(run: StageRun) -> str:
    if run.skip_reason is None:
        return run.status.value
    return f"{run.status.value} {run.skip_reason}"


def _render_table(rows: list[_Row], *, include_producer: bool) -> str:
    if include_producer:
        headers: tuple[str, ...] = (
            HEADER_KEY,
            HEADER_VALUE,
            HEADER_PRODUCER,
            HEADER_STATUS,
        )
        table_rows: list[tuple[str, ...]] = [
            (row.key, row.value, row.producer, row.status) for row in rows
        ]
    else:
        headers = (HEADER_KEY, HEADER_VALUE)
        table_rows = [(row.key, row.value) for row in rows]

    all_rows: list[tuple[str, ...]] = [headers, *table_rows]
    widths: tuple[int, ...] = _column_widths(all_rows)
    return "\n".join(_format_row(cells, widths) for cells in all_rows)


def _column_widths(rows: list[tuple[str, ...]]) -> tuple[int, ...]:
    if not rows:
        return ()
    widths: list[int] = [0] * len(rows[0])
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    return tuple(widths)


def _format_row(cells: tuple[str, ...], widths: tuple[int, ...]) -> str:
    padded: list[str] = [cell.ljust(width) for cell, width in zip(cells, widths)]
    return COLUMN_GAP.join(padded).rstrip()
