"""Stored-output evaluation and matched-cohort comparisons."""

from collections import Counter, defaultdict
from pathlib import Path
import random
import statistics

from nodal_hindsight.common import (
    HindsightError,
    artifact_dir,
    digest,
    markdown_report,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
)
from nodal_hindsight.datasets import load_dataset
from nodal_hindsight.ingest import parse_model
from nodal_hindsight.ingest.configuration import validate_gates
from nodal_hindsight.judges import Judge, judge_identity
from nodal_hindsight.schemas import Prediction
from nodal_hindsight.scoring import score_case


def summarize(verdicts):
    metrics, slices, confusion = defaultdict(list), defaultdict(list), Counter()
    for verdict in verdicts:
        for metric in verdict["metrics"]:
            key = f"{metric['field']}/{metric['metric']}"
            metrics[key].append(metric)
            for name, value in {
                **verdict["tags"],
                "exposure": verdict["exposure"],
                "tier": verdict["labels"][metric["field"]]["tier"],
            }.items():
                slices[f"{name}={value}/{key}"].append(metric)
            if (
                metric["metric"] == "exact_match"
                and metric["eligible"]
                and metric["status"] == "scored"
            ):
                detail = metric["details"]
                confusion[
                    (metric["field"], str(detail.get("reference")), str(detail.get("prediction")))
                ] += 1

    def aggregate(rows):
        eligible = [m for m in rows if m["eligible"]]
        scored = [m for m in eligible if m["status"] == "scored"]
        info = {
            "eligible": len(eligible),
            "scored": len(scored),
            "mean": statistics.mean(m["value"] for m in scored) if scored else None,
            "errors": sum(m["status"] == "error" for m in rows),
            "inapplicable": sum(m["status"] == "inapplicable" for m in rows),
            "experimental_mean": statistics.mean(
                m["value"] for m in rows if m["status"] == "scored"
            )
            if any(m["status"] == "scored" for m in rows) and not scored
            else None,
        }
        sets = [m for m in scored if "tp" in m["details"]]
        if sets:
            tp, fp, fn = (sum(m["details"][k] for m in sets) for k in ("tp", "fp", "fn"))
            info["micro"] = {
                "precision": tp / (tp + fp) if tp + fp else float(fn == 0),
                "recall": tp / (tp + fn) if tp + fn else float(fp == 0),
                "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 1.0,
            }
        costs = [m["details"]["cost"] for m in scored if "cost" in m["details"]]
        if costs:
            info["mean_cost"] = statistics.mean(costs)
        return info

    composite_values = [v["score"] for v in verdicts if v["score"] is not None]
    latencies = [
        v["resources"]["latency_seconds"]
        for v in verdicts
        if v.get("resources", {}).get("latency_seconds") is not None
    ]
    costs = [
        v["resources"]["cost_usd"]
        for v in verdicts
        if v.get("resources", {}).get("cost_usd") is not None
    ]
    return {
        "composite": {
            "scored": len(composite_values),
            "mean": statistics.mean(composite_values) if composite_values else None,
        },
        "resources": {
            "latency_count": len(latencies),
            "mean_latency_seconds": statistics.mean(latencies) if latencies else None,
            "cost_count": len(costs),
            "total_cost_usd": sum(costs) if costs else None,
        },
        "count": len(verdicts),
        "valid": sum(v["valid"] for v in verdicts),
        "coverage": sum(v["prediction_status"] == "success" for v in verdicts) / len(verdicts)
        if verdicts
        else 0,
        "validity_rate": sum(v["valid"] for v in verdicts) / len(verdicts) if verdicts else 0,
        "gates": dict(Counter(v["gate"] for v in verdicts)),
        "metrics": {k: aggregate(v) for k, v in metrics.items()},
        "slices": {k: aggregate(v) for k, v in slices.items()},
        "confusion": [
            {"field": f, "reference": r, "prediction": p, "count": n}
            for (f, r, p), n in confusion.items()
        ],
    }


