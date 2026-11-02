"""Read canonical records and validate task-dependent semantics."""

import copy
import math
from collections import Counter

from jsonschema import Draft202012Validator, FormatChecker, SchemaError
from pydantic import ValidationError

from nodal_hindsight.common import (
    HindsightError,
    canonical,
    config_file,
    project_path,
    read_jsonl,
    timestamp,
)
from nodal_hindsight.schemas import Decision, HumanAction, Outcome, ReviewDecision, Task
from nodal_hindsight.ingest.configuration import validate_task_settings


SCORERS = {
    "exact_match",
    "cost_weighted_match",
    "top_k_match",
    "set_f1",
    "edit_distance",
    "rubric",
    "not_in_negatives",
}
CONSTRAINTS = {
    "output_schema",
    "message_required_iff_action",
    "max_length",
    "forbidden_pattern",
    "business_hours",
    "action_channel",
}


def validate_schema(schema, value, partial=False):
    schema = copy.deepcopy(schema)
    if partial:
        schema.pop("required", None)
        schema.pop("allOf", None)
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
    if errors:
        e = errors[0]
        raise HindsightError(
            f"schema violation at {'.'.join(map(str, e.path)) or '<root>'}: {e.validator}"
        )


def parse_model(model, raw):
    try:
        return model.model_validate(raw).model_dump(mode="json")
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors(include_input=False))
        raise HindsightError(f"invalid {model.__name__} fields: {fields}") from None


def load_tasks(path):
    data = config_file(path)
    if isinstance(data, dict) and set(data) == {"task_catalog"}:
        from pathlib import Path

        return load_tasks(Path(path).parent / data["task_catalog"])
    records = data if isinstance(data, list) else [data]
    tasks = {}
    for raw in records:
        task = parse_model(Task, raw)
        validate_task_settings(task)
        name = task["task"]
        if name in tasks:
            raise HindsightError("duplicate task ID")
        for key in ("input_schema", "output_schema"):
            try:
                Draft202012Validator.check_schema(task[key])
            except SchemaError:
                raise HindsightError(f"invalid {key}") from None
        fields = task["output_schema"].get("properties", {})
        if set(fields) != set(task["field_roles"]):
            raise HindsightError("field_roles must describe every output property")
        split = task["split_policy"]
        if timestamp(split["train_end"]) >= timestamp(split["dev_end"]):
            raise HindsightError("train_end must precede dev_end")
        if split.get("embargo_days", 0) < 0:
            raise HindsightError("embargo_days must be nonnegative")
        policy = task["outcome_policy"]
        if policy.get("window_days", 7) < 0:
            raise HindsightError("outcome window must be nonnegative")
        if policy.get("maturity", "full_window_required") != "full_window_required":
            raise HindsightError("unsupported outcome maturity policy")
        semantics = task["human_action_semantics"]
        if semantics.get("output_mode", "partial_snapshot") not in ("partial_snapshot", "additive"):
            raise HindsightError("unsupported human output mode")
        for scorer in task["scorers"]:
            if scorer.get("metric") not in SCORERS or scorer.get("field") not in fields:
                raise HindsightError("unknown scorer or field")
            if scorer["metric"] == "cost_weighted_match":
                vocab = fields[scorer["field"]].get("enum", [])
                matrix = scorer.get("cost_matrix", {})
                scale = scorer.get("cost_scale", 0)
                if not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0:
                    raise HindsightError("cost_scale must be positive and finite")
                if set(matrix) != set(vocab):
                    raise HindsightError("cost matrix must cover vocabulary")
                for ref, row in matrix.items():
                    if set(row) != set(vocab):
                        raise HindsightError("cost matrix row must cover vocabulary")
                    if any(
                        not isinstance(v, (float, int))
                        or not math.isfinite(v)
                        or v < 0
                        or v > scale
                        for v in row.values()
                    ):
                        raise HindsightError("costs must be finite and between zero and scale")
                    if row[ref] != 0:
                        raise HindsightError("cost matrix diagonal must be zero")
        for constraint in task["constraints"]:
            if constraint.get("kind") not in CONSTRAINTS:
                raise HindsightError("unsupported constraint kind")
        rubric_ids = [r["id"] for r in task["rubrics"]]
        if len(set(rubric_ids)) != len(rubric_ids):
            raise HindsightError("duplicate rubric ID")
        for scorer in task["scorers"]:
            if scorer["metric"] == "rubric" and not set(scorer.get("rubric_ids", [])) <= set(
                rubric_ids
            ):
                raise HindsightError("scorer references unknown rubric")
        if task["composition"].get("enabled"):
            weights = task["composition"].get("weights", {})
            if not weights or any(
                not isinstance(w, (int, float)) or not math.isfinite(w) or w <= 0
                for w in weights.values()
            ):
                raise HindsightError("composition requires positive finite metric weights")
        tasks[name] = task
    return tasks


