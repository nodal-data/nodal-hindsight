import copy
import json

import pytest

from nodal_hindsight.common import HindsightError, read_json, read_jsonl
from nodal_hindsight.datasets import export_dataset, load_dataset


def test_labels_boundaries_and_reproducibility(fixture_data):
    _, _, _, build = fixture_data
    first, second = build(name="a"), build(name="b")
    assert read_json(first / "manifest.json") == read_json(second / "manifest.json")
    cases = {c["decision_id"][-3:]: c for c in read_jsonl(first / "cases.jsonl")}
    assert cases["001"]["labels"]["next_action"]["tier"] == "Weak"
    assert cases["002"]["labels"]["message"]["tier"] == "Bronze"
    assert cases["002"]["labels"]["next_action"]["tier"] == "Weak"
    assert cases["003"]["labels"]["next_action"]["tier"] == "Gold"
    assert cases["004"]["labels"]["next_action"]["tier"] == "Silver"
    assert cases["005"]["labels"]["next_action"]["tier"] == "Silver"
    assert cases["006"]["negatives"] == {}
    assert cases["006"]["outcome_status"] == "negative_evidence"
    assert cases["007"]["labels"]["next_action"]["tier"] == "Gold"
    assert cases["007"]["labels"]["message"]["tier"] == "Weak"
    assert cases["008"]["outcome_status"] == "censored"
    assert cases["009"]["labels"]["next_action"]["tier"] == "Silver"
    assert "outcome-009" not in cases["009"]["provenance"]["evidence_ids"]
    for item in read_jsonl(first / "inputs/train.jsonl"):
        assert set(item) == {"case_id", "input"}
        assert "suggestion" not in item["input"]


def test_duplicate_ids_and_conflicting_payloads(fixture_data):
    d, e, t, build = fixture_data
    p = build(decisions_override=d + [d[0]], events_override=e + [e[0]])
    assert len(read_jsonl(p / "cases.jsonl")) == len(d)
    changed = copy.deepcopy(d[0])
    changed["suggestion"]["message"] = "Changed"
    with pytest.raises(HindsightError, match="conflicting"):
        build(decisions_override=d + [changed], name="conflict")


@pytest.mark.parametrize(
    "mutation",
    ["future_history", "future_snapshot", "naive_time", "unknown_field", "unknown_action"],
)
def test_bad_decisions(fixture_data, mutation):
    d, e, t, build = fixture_data
    if mutation == "future_history":
        d[0]["context"]["history"][0]["at"] = "2027-01-01T00:00:00Z"
    if mutation == "future_snapshot":
        d[0]["context_provenance"]["snapshot_at"] = "2027-01-01T00:00:00Z"
    if mutation == "naive_time":
        d[0]["decided_at"] = "2026-08-04T15:00:00"
    if mutation == "unknown_field":
        d[0]["oops"] = "value"
    if mutation == "unknown_action":
        d[0]["suggestion"]["next_action"] = "not_an_action"
    with pytest.raises(HindsightError):
        build()


def test_event_join_and_chronology(fixture_data):
    d, e, t, build = fixture_data
    e[0]["group_id"] = "wrong"
    with pytest.raises(HindsightError, match="join"):
        build()


def test_partition_group_and_duplicate_exclusions(fixture_data):
    d, e, t, build = fixture_data
    # Same group in training and dev; both are removed.
    dev = next(x for x in d if x["decision_id"].endswith("004"))
    dev["group_id"] = d[0]["group_id"]
    for event in e:
        if event["decision_id"] == dev["decision_id"]:
            event["group_id"] = dev["group_id"]
    p = build()
    report = read_json(p / "quality-report.json")
    assert sum(x["reason"] == "group_crosses_split_boundary" for x in report["excluded"]) == 2


def test_duplicate_context_and_embargo(fixture_data):
    d, e, t, build = fixture_data
    dev = next(x for x in d if x["decision_id"].endswith("004"))
    dev["context"] = copy.deepcopy(d[0]["context"])
    p = build()
    assert any(
        x["reason"] == "cross_split_duplicate_input"
        for x in read_json(p / "quality-report.json")["excluded"]
    )
    t[0]["split_policy"]["embargo_days"] = 1
    p = build(name="embargo")
    assert any(
        x["reason"] == "embargo_overlap" for x in read_json(p / "quality-report.json")["excluded"]
    )


def test_conflicts_partial_actions_and_reviews(fixture_data):
    d, e, t, build = fixture_data
    h = copy.deepcopy(next(x for x in e if x["event_id"] == "human-004"))
    h.update(event_id="conflict-004", output={"next_action": "wait"})
    e.append(h)
    p = build(name="conflicted")
    c = next(x for x in read_jsonl(p / "cases.jsonl") if x["decision_id"].endswith("004"))
    assert c["labels"]["next_action"]["tier"] == "Unknown"
    e.append(
        {
            "schema_version": "1.0",
            "record_type": "review",
            "event_id": "review-004",
            "decision_id": h["decision_id"],
            "group_id": h["group_id"],
            "event_at": "2026-09-05T10:00:00Z",
            "observed_at": "2026-09-05T10:00:01Z",
            "reviewer": "expert",
            "fields": ["next_action"],
            "disposition": "approve",
            "references": {"next_action": "wait"},
            "reason": "Reviewed independently",
        }
    )
    p2 = build(name="reviewed")
    c2 = next(x for x in read_jsonl(p2 / "cases.jsonl") if x["decision_id"] == c["decision_id"])
    assert c2["case_id"] == c["case_id"] and c2["version"] != c["version"]
    assert c2["labels"]["next_action"]["tier"] == "Gold"