def evaluate_predictions(
    dataset,
    out,
    split=None,
    predictions=None,
    run=None,
    source=None,
    scoring_config=None,
    allow_test=False,
    overwrite=False,
):
    if sum(x is not None for x in (predictions, run, source)) != 1:
        raise HindsightError("choose exactly one of predictions, run, or source logged")
    cfg = scoring_config or {}
    if set(cfg) - {"judge"}:
        raise HindsightError(
            "scoring configuration supports judge; task metrics are frozen in dataset"
        )
    run_manifest = None
    if run:
        run_manifest = read_json(Path(run) / "run-manifest.json")
        if run_manifest.get("status") != "complete":
            raise HindsightError("cannot evaluate an incomplete run")
        if split and split != run_manifest["split"]:
            raise HindsightError("run split mismatch")
        split = run_manifest["split"]
        raw = read_jsonl(Path(run) / "predictions.jsonl")
        if digest(raw) != run_manifest["prediction_hash"]:
            raise HindsightError("prediction integrity mismatch")
    elif predictions is not None:
        raw = read_jsonl(predictions) if isinstance(predictions, (str, Path)) else predictions
    else:
        if source != "logged":
            raise HindsightError("only source logged is supported")
        raw = None
    split = split or "dev"
    manifest, cases = load_dataset(dataset, split, allow_test)
    if run_manifest and run_manifest["dataset_id"] != manifest["dataset_id"]:
        raise HindsightError("run dataset mismatch")
    if raw is None:
        raw = [
            {
                "case_id": c["case_id"],
                "output": c["suggestion"],
                "candidate": c["provenance"]["policy"],
            }
            for c in cases
        ]
    allowed = {c["case_id"] for c in cases}
    by_id = {}
    for item in raw:
        p = parse_model(Prediction, item)
        if p["case_id"] not in allowed or p["case_id"] in by_id:
            raise HindsightError("duplicate or unknown prediction case_id")
        by_id[p["case_id"]] = p
    judge = Judge(cfg["judge"]) if cfg.get("judge") else None
    if judge:
        for file in cfg["judge"].get("approval_files", []):
            approval = read_json(file)
            if set(approval.get("group_ids", [])) & {c["group_id"] for c in cases}:
                raise HindsightError("calibration groups overlap evaluation cohort")
    verdicts = []
    for case in cases:
        p = by_id.get(case["case_id"], {"output": None, "status": "error"})
        verdict = score_case(case, p["output"], manifest["tasks"][case["task"]], judge, p["status"])
        attempts = p.get("attempts", [])
        cost = p.get("usage", {}).get("cost_usd")
        if attempts:
            costs = [a.get("usage", {}).get("cost_usd") for a in attempts]
            cost = sum(costs) if all(v is not None for v in costs) else None
        verdict["resources"] = {
            "latency_seconds": p.get("latency_seconds"),
            "cost_usd": cost,
            "cache_hit": p.get("cache_hit", False),
        }
        verdicts.append(verdict)
    report = summarize(verdicts)
    approvals = [digest(read_json(f)) for f in cfg.get("judge", {}).get("approval_files", [])]
    scoring_identity = digest(
        {
            "tasks": manifest["tasks"],
            "judge": judge_identity(cfg.get("judge", {})),
            "approvals": approvals,
            "scorer_version": "0.1.0",
        }
    )
    em = {
        "dataset_id": manifest["dataset_id"],
        "split": split,
        "test_access": split == "test",
        "scoring_identity": scoring_identity,
        "case_revisions": {c["case_id"]: c["version"] for c in cases},
        "prediction_hash": digest(raw),
        "verdicts_hash": digest(verdicts),
        "source": source or "run" if run else source or "predictions",
        "policy_identities": sorted({digest(p.get("candidate", {})) for p in raw}),
        "judge_calls": judge.calls if judge else 0,
    }
    em["evaluation_id"] = digest(em)
    report["mixed_policy_cohort"] = len(em["policy_identities"]) > 1
    with artifact_dir(out, overwrite) as dest:
        write_json(dest / "evaluation-manifest.json", em)
        write_jsonl(dest / "verdicts.jsonl", verdicts)
        write_jsonl(dest / "judge-responses.jsonl", judge.records if judge else [])
        write_json(dest / "report.json", report)
        (dest / "report.md").write_text(markdown_report("Evaluation", report))
    return report