def validate_decision(raw, tasks):
    d = parse_model(Decision, raw)
    if d["task"] not in tasks:
        raise HindsightError("unknown task")
    task = tasks[d["task"]]
    validate_schema(task["input_schema"], d["context"])
    validate_schema(task["output_schema"], d["suggestion"])
    prov = d["context_provenance"]
    if not prov["decision_time_complete"]:
        raise HindsightError("missing decision-time completeness assertion")
    if timestamp(prov["snapshot_at"]) > timestamp(d["decided_at"]):
        raise HindsightError("future context snapshot")
    path = task["context_rules"].get("history_timestamp_path") or prov.get("history_timestamp_path")
    if path and any(
        timestamp(t) > timestamp(d["decided_at"]) for t in project_path(d["context"], path)
    ):
        raise HindsightError("future history entry")
    for obj, key in (
        (d.get("sampling"), "inclusion_probability"),
        (d.get("logging_policy"), "selected_probability"),
    ):
        if (
            obj
            and key in obj
            and (
                not isinstance(obj[key], (int, float))
                or not math.isfinite(obj[key])
                or not 0 < obj[key] <= 1
            )
        ):
            raise HindsightError("invalid probability")
    lp = d.get("logging_policy")
    if lp and "distribution" in lp:
        dist = lp["distribution"]
        if not isinstance(dist, dict) or any(
            not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
            for v in dist.values()
        ):
            raise HindsightError("invalid logging distribution")
        if abs(sum(dist.values()) - 1) > 1e-8 or dist.get(lp.get("selected"), 0) <= 0:
            raise HindsightError("logging distribution has invalid support or normalization")
        if (
            "selected_probability" in lp
            and abs(dist[lp["selected"]] - lp["selected_probability"]) > 1e-8
        ):
            raise HindsightError("selected probability mismatch")
    return d


def validate(traces, events, config, quarantine_invalid=False):
    tasks = load_tasks(config)
    quarantine = [] if quarantine_invalid else None
    decisions, event_records = {}, {}

    def add(raw, model, collection, key, checker):
        try:
            item = checker(raw)
            identity = item[key]
            if identity in collection and canonical(collection[identity]) != canonical(item):
                # Conflicting IDs are never silently quarantined.
                raise RuntimeError("conflicting record ID")
            collection[identity] = item
        except HindsightError as exc:
            if quarantine is None:
                raise
            quarantine.append({"record_type": model, "reason": str(exc)})
        except RuntimeError:
            raise HindsightError("conflicting record ID") from None

    for raw in read_jsonl(traces, quarantine):
        add(raw, "decision", decisions, "decision_id", lambda r: validate_decision(r, tasks))

    def check_event(raw):
        models = {"human_action": HumanAction, "outcome": Outcome, "review": ReviewDecision}
        if raw.get("record_type") not in models:
            raise HindsightError("unknown event record_type")
        e = parse_model(models[raw["record_type"]], raw)
        d = decisions.get(e["decision_id"])
        if d is None or d["group_id"] != e["group_id"]:
            raise HindsightError("event decision/group join mismatch")
        if (
            not timestamp(d["decided_at"])
            <= timestamp(e["event_at"])
            <= timestamp(e["observed_at"])
        ):
            raise HindsightError("invalid event chronology")
        task = tasks[d["task"]]
        if e["record_type"] == "human_action":
            validate_schema(task["output_schema"], e["output"], partial=True)
        if e["record_type"] == "review":
            if not set(e["fields"]) <= set(task["field_roles"]):
                raise HindsightError("unknown review field")
            validate_schema(task["output_schema"], e["references"], partial=True)
            for key in ("acceptable", "negatives"):
                for field, values in e[key].items():
                    if field not in e["fields"] or not isinstance(values, list):
                        raise HindsightError("invalid review alternatives")
                    for value in values:
                        validate_schema(task["output_schema"], {field: value}, partial=True)
        return e

    if events:
        for raw in read_jsonl(events, quarantine):
            add(raw, raw.get("record_type"), event_records, "event_id", check_event)
    for e in event_records.values():
        if e["record_type"] == "outcome" and e["attribution"].get("human_action_event_id"):
            link = event_records.get(e["attribution"]["human_action_event_id"])
            if (
                not link
                or link["record_type"] != "human_action"
                or link["decision_id"] != e["decision_id"]
                or timestamp(link["event_at"]) > timestamp(e["event_at"])
            ):
                raise HindsightError("invalid outcome activity attribution")
    return {
        "tasks": tasks,
        "decisions": list(decisions.values()),
        "events": list(event_records.values()),
        "quarantine": quarantine or [],
        "counts": dict(Counter(e["record_type"] for e in event_records.values())),
    }
