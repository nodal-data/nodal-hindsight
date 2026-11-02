"""Pure task scorers; model IO is delegated to an injected judge."""

import re
import unicodedata
from zoneinfo import ZoneInfo

from nodal_hindsight.common import HindsightError, timestamp
from nodal_hindsight.ingest import validate_schema
from nodal_hindsight.judges import judge_request
from nodal_hindsight.schemas import MetricResult, Verdict


def edit_similarity(a, b):
    a, b = unicodedata.normalize("NFC", a), unicodedata.normalize("NFC", b)
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (x != y)))
        previous = current
    distance = previous[-1] / max(len(a), len(b), 1)
    return 1 - distance


def set_scores(a, b):
    a, b = set(a), set(b)
    tp = len(a & b)
    if not a and not b:
        precision = recall = f1 = jaccard = 1.0
    elif not a or not b:
        precision = recall = f1 = jaccard = 0.0
    else:
        precision, recall = tp / len(a), tp / len(b)
        f1 = 2 * tp / (len(a) + len(b))
        jaccard = tp / len(a | b)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "jaccard": jaccard,
        "tp": tp,
        "fp": len(a - b),
        "fn": len(b - a),
    }


def constraints(case, output, task):
    result = []
    try:
        validate_schema(task["output_schema"], output)
        schema_valid = True
    except HindsightError:
        schema_valid = False
    result.append({"id": "output_schema", "passed": schema_valid, "severity": "hard"})
    for rule in task["constraints"]:
        kind = rule["kind"]
        if kind == "output_schema":
            continue
        field = rule.get("field", "message")
        value = output.get(field)
        passed = True
        if kind == "message_required_iff_action":
            af = rule.get(
                "action_field",
                next((f for f, r in task["field_roles"].items() if r == "categorical"), ""),
            )
            passed = (
                (isinstance(value, str) and bool(value))
                if output.get(af) == rule["action"]
                else value is None
            )
        elif kind == "max_length":
            channel = case["input"].get(rule.get("channel_field", "channel"))
            if rule.get("channel") in (None, channel) and value is not None:
                passed = isinstance(value, str) and len(value) <= rule["max_length"]
        elif kind == "forbidden_pattern":
            passed = value is None or (
                isinstance(value, str) and not re.search(rule["pattern"], value)
            )
        elif kind == "action_channel":
            af = rule.get("action_field", "next_action")
            channel = case["input"].get(rule.get("channel_field", "channel"))
            passed = output.get(af) in rule["allowed"].get(channel, [])
        elif kind == "business_hours":
            af = rule.get("action_field", "next_action")
            if output.get(af) in rule["actions"]:
                local = timestamp(case["decided_at"]).astimezone(
                    ZoneInfo(case["input"][rule.get("timezone_field", "timezone")])
                )
                passed = (
                    local.weekday() in rule["weekdays"]
                    and rule["start_hour"] <= local.hour < rule["end_hour"]
                )
        result.append(
            {"id": rule["id"], "passed": bool(passed), "severity": rule.get("severity", "hard")}
        )
    return result


