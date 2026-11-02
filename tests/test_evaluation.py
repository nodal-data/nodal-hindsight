import copy

import pytest

from nodal_hindsight.common import HindsightError, read_json, read_jsonl
from nodal_hindsight.datasets import load_dataset
from nodal_hindsight.evaluation import compare_evaluations, evaluate_predictions
from nodal_hindsight.scoring import edit_similarity, score_case, set_scores


def test_metrics_hand_calculated():
    assert edit_similarity("cafe\u0301", "café") == 1
    assert edit_similarity("kitten", "sitting") == pytest.approx(4 / 7)
    assert edit_similarity("", "") == 1
    assert set_scores([], [])["f1"] == 1
    assert set_scores([], ["a"])["precision"] == 0
    assert set_scores(["a", "b"], ["b", "c"])["f1"] == 0.5
    assert set_scores(["a", "b"], ["b", "c"])["jaccard"] == pytest.approx(1 / 3)


def test_cost_direction_and_missing_fields(fixture_data):
    *_, build = fixture_data
    m, cases = load_dataset(build(), "dev")
    c = cases[0]
    task = m["tasks"][c["task"]]
    v = score_case(c, {"next_action": "close_case", "follow_ups": [], "message": None}, task)
    cost = next(x for x in v["metrics"] if x["metric"] == "cost_weighted_match")
    assert cost["value"] == 0 and cost["details"]["cost"] == 5
    omitted = score_case(c, {}, task, prediction_status="error")
    assert not omitted["valid"]
    assert all(x["value"] == 0 for x in omitted["metrics"] if x["eligible"])


def test_reference_applicability_prevents_evasion(fixture_data):
    *_, build = fixture_data
    m, cases = load_dataset(build(), "train")
    c = next(c for c in cases if c["decision_id"].endswith("002"))
    v = score_case(
        c, {"next_action": "wait", "follow_ups": [], "message": None}, m["tasks"][c["task"]]
    )
    edit = next(x for x in v["metrics"] if x["metric"] == "edit_distance")
    assert edit["eligible"] and edit["value"] == 0 and edit["status"] == "scored"


def test_missing_predictions_fail_comparison(fixture_data, tmp_path):
    *_, build = fixture_data
    dataset = build()
    _, cases = load_dataset(dataset, "dev")
    good = [{"case_id": c["case_id"], "output": c["references"]} for c in cases]
    evaluate_predictions(dataset, tmp_path / "good", predictions=good)
    evaluate_predictions(dataset, tmp_path / "missing", predictions=[])
    report = compare_evaluations(
        tmp_path / "good", tmp_path / "missing", {"min_groups": 2}, tmp_path / "compare"
    )
    assert report["status"] == "fail"
    assert "candidate_coverage_below_minimum" in report["failures"]
    assert report["paired_cases"] == 2


def test_duplicate_predictions_and_incompatible_scoring(fixture_data, tmp_path):
    *_, build = fixture_data
    dataset = build()
    _, cases = load_dataset(dataset, "dev")
    p = {"case_id": cases[0]["case_id"], "output": cases[0]["references"]}
    with pytest.raises(HindsightError, match="duplicate"):
        evaluate_predictions(dataset, tmp_path / "dupe", predictions=[p, p])
    evaluate_predictions(dataset, tmp_path / "a", source="logged")
    evaluate_predictions(
        dataset,
        tmp_path / "b",
        source="logged",
        scoring_config={"judge": {"provider": "fixture", "model": "different"}},
    )
    with pytest.raises(HindsightError, match="scoring_identity"):
        compare_evaluations(tmp_path / "a", tmp_path / "b", {}, tmp_path / "compare")


def test_small_cohort_and_seeded_bootstrap(fixture_data, tmp_path):
    *_, build = fixture_data
    dataset = build()
    evaluate_predictions(dataset, tmp_path / "a", source="logged")
    r = compare_evaluations(tmp_path / "a", tmp_path / "a", {"min_groups": 100}, tmp_path / "small")
    assert r["status"] == "insufficient_evidence"
    r = compare_evaluations(tmp_path / "a", tmp_path / "a", {"min_groups": 2}, tmp_path / "same")
    assert all(v["interval"] == [0, 0] for v in r["deltas"].values())


def test_schema_and_timezone_constraints(fixture_data):
    *_, build = fixture_data
    m, cases = load_dataset(build(), "dev")
    c = copy.deepcopy(cases[0])
    t = copy.deepcopy(m["tasks"][c["task"]])
    c["decided_at"] = "2026-09-03T00:30:00Z"  # Wednesday 20:30 in New York
    t["constraints"].append(
        {
            "id": "hours",
            "kind": "business_hours",
            "actions": ["call_customer"],
            "weekdays": [0, 1, 2, 3, 4],
            "start_hour": 9,
            "end_hour": 17,
        }
    )
    v = score_case(c, {"next_action": "call_customer", "follow_ups": [], "message": None}, t)
    assert not next(x for x in v["constraints"] if x["id"] == "hours")["passed"]


def test_logged_baseline_and_reports(fixture_data, tmp_path):
    *_, build = fixture_data
    report = evaluate_predictions(build(), tmp_path / "logged", source="logged")
    assert report["count"] == 2
    assert report["confusion"]
    assert read_json(tmp_path / "logged/evaluation-manifest.json")["source"] == "logged"
    assert len(read_jsonl(tmp_path / "logged/verdicts.jsonl")) == 2


def test_slice_and_resource_gates(fixture_data, tmp_path):
    *_, build = fixture_data
    dataset = build()
    evaluate_predictions(dataset, tmp_path / "eval", source="logged")
    report = compare_evaluations(
        tmp_path / "eval",
        tmp_path / "eval",
        {
            "min_groups": 2,
            "slice_gates": [{"name": "channel", "value": "email", "min_groups": 2}],
            "max_total_cost_usd": 1,
        },
        tmp_path / "comparison",
    )
    assert report["status"] == "insufficient_evidence"
    assert "slice_too_small:channel=email" in report["insufficient_evidence"]
    assert "resource_measurements_missing:max_total_cost_usd" in report["insufficient_evidence"]


def test_composite_and_negative_targets(fixture_data):
    *_, build = fixture_data
    m, cases = load_dataset(build(), "dev")
    case = copy.deepcopy(cases[0])
    task = copy.deepcopy(m["tasks"][case["task"]])
    task["composition"] = {
        "enabled": True,
        "weights": {"next_action/exact_match": 0.7, "follow_ups/set_f1": 0.3},
    }
    case["negatives"] = {"next_action": ["close_case"]}
    task["scorers"].append({"field": "next_action", "metric": "not_in_negatives"})
    v = score_case(case, case["references"], task)
    assert v["score"] == 1
    v = score_case(case, {"next_action": "close_case", "follow_ups": [], "message": None}, task)
    assert v["score"] == 0
    assert next(x for x in v["metrics"] if x["metric"] == "not_in_negatives")["value"] == 0
