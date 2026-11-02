#!/usr/bin/env python3
"""Run the complete credential-free demo. Existing outputs require --overwrite."""

import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("artifacts/demo"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.out.resolve()

    def command(*parts, expected=0, writes=True):
        argv = [sys.executable, "-m", "nodal_hindsight.cli", *map(str, parts)]
        if writes and args.overwrite:
            argv.append("--overwrite")
        result = subprocess.run(argv, cwd=root)
        if result.returncode != expected:
            raise SystemExit(f"{parts[0]} returned {result.returncode}; expected {expected}")

    command(
        "validate",
        "--traces",
        "examples/health_tech/decisions.jsonl",
        "--events",
        "examples/health_tech/events.jsonl",
        "--config",
        "examples/health_tech/task.yaml",
        writes=False,
    )
    for name, cutoff in [
        ("dataset", "2026-10-01T00:00:00Z"),
        ("dataset-earlier", "2026-09-20T00:00:00Z"),
    ]:
        command(
            "compile",
            "--traces",
            "examples/health_tech/decisions.jsonl",
            "--events",
            "examples/health_tech/events.jsonl",
            "--config",
            "examples/health_tech/task.yaml",
            "--as-of",
            cutoff,
            "--out",
            output / name,
        )
    for name in ("baseline", "candidate"):
        command(
            "run",
            "--dataset",
            output / "dataset",
            "--split",
            "dev",
            "--candidate",
            f"examples.health_tech.candidates:{name}",
            "--run-config",
            f"examples/health_tech/{name}.yaml",
            "--out",
            output / f"{name}-run",
        )
        command(
            "evaluate",
            "--dataset",
            output / "dataset",
            "--run",
            output / f"{name}-run",
            "--scoring-config",
            "examples/health_tech/scoring.yaml",
            "--out",
            output / f"{name}-eval",
        )
    command(
        "evaluate",
        "--dataset",
        output / "dataset",
        "--source",
        "logged",
        "--split",
        "train",
        "--scoring-config",
        "examples/health_tech/scoring.yaml",
        "--out",
        output / "logged-train-eval",
    )
    command(
        "compare",
        "--baseline",
        output / "baseline-eval",
        "--candidate",
        output / "candidate-eval",
        "--gates",
        "examples/health_tech/gates.yaml",
        "--out",
        output / "comparison",
        expected=1,
    )
    command(
        "export",
        "--dataset",
        output / "dataset",
        "--split",
        "train",
        "--out",
        output / "train-export",
    )
    command("review", "--dataset", output / "dataset", "--out", output / "review")
    command(
        "calibrate",
        "--rubric",
        "examples/health_tech/calibration-rubric.yaml",
        "--labels",
        "examples/health_tech/calibration-labels.jsonl",
        "--judge-config",
        "examples/health_tech/calibration-judge.yaml",
        "--out",
        output / "calibration",
        expected=3,
    )
    print(
        f"Demo complete. The intentional candidate regression failed as expected. Reports: {output}"
    )


if __name__ == "__main__":
    main()
