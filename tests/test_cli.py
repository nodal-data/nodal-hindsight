import json
from pathlib import Path

from typer.testing import CliRunner

from nodal_hindsight.cli import app
from nodal_hindsight.common import read_json
from nodal_hindsight.schemas import MODELS


def test_documented_workflow(tmp_path):
    runner = CliRunner()

    def invoke(*args, code=0):
        r = runner.invoke(app, list(map(str, args)))
        assert r.exit_code == code, r.output + str(r.exception)
        return r

    dataset = tmp_path / "dataset"
    invoke(
        "validate",
        "--traces",
        "examples/health_tech/decisions.jsonl",
        "--events",
        "examples/health_tech/events.jsonl",
        "--config",
        "examples/health_tech/task.yaml",
    )
    invoke(
        "compile",
        "--traces",
        "examples/health_tech/decisions.jsonl",
        "--events",
        "examples/health_tech/events.jsonl",
        "--config",
        "examples/health_tech/task.yaml",
        "--as-of",
        "2026-10-01T00:00:00Z",
        "--out",
        dataset,
    )
    for name in ["baseline", "candidate"]:
        invoke(
            "run",
            "--dataset",
            dataset,
            "--candidate",
            f"examples.health_tech.candidates:{name}",
            "--run-config",
            f"examples/health_tech/{name}.yaml",
            "--out",
            tmp_path / f"{name}-run",
        )
        invoke(
            "evaluate",
            "--dataset",
            dataset,
            "--run",
            tmp_path / f"{name}-run",
            "--scoring-config",
            "examples/health_tech/scoring.yaml",
            "--out",
            tmp_path / f"{name}-eval",
        )
    invoke(
        "compare",
        "--baseline",
        tmp_path / "baseline-eval",
        "--candidate",
        tmp_path / "candidate-eval",
        "--gates",
        "examples/health_tech/gates.yaml",
        "--out",
        tmp_path / "comparison",
        code=1,
    )
    invoke("export", "--dataset", dataset, "--split", "train", "--out", tmp_path / "export")
    invoke("review", "--dataset", dataset, "--out", tmp_path / "review")
    assert read_json(tmp_path / "comparison/comparison.json")["regressions"]
    invoke("schemas", "--out", tmp_path / "schemas")
    for model in MODELS:
        assert (
            json.loads((tmp_path / f"schemas/{model.__name__}.schema.json").read_text())
            == model.model_json_schema()
        )


def test_generated_schema_consistency():
    for model in MODELS:
        assert (
            read_json(Path("schemas") / f"{model.__name__}.schema.json")
            == model.model_json_schema()
        )
