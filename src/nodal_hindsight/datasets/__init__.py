"""Immutable compilation, protected splits, and portable exports."""

import copy
import hashlib
import hmac
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from nodal_hindsight.common import (
    HindsightError,
    artifact_dir,
    digest,
    markdown_report,
    protected_split,
    read_json,
    read_jsonl,
    timestamp,
    write_json,
    write_jsonl,
)
from nodal_hindsight.ingest import validate
from nodal_hindsight.labels import adjudicate
from nodal_hindsight.schemas import EvalCase


def redact(value, policy):
    value = copy.deepcopy(value)

    def walk(node, segments, mode):
        key = segments[0]
        array = key.endswith("[]")
        key = key[:-2] if array else key
        if not isinstance(node, dict) or key not in node:
            return
        if len(segments) == 1:
            if mode == "drop":
                del node[key]
            else:
                secret = os.environ.get(policy.get("secret_env", "HINDSIGHT_REDACTION_KEY"))
                if not secret:
                    raise HindsightError(
                        "pseudonymization requires redaction secret environment variable"
                    )
                node[key] = hmac.new(
                    secret.encode(), str(node[key]).encode(), hashlib.sha256
                ).hexdigest()
        else:
            for child in node[key] if array else [node[key]]:
                walk(child, segments[1:], mode)

    for path in policy.get("drop", []):
        walk(value, path.split("."), "drop")
    for path in policy.get("pseudonymize", []):
        walk(value, path.split("."), "hash")
    return value


def compile_dataset(
    traces, events, config, as_of, out, quarantine_invalid=False, overwrite=False, parent=None
):
    source = validate(traces, events, config, quarantine_invalid)
    tasks = source["tasks"]
    cutoff = timestamp(as_of)
    ds = sorted(source["decisions"], key=lambda d: (d["decided_at"], d["decision_id"]))
    events_by = defaultdict(list)
    for event in source["events"]:
        events_by[event["decision_id"]].append(event)
    assigned, group_splits = {}, defaultdict(set)
    excluded = list(source["quarantine"])
    for d in ds:
        task = tasks[d["task"]]
        sp = task["split_policy"]
        if cutoff <= timestamp(sp["dev_end"]):
            raise HindsightError("as_of must be after dev_end")
        when = timestamp(d["decided_at"])
        if when > cutoff:
            excluded.append({"decision_id": d["decision_id"], "reason": "after_observation_cutoff"})
            continue
        split = (
            "train"
            if when < timestamp(sp["train_end"])
            else "dev"
            if when < timestamp(sp["dev_end"])
            else "test"
        )
        assigned[d["decision_id"]] = split
        group_splits[(d["task"], d["group_id"])].add(split)
    cases, seen = [], {}
    for d in ds:
        if d["decision_id"] not in assigned:
            continue
        task = tasks[d["task"]]
        sp = task["split_policy"]
        split = assigned[d["decision_id"]]
        why = None
        if len(group_splits[(d["task"], d["group_id"])]) > 1:
            why = "group_crosses_split_boundary"
        embargo = sp.get("embargo_days", 0)
        if embargo:
            start = timestamp(d["decided_at"])
            end = start + timedelta(days=task["outcome_policy"].get("window_days", 7))
            for boundary in (timestamp(sp["train_end"]), timestamp(sp["dev_end"])):
                if start < boundary + timedelta(days=embargo) and end >= boundary - timedelta(
                    days=embargo
                ):
                    why = "embargo_overlap"
        safe = redact(d, task["redaction"])
        fingerprint = digest({"task": d["task"], "context": safe["context"]})
        if fingerprint in seen and seen[fingerprint] != split:
            why = "cross_split_duplicate_input"
        if why:
            excluded.append({"decision_id": d["decision_id"], "reason": why})
            continue
        seen[fingerprint] = split
        bound = (
            timestamp(sp["train_end"])
            if split == "train"
            else timestamp(sp["dev_end"])
            if split == "dev"
            else cutoff
        )
        ev = sorted(events_by[d["decision_id"]], key=lambda e: (e["observed_at"], e["event_id"]))
        # Redaction can cover event.output, event.reason, and any other event path.
        ev = [
            redact(
                e,
                {
                    **task["redaction"],
                    "drop": task["redaction"].get("event_drop", []),
                    "pseudonymize": task["redaction"].get("event_pseudonymize", []),
                },
            )
            for e in ev
        ]
        labels = adjudicate(safe, ev, task, bound, inclusive=split == "test")
        evidence_ids = labels.pop("evidence_ids")
        if d["group_id"] in sp.get("calibration_groups", []):
            if split != "train":
                raise HindsightError("calibration groups must be drawn from training interval")
            split = "calibration"
        item = {
            "case_id": digest([d["task"], d["decision_id"]])[:24],
            "version": "",
            "task": d["task"],
            "task_version": task["version"],
            "decision_id": d["decision_id"],
            "group_id": d["group_id"],
            "decided_at": d["decided_at"],
            "split": split,
            "input": safe["context"],
            "suggestion": safe["suggestion"],
            "exposure": d["exposure"],
            "tags": safe["tags"],
            "rubric_ids": [r["id"] for r in task["rubrics"]],
            "correction_group": fingerprint,
            "provenance": {
                "source": safe["source"],
                "policy": d["policy"],
                "source_hash": digest(d),
                "evidence_ids": evidence_ids,
                "label_cutoff": bound.isoformat(),
                "task_hash": digest(task),
                "sampling": safe.get("sampling"),
                "logging_policy": safe.get("logging_policy"),
            },
            **labels,
        }
        item["version"] = digest(item)
        cases.append(EvalCase.model_validate(item).model_dump(mode="json"))
    counts = Counter(c["split"] for c in cases)
    quality = {
        "cases": len(cases),
        "splits": dict(counts),
        "excluded": excluded,
        "label_counts": dict(
            Counter(label["tier"] for c in cases for label in c["labels"].values())
        ),
        "exposure": dict(Counter(c["exposure"] for c in cases)),
        "outcomes": dict(Counter(c["outcome_status"] for c in cases)),
        "review_flags": dict(Counter(flag for c in cases for flag in c["review_flags"])),
        "ope": {
            "ready": False,
            "reason": "No OPE estimator; propensity metadata is observational only",
            "with_logging_policy": sum(bool(d.get("logging_policy")) for d in ds),
        },
        "duplicate_context_counts": dict(Counter(c["correction_group"] for c in cases)),
    }
    manifest = {
        "schema_version": "1.0",
        "compiler_version": "0.1.0",
        "as_of": cutoff.isoformat(),
        "source_hash": digest(
            {"decisions": ds, "events": sorted(source["events"], key=lambda x: x["event_id"])}
        ),
        "config_hash": digest(tasks),
        "tasks": tasks,
        "counts": dict(counts),
        "excluded_count": len(excluded),
        "parent_dataset_id": parent,
        "cases_hash": digest(cases),
    }
    manifest["dataset_id"] = digest(manifest)
    with artifact_dir(out, overwrite) as dest:
        write_json(dest / "manifest.json", manifest)
        write_jsonl(dest / "cases.jsonl", cases)
        write_json(dest / "definitions/tasks.json", tasks)
        write_json(dest / "definitions/rubrics.json", {k: t["rubrics"] for k, t in tasks.items()})
        for split in ("train", "dev", "test", "calibration"):
            write_jsonl(
                dest / f"inputs/{split}.jsonl",
                [
                    {"case_id": c["case_id"], "input": c["input"]}
                    for c in cases
                    if c["split"] == split
                ],
            )
        write_json(dest / "splits.json", {c["case_id"]: c["split"] for c in cases})
        write_json(dest / "quality-report.json", quality)
        (dest / "quality-report.md").write_text(markdown_report("Dataset quality", quality))
        write_jsonl(dest / "quarantine.jsonl", excluded)
    return manifest


