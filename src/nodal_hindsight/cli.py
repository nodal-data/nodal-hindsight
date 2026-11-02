"""CLI for compilation, execution, scoring, calibration, and review."""

from pathlib import Path
from typing import Annotated
import json

import typer
from pydantic import ValidationError

from nodal_hindsight.common import HindsightError, config_file, write_json
from nodal_hindsight.datasets import compile_dataset, export_dataset
from nodal_hindsight.evaluation import compare_evaluations, evaluate_predictions
from nodal_hindsight.ingest import validate
from nodal_hindsight.judges import calibrate
from nodal_hindsight.review import correction_report
from nodal_hindsight.runners import run_candidate
from nodal_hindsight.schemas import MODELS

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)
P = Annotated[Path, typer.Option()]
S = Annotated[str, typer.Option()]
B = Annotated[bool, typer.Option()]


def show(value):
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False))


def gate_exit(report):
    if report["status"] in ("fail", "insufficient_evidence"):
        raise typer.Exit(1 if report["status"] == "fail" else 3)


@app.command("validate")
def validate_command(traces: P, config: P, events: Annotated[Path | None, typer.Option()] = None):
    result = validate(traces, events, config)
    show(
        {
            "decisions": len(result["decisions"]),
            "events": len(result["events"]),
            "tasks": list(result["tasks"]),
            "status": "valid",
        }
    )


@app.command("compile")
def compile_command(
    traces: P,
    config: P,
    as_of: S,
    out: P,
    events: Annotated[Path | None, typer.Option()] = None,
    quarantine_invalid: B = False,
    overwrite: B = False,
):
    result = compile_dataset(traces, events, config, as_of, out, quarantine_invalid, overwrite)
    show(
        {
            "dataset_id": result["dataset_id"],
            "counts": result["counts"],
            "excluded_count": result["excluded_count"],
        }
    )


@app.command("run")
def run_command(
    dataset: P,
    candidate: S,
    run_config: P,
    out: P,
    split: S = "dev",
    allow_test: B = False,
    resume: B = False,
    overwrite: B = False,
    evaluate: B = False,
    scoring_config: Annotated[Path | None, typer.Option()] = None,
):
    result = run_candidate(
        dataset, split, candidate, config_file(run_config), out, allow_test, resume, overwrite
    )
    show(result)
    if evaluate:
        show(
            evaluate_predictions(
                dataset,
                out.with_name(out.name + "-eval"),
                run=out,
                scoring_config=config_file(scoring_config) if scoring_config else {},
                allow_test=allow_test,
                overwrite=overwrite,
            )
        )


@app.command("evaluate")
def evaluate_command(
    dataset: P,
    out: P,
    split: Annotated[str | None, typer.Option()] = None,
    predictions: Annotated[Path | None, typer.Option()] = None,
    run: Annotated[Path | None, typer.Option()] = None,
    source: Annotated[str | None, typer.Option()] = None,
    scoring_config: Annotated[Path | None, typer.Option()] = None,
    allow_test: B = False,
    overwrite: B = False,
):
    report = evaluate_predictions(
        dataset,
        out,
        split,
        predictions,
        run,
        source,
        config_file(scoring_config) if scoring_config else {},
        allow_test,
        overwrite,
    )
    show({"count": report["count"], "coverage": report["coverage"], "gates": report["gates"]})


@app.command("compare")
def compare_command(baseline: P, candidate: P, gates: P, out: P, overwrite: B = False):
    report = compare_evaluations(baseline, candidate, config_file(gates), out, overwrite)
    show(
        {
            "status": report["status"],
            "failures": report["failures"],
            "insufficient_evidence": report["insufficient_evidence"],
        }
    )
    gate_exit(report)


@app.command("export")
def export_command(
    dataset: P,
    split: S,
    out: P,
    format: S = "jsonl",
    allow_test: B = False,
    input_only: B = False,
    overwrite: B = False,
):
    if format != "jsonl":
        raise HindsightError("only canonical jsonl export is implemented")
    show(export_dataset(dataset, split, out, allow_test, input_only, overwrite))


@app.command("calibrate")
def calibrate_command(
    rubric: P,
    labels: P,
    judge_config: P,
    out: P,
    approved_by: Annotated[str | None, typer.Option()] = None,
    overwrite: B = False,
):
    report = calibrate(
        config_file(rubric), labels, config_file(judge_config), out, approved_by, overwrite
    )
    show(report)
    gate_exit(report)


@app.command("review")
def review_command(
    dataset: P,
    out: P,
    sample_size: Annotated[int, typer.Option(min=1)] = 30,
    seed: Annotated[int, typer.Option()] = 0,
    overwrite: B = False,
):
    show(correction_report(dataset, out, sample_size, seed, overwrite))


@app.command("schemas")
def schemas_command(out: P = Path("schemas")):
    for model in MODELS:
        write_json(out / f"{model.__name__}.schema.json", model.model_json_schema())
    show({"schemas": len(MODELS), "out": str(out)})


def main():
    try:
        app()
    except HindsightError as exc:
        typer.echo(str(exc), err=True)
        raise SystemExit(2) from None
    except (ValidationError, KeyError, TypeError, OSError) as exc:
        # Never print raw input-bearing Pydantic exceptions or application payloads.
        typer.echo(f"Invalid input/configuration or IO failure ({type(exc).__name__})", err=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