def load_evaluation(path):
    m = read_json(Path(path) / "evaluation-manifest.json")
    v = read_jsonl(Path(path) / "verdicts.jsonl")
    if digest(v) != m["verdicts_hash"]:
        raise HindsightError("evaluation verdict integrity mismatch")
    return m, {x["case_id"]: x for x in v}


def bootstrap(groups, samples, seed):
    rng = random.Random(seed)
    keys = sorted(groups)
    values = []
    for _ in range(samples):
        draws = [value for _ in keys for value in groups[rng.choice(keys)]]
        values.append(statistics.mean(draws))
    values.sort()
    return [values[int(0.025 * (samples - 1))], values[int(0.975 * (samples - 1))]]


def compare_evaluations(baseline, candidate, gates, out, overwrite=False):
    validate_gates(gates)
    bm, bv = load_evaluation(baseline)
    cm, cv = load_evaluation(candidate)
    for key in ("dataset_id", "split", "scoring_identity", "case_revisions"):
        if bm[key] != cm[key]:
            raise HindsightError(f"incompatible evaluation {key}; reevaluate explicitly")
    if set(bv) != set(cv):
        raise HindsightError("comparison cohorts differ")
    if gates.get("bootstrap_samples", 1000) < 10 or gates.get("min_groups", 2) < 1:
        raise HindsightError("invalid comparison sample/group limits")
    regressions, fixes, still_wrong, unresolved = [], [], [], []
    deltas, paired = {}, defaultdict(lambda: defaultdict(list))
    failures, insufficient = [], []
    for key, b in bv.items():
        c = cv[key]
        if b["gate"] == "pass" and c["gate"] == "fail":
            regressions.append(key)
        elif b["gate"] == "fail" and c["gate"] == "pass":
            fixes.append(key)
        elif b["gate"] == "fail" and c["gate"] == "fail":
            still_wrong.append(key)
        elif "insufficient_evidence" in (b["gate"], c["gate"]):
            unresolved.append(key)
        if (b.get("composite_required") or c.get("composite_required")) and (
            b["score"] is None or c["score"] is None
        ):
            insufficient.append("required_composite_unavailable")
        if b["score"] is not None and c["score"] is not None:
            paired["composite"][b["group_id"]].append(c["score"] - b["score"])
        elif (b["score"] is None) != (c["score"] is None):
            insufficient.append("composite_coverage_mismatch")
        baseline_metrics = {(m["field"], m["metric"]): m for m in b["metrics"]}
        candidate_metrics = {(m["field"], m["metric"]): m for m in c["metrics"]}
        if baseline_metrics.keys() != candidate_metrics.keys():
            raise HindsightError("metric cohorts differ")
        for metric_key, before in baseline_metrics.items():
            after = candidate_metrics[metric_key]
            if before["eligible"] != after["eligible"]:
                raise HindsightError("metric eligibility differs")
            if not before["eligible"]:
                continue
            name = "/".join(metric_key)
            if before["status"] != "scored" or after["status"] != "scored":
                insufficient.append("unscored_required_metric:" + name)
                continue
            delta = after["value"] - before["value"]
            if before["direction"] == "lower":
                delta = -delta
            paired[name][b["group_id"]].append(delta)
        if gates.get("no_new_hard_constraint_failures", True):
            before_checks = {x["id"]: x for x in b["constraints"]}
            for check in c["constraints"]:
                if (
                    check["severity"] == "hard"
                    and not check["passed"]
                    and before_checks.get(check["id"], {}).get("passed")
                ):
                    failures.append("new_hard_constraint_failure:" + key)
    for metric, groups in paired.items():
        vals = [v for rows in groups.values() for v in rows]
        enough = len(groups) >= gates.get("min_groups", 2)
        interval = (
            bootstrap(groups, gates.get("bootstrap_samples", 1000), gates.get("seed", 0))
            if enough
            else None
        )
        mean = statistics.mean(vals)
        deltas[metric] = {
            "mean_delta": mean,
            "paired_cases": len(vals),
            "groups": len(groups),
            "interval": interval,
        }
        if not enough:
            insufficient.append("too_few_groups:" + metric)
        margin = gates.get("metric_margins", {}).get(metric, gates.get("max_regression", 0))
        if mean < -margin:
            failures.append("metric_regression:" + metric)
        elif gates.get("require_confidence_bound", False) and interval and interval[0] < -margin:
            insufficient.append("uncertain_regression_margin:" + metric)
    if not paired:
        insufficient.append("no_eligible_metrics")
    cs = summarize(list(cv.values()))
    if cs["coverage"] < gates.get("min_coverage", 1):
        failures.append("candidate_coverage_below_minimum")
    if cs["validity_rate"] < gates.get("min_validity", 1):
        failures.append("candidate_validity_below_minimum")
    if len(regressions) > gates.get("max_new_regressions", len(bv)):
        failures.append("too_many_new_regressions")
    for metric in gates.get("required_metrics", []):
        if metric not in paired:
            insufficient.append("required_metric_unavailable:" + metric)
    for gate_name, metric_name, count_name in (
        ("max_mean_latency_seconds", "mean_latency_seconds", "latency_count"),
        ("max_total_cost_usd", "total_cost_usd", "cost_count"),
    ):
        if gate_name in gates:
            if cs["resources"][count_name] != len(cv) or cs["resources"][metric_name] is None:
                insufficient.append("resource_measurements_missing:" + gate_name)
            elif cs["resources"][metric_name] > gates[gate_name]:
                failures.append("resource_limit:" + gate_name)
    if "min_composite_score" in gates:
        if cs["composite"]["scored"] != len(cv):
            insufficient.append("composite_coverage_incomplete")
        elif cs["composite"]["mean"] < gates["min_composite_score"]:
            failures.append("composite_below_minimum")
    slice_reports = []
    for sg in gates.get("slice_gates", []):
        name, expected = sg["name"], sg["value"]

        def matches(v):
            return (v["exposure"] if name == "exposure" else v["tags"].get(name)) == expected

        selected = {key: v for key, v in cv.items() if matches(v)}
        count = len({v["group_id"] for v in selected.values()})
        summary = summarize(list(selected.values()))
        label = f"{name}={expected}"
        slice_delta = defaultdict(list)
        for key, c in selected.items():
            before = {(m["field"], m["metric"]): m for m in bv[key]["metrics"]}
            for after in c["metrics"]:
                b = before[(after["field"], after["metric"])]
                if (
                    b["eligible"]
                    and after["eligible"]
                    and b["status"] == after["status"] == "scored"
                ):
                    slice_delta[f"{after['field']}/{after['metric']}"].append(
                        after["value"] - b["value"]
                    )
        if count < sg.get("min_groups", gates.get("min_groups", 2)):
            insufficient.append("slice_too_small:" + label)
        if summary["validity_rate"] < sg.get("min_validity", gates.get("min_validity", 1)):
            failures.append("slice_invalid:" + label)
        if not slice_delta:
            insufficient.append("slice_no_eligible_metrics:" + label)
        for metric, values in slice_delta.items():
            if statistics.mean(values) < -sg.get("max_regression", gates.get("max_regression", 0)):
                failures.append("slice_regression:" + label + "/" + metric)
        slice_reports.append(
            {
                "slice": label,
                "groups": count,
                "summary": summary,
                "mean_deltas": {m: statistics.mean(v) for m, v in slice_delta.items()},
            }
        )
    if gates.get("promotion_enabled") is False:
        insufficient.append("promotion_disabled_by_configuration")
    status = "fail" if failures else "insufficient_evidence" if insufficient else "pass"
    report = {
        "status": status,
        "baseline": bm["evaluation_id"],
        "candidate": cm["evaluation_id"],
        "gates": gates,
        "paired_cases": len(bv),
        "deltas": deltas,
        "regressions": regressions,
        "fixes": fixes,
        "still_wrong": still_wrong,
        "unresolved": unresolved,
        "failures": sorted(set(failures)),
        "insufficient_evidence": sorted(set(insufficient)),
        "candidate_summary": cs,
        "slice_gates": slice_reports,
    }
    with artifact_dir(out, overwrite) as dest:
        write_json(dest / "comparison.json", report)
        (dest / "report.md").write_text(markdown_report("Candidate comparison", report))
    return report