def score_case(case, output, task, judge=None, prediction_status="success"):
    output = output if isinstance(output, dict) else {}
    checks = constraints(case, output, task)
    valid = prediction_status == "success" and all(
        c["passed"] for c in checks if c["severity"] == "hard"
    )
    metrics = []
    rubric_by = {r["id"]: r for r in task["rubrics"]}
    for spec in task["scorers"]:
        field, name = spec["field"], spec["metric"]
        label = case["labels"][field]
        reference = case["references"].get(field)
        value = output.get(field)
        targets = case["acceptable"].get(field, [reference])
        eligible = label["eligible"]
        if name == "not_in_negatives":
            eligible = bool(case["negatives"].get(field))
        specs = spec.get("rubric_ids", []) if name == "rubric" else [None]
        for rid in specs:
            metric_name = f"rubric:{rid}" if rid else name
            result = {
                "metric": metric_name,
                "field": field,
                "value": None,
                "status": "inapplicable",
                "eligible": eligible,
                "passed": None,
                "feedback": label["reason"],
                "reason": label["reason"],
                "evidence_ids": label["evidence_ids"],
                "details": {},
            }
            if name == "rubric":
                rubric = rubric_by[rid]
                app = rubric.get("applicability", {})
                if (
                    app.get("condition") == "non_null"
                    and case["references"].get(app.get("reference_field", field)) is None
                ):
                    metrics.append(MetricResult(**result).model_dump())
                    continue
                approved = judge is not None and judge.approved(rubric)
                result["eligible"] = approved
                result["details"]["experimental"] = not approved
                if value is None or prediction_status != "success":
                    result.update(
                        value=0.0,
                        status="scored",
                        passed=False,
                        reason="candidate_missing",
                        feedback="Candidate output is missing.",
                    )
                elif judge is None:
                    result.update(
                        status="error",
                        reason="judge_not_configured",
                        feedback="Rubric requires a configured judge.",
                    )
                else:
                    try:
                        response = judge(judge_request(case, output, rubric), rubric)
                        result.update(
                            value=float(response["passed"]),
                            status="scored",
                            passed=response["passed"],
                            reason="rubric_verdict",
                            feedback=response["reason"],
                        )
                    except HindsightError:
                        result.update(
                            status="error",
                            reason="judge_error",
                            feedback="Judge failed or exhausted its budget.",
                        )
                metrics.append(MetricResult(**result).model_dump())
                continue
            if not eligible:
                if label["tier"] == "Weak" and reference is not None:
                    result.update(
                        metric="behavioral_drift:" + name,
                        value=float(value == reference),
                        status="scored",
                        reason="weak_reference_descriptive_only",
                        feedback="Agreement with unconfirmed accepted behavior.",
                    )
                metrics.append(MetricResult(**result).model_dump())
                continue
            missing = value is None or prediction_status != "success"
            score, details = 0.0, {}
            if not missing:
                try:
                    if name == "exact_match":
                        score = float(value in targets)
                        details = {"reference": reference, "prediction": value}
                    elif name == "cost_weighted_match":
                        cost = min(spec["cost_matrix"][ref][value] for ref in targets)
                        score, details = 1 - cost / spec["cost_scale"], {"cost": cost}
                    elif name == "top_k_match":
                        if not isinstance(value, list) or len(set(value)) != len(value):
                            raise ValueError
                        rank_targets = [
                            ref[0] if isinstance(ref, list) and ref else ref for ref in targets
                        ]
                        score = float(any(ref in value[: spec.get("k", 3)] for ref in rank_targets))
                    elif name == "set_f1":
                        if not isinstance(value, list):
                            raise ValueError
                        details = set_scores(value, reference)
                        score = details[spec.get("measure", "f1")]
                    elif name == "edit_distance":
                        if not isinstance(value, str) or not isinstance(reference, str):
                            raise ValueError
                        score = edit_similarity(value, reference)
                        details = {"distance": 1 - score}
                    elif name == "not_in_negatives":
                        score = float(value not in case["negatives"][field])
                except (TypeError, KeyError, ValueError):
                    missing = True
            if missing:
                score = 0.0
                if name == "cost_weighted_match":
                    details = {"cost": spec["cost_scale"]}
                elif name == "exact_match":
                    details = {"reference": reference, "prediction": "<invalid_or_missing>"}
                elif name == "set_f1":
                    details = {
                        "precision": 0.0,
                        "recall": 0.0,
                        "f1": 0.0,
                        "jaccard": 0.0,
                        "tp": 0,
                        "fp": 1 if not reference else 0,
                        "fn": len(reference),
                        "invalid_penalty": True,
                    }
            details["corrections"] = [f for f in case["feedback"] if f["field"] == field]
            threshold = spec.get("threshold", 1.0)
            result.update(
                value=score,
                status="scored",
                passed=score >= threshold,
                reason="candidate_missing_or_invalid" if missing else "reference_scored",
                details=details,
                feedback="Missing/invalid candidate field receives zero."
                if missing
                else "Reference agreement passed."
                if score >= threshold
                else "Candidate differs from eligible reference; review field evidence.",
            )
            metrics.append(MetricResult(**result).model_dump())
    composition = task["composition"]
    score = None
    if composition.get("enabled"):
        numerator, denominator, blocked = 0, 0, False
        for key, weight in composition["weights"].items():
            selected = next((m for m in metrics if f"{m['field']}/{m['metric']}" == key), None)
            if (
                selected is not None
                and selected["status"] == "inapplicable"
                and selected["reason"] == "observed_not_applicable"
                and composition.get("inapplicable", "block") == "renormalize"
            ):
                continue
            if selected is None or not selected["eligible"]:
                blocked = True
            elif selected["status"] == "scored":
                numerator += weight * selected["value"]
                denominator += weight
            else:
                blocked = True
        if denominator and not blocked:
            score = numerator / denominator
    eligible_metrics = [m for m in metrics if m["eligible"]]
    gate = (
        "fail"
        if not valid or any(m["passed"] is False for m in eligible_metrics)
        else "insufficient_evidence"
        if not eligible_metrics
        or any(m["status"] != "scored" for m in eligible_metrics)
        or (composition.get("enabled") and score is None)
        else "pass"
    )
    return Verdict(
        case_id=case["case_id"],
        case_version=case["version"],
        group_id=case["group_id"],
        valid=valid,
        metrics=metrics,
        score=score,
        composite_required=bool(composition.get("enabled")),
        gate=gate,
        constraints=checks,
        tags={
            **case["tags"],
            "task": case["task"],
            "policy_version": case["provenance"]["policy"]["prompt_version"],
            "month": case["decided_at"][:7],
        },
        exposure=case["exposure"],
        labels=case["labels"],
        prediction_status=prediction_status,
    ).model_dump()
