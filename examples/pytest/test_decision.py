"""A pytest runner can call the same pure scorer; use your own compiled bundle."""

import os
from pathlib import Path

import pytest

from nodal_hindsight.datasets import load_dataset
from nodal_hindsight.scoring import score_case
from examples.health_tech.candidates import baseline
from nodal_hindsight.schemas import RunContext


def test_application_against_compiled_cases():
    dataset = Path(os.environ.get("HINDSIGHT_EXAMPLE_DATASET", "artifacts/dataset"))
    if not dataset.exists():
        pytest.skip("Compile the Health Tech dataset first")
    manifest, cases = load_dataset(dataset, "dev")
    verdicts = []
    for case in cases:
        response = baseline(
            case["input"],
            RunContext(
                case_id=case["case_id"], timeout_seconds=10, deadline="2099-01-01T00:00:00Z"
            ),
        )
        verdicts.append(score_case(case, response.output, manifest["tasks"][case["task"]]))
    assert verdicts
    assert all(v["valid"] for v in verdicts)
    # Suite-level quality check. Production should compare a persisted baseline too.
    assert not any(v["gate"] == "fail" for v in verdicts)
