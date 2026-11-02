"""Reject unknown declarative settings rather than silently ignoring them."""

import math
import re

from nodal_hindsight.common import HindsightError


def keys(obj, allowed, name, required=()):
    if not isinstance(obj, dict) or set(obj) - set(allowed) or not set(required) <= set(obj):
        raise HindsightError(f"unsupported or missing {name} configuration keys")


def finite(value, name, low=0, high=None):
    if (
        type(value) not in (int, float)
        or not math.isfinite(value)
        or value < low
        or (high is not None and value > high)
    ):
        raise HindsightError(f"invalid {name}")


def validate_task_settings(task):
    keys(
        task["split_policy"],
        {
            "train_end",
            "dev_end",
            "example_as_of",
            "group_key",
            "boundary_spanning_groups",
            "cross_split_exact_duplicates",
            "embargo_days",
            "evidence_cutoff",
            "calibration_groups",
        },
        "split",
        ("train_end", "dev_end"),
    )
    for key, value in {
        "group_key": "group_id",
        "boundary_spanning_groups": "quarantine",
        "cross_split_exact_duplicates": "exclude_later",
        "evidence_cutoff": "partition_end",
    }.items():
        if task["split_policy"].get(key, value) != value:
            raise HindsightError("unsupported split policy")
    keys(
        task["outcome_policy"],
        {
            "window_days",
            "maturity",
            "join_on",
            "require_matching_group_id",
            "positive_rules",
            "negative_evidence_types",
            "negative_target_requires",
            "missing_outcome",
            "contradictory_outcomes",
        },
        "outcome",
    )
    for key, value in {
        "join_on": "decision_id",
        "require_matching_group_id": True,
        "negative_target_requires": "human_adjudication",
        "missing_outcome": "unknown",
        "contradictory_outcomes": "review_required",
    }.items():
        if task["outcome_policy"].get(key, value) != value:
            raise HindsightError("unsupported outcome policy")
    for rule in task["outcome_policy"].get("positive_rules", []):
        keys(
            rule,
            {
                "outcome_type",
                "executed_action",
                "action_field",
                "supported_fields",
                "required_attribution",
            },
            "outcome rule",
            ("outcome_type", "executed_action", "supported_fields", "required_attribution"),
        )
        if not set(rule["supported_fields"]) <= set(task["field_roles"]):
            raise HindsightError("unknown outcome-supported field")
    keys(
        task["context_rules"],
        {"history_timestamp_path", "timezone_path", "require_decision_time_assertion"},
        "context",
    )
    keys(
        task["label_policy"],
        {"unit", "weak_reference_use", "weights", "hidden_exposure_is_not_gold"},
        "label",
    )
    if task["label_policy"].get("weights"):
        raise HindsightError(
            "tier-weighted aggregates are not supported; use explicit metric composition"
        )
    for key, value in {
        "unit": "field",
        "weak_reference_use": "behavioral_drift_only",
        "hidden_exposure_is_not_gold": True,
    }.items():
        if task["label_policy"].get(key, value) != value:
            raise HindsightError("unsupported label policy")
    keys(
        task["redaction"],
        {"version", "drop", "pseudonymize", "event_drop", "event_pseudonymize", "secret_env"},
        "redaction",
    )
    if task["redaction"] and not task["redaction"].get("version"):
        raise HindsightError("redaction policy requires a version")
    keys(
        task["human_action_semantics"],
        {"output_mode", "follow_ups", "absent_field", "null_message", "empty_follow_ups"},
        "human action",
    )
    keys(task["composition"], {"enabled", "reason", "weights", "inapplicable"}, "composition")
    if task["composition"].get("inapplicable", "block") not in ("block", "renormalize"):
        raise HindsightError("unsupported composite applicability policy")
    known_metrics = set()
    for scorer in task["scorers"]:
        keys(
            scorer,
            {
                "field",
                "metric",
                "threshold",
                "cost_matrix",
                "cost_scale",
                "k",
                "measure",
                "normalization",
                "rubric_ids",
                "mode",
            },
            "scorer",
            ("field", "metric"),
        )
        if "threshold" in scorer:
            finite(scorer["threshold"], "metric threshold", 0, 1)
        if scorer.get("normalization", "NFC") != "NFC":
            raise HindsightError("only NFC edit normalization is supported")
        if scorer.get("measure", "f1") not in ("f1", "precision", "recall", "jaccard"):
            raise HindsightError("unknown set measure")
        if scorer.get("k", 1) < 1:
            raise HindsightError("top-k must be positive")
        names = (
            [f"rubric:{r}" for r in scorer.get("rubric_ids", [])]
            if scorer["metric"] == "rubric"
            else [scorer["metric"]]
        )
        for name in names:
            key = f"{scorer['field']}/{name}"
            if key in known_metrics:
                raise HindsightError("duplicate scorer key")
            known_metrics.add(key)
    if not set(task["composition"].get("weights", {})) <= known_metrics:
        raise HindsightError("composition references unknown metric")
    for rule in task["constraints"]:
        keys(
            rule,
            {
                "id",
                "kind",
                "severity",
                "field",
                "action",
                "action_field",
                "channel",
                "channel_field",
                "max_length",
                "pattern",
                "allowed",
                "actions",
                "timezone_field",
                "weekdays",
                "start_hour",
                "end_hour",
            },
            "constraint",
            ("id", "kind"),
        )
        required = {
            "message_required_iff_action": ["action", "field"],
            "max_length": ["field", "max_length"],
            "forbidden_pattern": ["field", "pattern"],
            "action_channel": ["allowed"],
            "business_hours": ["actions", "weekdays", "start_hour", "end_hour"],
        }.get(rule["kind"], [])
        if not set(required) <= set(rule) or rule.get("severity", "hard") not in ("hard", "soft"):
            raise HindsightError("invalid constraint configuration")
        if "pattern" in rule:
            try:
                re.compile(rule["pattern"])
            except re.error:
                raise HindsightError("invalid constraint regex") from None
    for rubric in task["rubrics"]:
        keys(
            rubric,
            {
                "id",
                "version",
                "status",
                "calibration_status",
                "field",
                "criterion",
                "permitted_input_fields",
                "use_reference",
                "applicability",
                "response_schema",
                "provenance",
            },
            "rubric",
            ("id", "version", "field", "criterion", "permitted_input_fields"),
        )
        if rubric["field"] not in task["field_roles"]:
            raise HindsightError("unknown rubric field")
        app = rubric.get("applicability", {})
        keys(app, {"reference_field", "condition"}, "rubric applicability")
        if app.get("condition", "always") not in ("always", "non_null"):
            raise HindsightError("unsupported rubric applicability condition")
    for schema in (task["input_schema"], task["output_schema"]):

        def check(node):
            if isinstance(node, dict):
                if "$ref" in node and not node["$ref"].startswith("#"):
                    raise HindsightError("external schema references are not allowed")
                for v in node.values():
                    check(v)
            elif isinstance(node, list):
                for v in node:
                    check(v)

        check(schema)


