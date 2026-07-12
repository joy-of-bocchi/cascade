from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from cascade.engine import BuiltPipeline, RunResult, StageStatus, run
from cascade.lint import carrylint, decllint, derivlint
from demo.trips import generate
from demo.trips.models import ApprovalPacket, Invoice, Leg, Summary, Trip
from demo.trips.pipeline import build_demo, demo_roots, needs_manual_approval

TRIPS_DIR: Path = Path("demo/trips")
OUT_DIR: Path = TRIPS_DIR / "out"


def test_committed_artifacts_match_regenerated_content() -> None:
    generated: dict[str, str] = generate.build_artifacts()

    for name, content in generated.items():
        assert (OUT_DIR / name).read_text(encoding="utf-8") == content


def test_demo_run_records_expected_gated_statuses() -> None:
    built: BuiltPipeline = build_demo()
    roots: dict[type[BaseModel], BaseModel] = demo_roots()
    result: RunResult = run(built, roots)
    statuses: dict[str, StageStatus] = {
        stage.name: stage.status for stage in result.stages
    }
    manual_gate_expected: StageStatus = (
        StageStatus.SUCCESS
        if needs_manual_approval(roots[Trip])
        else StageStatus.SKIPPED
    )

    assert statuses["summarize_trip"] is StageStatus.SUCCESS
    assert statuses["prepare_manual_approval"] is manual_gate_expected
    assert statuses["prepare_auto_approval"] is StageStatus.SUCCESS
    assert statuses["create_invoice"] is StageStatus.SUCCESS
    assert ApprovalPacket in result.final_store
    assert Invoice in result.final_store


def test_demo_lint_surface_is_clean() -> None:
    paths: list[str] = [str(path) for path in sorted(TRIPS_DIR.glob("*.py"))]
    models: list[type[BaseModel]] = [Leg, Trip, Summary, ApprovalPacket, Invoice]
    model_names: set[str] = {model.__name__ for model in models}

    carry_violations: list[carrylint.Violation] = carrylint.check_paths(
        paths, model_names
    )
    deriv_violations: list[derivlint.Violation] = derivlint.check_paths(
        paths, model_names
    )
    decl_violations: list[decllint.DeclViolation] = decllint.check_single_declaration(
        models
    )

    assert not [violation for violation in carry_violations if violation.blocking]
    assert carry_violations == []
    assert not [
        violation
        for violation in deriv_violations
        if violation.kind in derivlint.BLOCKING_KINDS
    ]
    assert deriv_violations == []
    assert decl_violations == []