def load_dataset(path, split=None, allow_test=False):
    path = Path(path)
    manifest = read_json(path / "manifest.json")
    identity = dict(manifest)
    dataset_id = identity.pop("dataset_id", None)
    if digest(identity) != dataset_id:
        raise HindsightError("dataset manifest integrity mismatch")
    cases = read_jsonl(path / "cases.jsonl")
    if digest(cases) != manifest["cases_hash"]:
        raise HindsightError("dataset cases integrity mismatch")
    for c in cases:
        EvalCase.model_validate(c)
    if split:
        protected_split(split, allow_test)
        cases = [c for c in cases if c["split"] == split]
    return manifest, cases


def export_dataset(dataset, split, out, allow_test=False, input_only=False, overwrite=False):
    manifest, cases = load_dataset(dataset, split, allow_test)
    exported = []
    for c in cases:
        c = copy.deepcopy(c)
        if input_only:
            exported.append({"case_id": c["case_id"], "input": c["input"]})
            continue
        if split == "train":
            eligible = {f for f, label in c["labels"].items() if label["eligible"]}
            if not eligible and not c["negatives"]:
                continue
            for key in ("references", "acceptable"):
                c[key] = {f: v for f, v in c[key].items() if f in eligible}
            c["feedback"] = [x for x in c["feedback"] if x["field"] in eligible]
        exported.append(c)
    with artifact_dir(out, overwrite) as dest:
        write_jsonl(dest / ("inputs.jsonl" if input_only else "cases.jsonl"), exported)
        write_json(dest / "definitions/tasks.json", manifest["tasks"])
        write_json(
            dest / "manifest.json",
            {
                "dataset_id": manifest["dataset_id"],
                "split": split,
                "input_only": input_only,
                "test_access": split == "test",
                "count": len(exported),
                "content_hash": digest(exported),
                "source_manifest": manifest,
            },
        )
    return {"count": len(exported), "split": split}
