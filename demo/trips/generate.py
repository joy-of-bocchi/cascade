from __future__ import annotations

from pathlib import Path

from cascade.engine import BuiltPipeline, RunResult, dump_run, run
from cascade.engine.engine import StageRun
from cascade.render import render
from cascade.render.wrapmmd import wrap
from cascade.spec import (
    AuthoredExtra,
    DecisionOverlay,
    DiagramSpec,
    Overlay,
    TypeOverlay,
    fragment_from_pipeline,
    merge,
)
from cascade.vocab import render_tsv, vocabulary_from_pipeline

from .pipeline import build_demo, demo_roots

OUT_DIR: Path = Path(__file__).resolve().parent / "out"
PIPELINE_ID: str = "trips"
PIPELINE_MMD: str = "pipeline.mmd"
PIPELINE_VIEW: str = "pipeline.view.html"
VOCABULARY_TSV: str = "vocabulary.tsv"
RUNDUMP_TXT: str = "rundump.txt"


def build_spec() -> DiagramSpec:
    built: BuiltPipeline = build_demo()
    return merge(
        [fragment_from_pipeline(built, PIPELINE_ID)],
        overlay=Overlay(
            types={
                "Summary": TypeOverlay(
                    prose="Aggregates the trip legs before approval and billing."
                )
            },
            decisions={
                "decision_ApprovalPacket": DecisionOverlay(
                    question="Does this trip need manual approval?"
                )
            },
        ),
        extra=AuthoredExtra(),
    )


def _zero_stage_run(stage_run: StageRun) -> StageRun:
    return stage_run.model_copy(
        update={
            "elapsed_ms": 0.0,
            "sub_runs": tuple(_zero_stage_run(child) for child in stage_run.sub_runs),
        }
    )


def _zero_elapsed(result: RunResult) -> RunResult:
    return result.model_copy(
        update={
            "wall_time_ms": 0.0,
            "stages": tuple(_zero_stage_run(stage) for stage in result.stages),
        }
    )


def build_artifacts() -> dict[str, str]:
    built: BuiltPipeline = build_demo()
    mermaid_text: str = render(build_spec())
    result: RunResult = _zero_elapsed(run(built, demo_roots()))
    artifacts: dict[str, str] = {
        PIPELINE_MMD: mermaid_text,
        PIPELINE_VIEW: wrap("pipeline", mermaid_text),
        VOCABULARY_TSV: render_tsv(vocabulary_from_pipeline(built)),
        RUNDUMP_TXT: dump_run(built, result) + "\n",
    }
    return artifacts


def write_artifacts(out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    name: str
    content: str
    for name, content in build_artifacts().items():
        path: Path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main() -> int:
    path: Path
    for path in write_artifacts():
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
