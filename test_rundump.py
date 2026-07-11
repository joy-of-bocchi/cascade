from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from engine import Pipeline, run
from rundump import (
    EMPTY_VALUE,
    ROOT_PRODUCER,
    ROOT_STATUS,
    TRUNCATION_MARK,
    dump_run,
    dump_store,
)

ELAPSED_RE: re.Pattern[str] = re.compile(r"\d+\.\d+ms")


class ToyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Trip(ToyModel):
    distance: float
    duration: float


class Velocity(ToyModel):
    value: float


class ChildOut(ToyModel):
    value: int


class Final(ToyModel):
    value: int


class Root(ToyModel):
    value: int


class Inner(ToyModel):
    value: int


class Wrapper(ToyModel):
    inner: Inner


class Bag(ToyModel):
    items: list[int]


class Note(ToyModel):
    text: str


def always_false() -> bool:
    return False


def _rows(dump: str) -> list[str]:
    return dump.splitlines()


def _row_for(dump: str, key: str) -> str:
    for line in _rows(dump):
        if line.startswith(key + " ") or line == key:
            return line
    raise AssertionError(f"no row for {key!r} in:\n{dump}")


def _velocity_pipeline() -> Pipeline:
    pipeline: Pipeline = Pipeline(root_types=(Trip,))

    @pipeline.stage(output=Velocity)
    def derive_velocity(trip: Trip) -> Velocity:
        return Velocity(value=trip.distance / trip.duration)

    return pipeline


def test_root_fields_show_root_producer() -> None:
    built = _velocity_pipeline().build()
    result = run(built, {Trip: Trip(distance=12450.0, duration=3600.0)})

    dump = dump_run(built, result)

    header = _rows(dump)[0]
    assert header.startswith("KEY")
    assert "PRODUCER" in header and "STATUS" in header

    distance_row = _row_for(dump, "Trip.distance")
    assert ROOT_PRODUCER in distance_row
    assert distance_row.endswith(ROOT_STATUS)
    assert "12450.0" in distance_row

    duration_row = _row_for(dump, "Trip.duration")
    assert ROOT_PRODUCER in duration_row


def test_derived_field_shows_stage_and_success() -> None:
    built = _velocity_pipeline().build()
    result = run(built, {Trip: Trip(distance=12450.0, duration=3600.0)})

    dump = dump_run(built, result)

    velocity_row = _row_for(dump, "Velocity.value")
    assert "derive_velocity" in velocity_row
    assert "SUCCESS" in velocity_row
    assert ELAPSED_RE.search(velocity_row) is not None


def test_skipped_stage_shows_skip_row_with_reason() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Trip,))

    @pipeline.stage(output=Velocity, when=always_false)
    def derive_velocity(trip: Trip) -> Velocity:
        return Velocity(value=trip.distance / trip.duration)

    built = pipeline.build()
    result = run(built, {Trip: Trip(distance=1.0, duration=2.0)})

    dump = dump_run(built, result)

    skip_row = _row_for(dump, "Velocity")
    assert EMPTY_VALUE in skip_row
    assert "SKIPPED" in skip_row
    assert "gate was false" in skip_row
    assert "derive_velocity" in skip_row


def test_nested_include_shows_parent_child_producer() -> None:
    child: Pipeline = Pipeline(root_types=(Root,))

    @child.stage(output=ChildOut)
    def child_stage(root: Root) -> ChildOut:
        return ChildOut(value=root.value + 10)

    parent: Pipeline = Pipeline(root_types=(Root,))
    parent.include(child, output=ChildOut, name="delegate")

    @parent.stage(output=Final)
    def finish(child_out: ChildOut) -> Final:
        return Final(value=child_out.value + 1)

    built = parent.build()
    result = run(built, {Root: Root(value=1)})

    dump = dump_run(built, result)

    child_row = _row_for(dump, "ChildOut.value")
    assert "delegate/child_stage" in child_row
    assert "SUCCESS" in child_row

    final_row = _row_for(dump, "Final.value")
    assert "finish" in final_row


def test_long_value_truncated() -> None:
    pipeline: Pipeline = Pipeline(root_types=(Note,))
    built = pipeline.build()
    long_text = "x" * 100
    result = run(built, {Note: Note(text=long_text)})

    dump = dump_run(built, result)

    note_row = _row_for(dump, "Note.text")
    assert TRUNCATION_MARK in note_row
    assert long_text not in dump


def test_nested_model_field_rendered_as_name_ellipsis() -> None:
    store = {Wrapper: Wrapper(inner=Inner(value=3))}

    dump = dump_store(store)

    inner_row = _row_for(dump, "Wrapper.inner")
    assert f"Inner({TRUNCATION_MARK})" in inner_row


def test_list_field_rendered_as_item_count() -> None:
    store = {Bag: Bag(items=[1, 2, 3])}

    dump = dump_store(store)

    bag_row = _row_for(dump, "Bag.items")
    assert "[3 items]" in bag_row


def test_dump_store_without_run() -> None:
    store = {Trip: Trip(distance=5.0, duration=2.0)}

    dump = dump_store(store)

    header = _rows(dump)[0]
    assert header.startswith("KEY")
    assert "PRODUCER" not in header
    assert "STATUS" not in header

    distance_row = _row_for(dump, "Trip.distance")
    assert "5.0" in distance_row


def test_output_is_deterministic_across_runs() -> None:
    built = _velocity_pipeline().build()
    roots = {Trip: Trip(distance=12450.0, duration=3600.0)}

    first = ELAPSED_RE.sub("<ms>", dump_run(built, run(built, roots)))
    second = ELAPSED_RE.sub("<ms>", dump_run(built, run(built, roots)))

    assert first == second