def test_negative_review_and_export(fixture_data, tmp_path):
    d, e, t, build = fixture_data
    h = next(x for x in e if x["event_id"] == "human-004")
    e.append(
        {
            "schema_version": "1.0",
            "record_type": "review",
            "event_id": "negative-004",
            "decision_id": h["decision_id"],
            "group_id": h["group_id"],
            "event_at": "2026-09-05T10:00:00Z",
            "observed_at": "2026-09-05T10:00:01Z",
            "reviewer": "expert",
            "fields": ["next_action"],
            "disposition": "negative",
            "negatives": {"next_action": ["close_case"]},
            "reason": "Unresolved request",
        }
    )
    dataset = build()
    export_dataset(dataset, "train", tmp_path / "export")
    exported = read_jsonl(tmp_path / "export/cases.jsonl")
    assert all(all(c["labels"][f]["eligible"] for f in c["references"]) for c in exported)
    with pytest.raises(HindsightError, match="allow-test"):
        export_dataset(dataset, "test", tmp_path / "test-export")
    export_dataset(dataset, "dev", tmp_path / "inputs", input_only=True)
    assert all(set(x) == {"case_id", "input"} for x in read_jsonl(tmp_path / "inputs/inputs.jsonl"))


def test_redaction_and_integrity(fixture_data, tmp_path, monkeypatch):
    d, e, t, build = fixture_data
    monkeypatch.setenv("HINDSIGHT_REDACTION_KEY", "test-only-secret")
    t[0]["redaction"] = {
        "version": "1",
        "pseudonymize": ["context.customer_profile.customer_id"],
        "event_drop": ["reason"],
    }
    p = build()
    c = read_jsonl(p / "cases.jsonl")[0]
    assert (
        c["input"]["customer_profile"]["customer_id"]
        != d[0]["context"]["customer_profile"]["customer_id"]
    )
    data = (p / "cases.jsonl").read_text().replace("customer_id", "changed_key")
    (p / "cases.jsonl").write_text(data)
    with pytest.raises(HindsightError, match="integrity"):
        load_dataset(p)


def test_generic_task(fixture_data):
    d, e, t, build = fixture_data
    # Completely different field names and input shape; no Health Tech-specific action field required.
    task = t[0]
    task["task"] = "routing.category"
    task["field_roles"] = {"category": "categorical"}
    task["input_schema"] = {
        "type": "object",
        "properties": {"request": {"type": "string"}},
        "required": ["request"],
    }
    task["output_schema"] = {
        "type": "object",
        "properties": {"category": {"enum": ["a", "b"]}},
        "required": ["category"],
        "additionalProperties": False,
    }
    task["scorers"] = [{"field": "category", "metric": "exact_match"}]
    task["constraints"] = []
    task["rubrics"] = []
    task["outcome_policy"] = {}
    task["context_rules"] = {}
    decision = d[0]
    decision["task"] = task["task"]
    decision["context"] = {"request": "Route this"}
    decision["context_provenance"].pop("history_timestamp_path")
    decision["suggestion"] = {"category": "a"}
    event = copy.deepcopy(e[0])
    event["output"] = {"category": "b"}
    event["kind"] = "overridden"
    p = build(decisions_override=[decision], events_override=[event])
    c = read_jsonl(p / "cases.jsonl")[0]
    assert c["references"] == {"category": "b"}
    assert c["labels"]["category"]["tier"] == "Silver"
    assert json.loads((p / "manifest.json").read_text())["counts"] == {"train": 1}


def test_configuration_errors_are_not_ignored(fixture_data):
    d, e, t, build = fixture_data
    t[0]["scorers"][0]["typo_threshold"] = 0.8
    with pytest.raises(HindsightError, match="configuration keys"):
        build()


def test_quarantine_and_calibration_partition(fixture_data):
    d, e, t, build = fixture_data
    d[0]["unexpected"] = "bad"
    p = build(events_override=[], quarantine_invalid=True)
    assert len(read_jsonl(p / "cases.jsonl")) == len(d) - 1
    assert read_json(p / "quality-report.json")["excluded"]
    d[0].pop("unexpected")
    t[0]["split_policy"]["calibration_groups"] = [d[0]["group_id"]]
    p = build(name="calibration")
    m, cases = load_dataset(p, "calibration")
    assert len(cases) == 1
    assert cases[0]["group_id"] == d[0]["group_id"]