def validate_judge_settings(config):
    keys(
        config,
        {
            "provider",
            "model",
            "adapter_version",
            "parameters",
            "api_key_env",
            "api_version",
            "timeout_seconds",
            "retries",
            "max_calls",
            "cache_dir",
            "responses",
            "default_response",
            "approval_files",
            "calibration_policy",
        },
        "judge",
    )
    if config.get("provider") not in ("fixture", "anthropic"):
        raise HindsightError("unknown judge provider")
    for key in ("max_calls", "retries"):
        finite(config.get(key, 0), key)
        if type(config.get(key, 0)) is not int:
            raise HindsightError("judge attempt limits must be integers")
    finite(config.get("timeout_seconds", 30), "judge timeout", 0.001)


def validate_gates(gates):
    keys(
        gates,
        {
            "promotion_enabled",
            "reason",
            "min_coverage",
            "min_validity",
            "min_groups",
            "max_regression",
            "max_new_regressions",
            "no_new_hard_constraint_failures",
            "bootstrap_samples",
            "seed",
            "metric_margins",
            "require_confidence_bound",
            "required_metrics",
            "slice_gates",
            "max_mean_latency_seconds",
            "max_total_cost_usd",
            "min_composite_score",
        },
        "gate",
    )
    for key in ("min_coverage", "min_validity", "min_composite_score"):
        if key in gates:
            finite(gates[key], key, 0, 1)
    for key in ("max_regression", "max_mean_latency_seconds", "max_total_cost_usd"):
        if key in gates:
            finite(gates[key], key)
    for margin in gates.get("metric_margins", {}).values():
        finite(margin, "metric margin")
    for value in gates.get("slice_gates", []):
        keys(
            value,
            {"name", "value", "min_groups", "min_validity", "max_regression"},
            "slice gate",
            ("name", "value"),
        )
