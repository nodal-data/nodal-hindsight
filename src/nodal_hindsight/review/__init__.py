"""Training-only correction reports and review templates."""

import difflib
import random
from collections import Counter

from nodal_hindsight.common import artifact_dir, markdown_report, write_json, write_jsonl
from nodal_hindsight.datasets import load_dataset


def correction_report(dataset, out, sample_size=30, seed=0, overwrite=False):
    manifest, cases = load_dataset(dataset, "train")
    records = []
    for case in cases:
        for feedback in case["feedback"]:
            record = {
                "case_id": case["case_id"],
                "decision_id": case["decision_id"],
                "group_id": case["group_id"],
                "tags": case["tags"],
                "label": case["labels"][feedback["field"]],
                **feedback,
            }
            if isinstance(feedback["suggested"], str) and isinstance(feedback["observed"], str):
                record["diff"] = "\n".join(
                    difflib.unified_diff(
                        feedback["suggested"].splitlines(),
                        feedback["observed"].splitlines(),
                        fromfile="suggested",
                        tofile="sent",
                        lineterm="",
                    )
                )
            records.append(record)
    rng = random.Random(seed)
    selected = rng.sample(cases, min(sample_size, len(cases)))
    templates = [
        {
            "case_id": c["case_id"],
            "decision_id": c["decision_id"],
            "group_id": c["group_id"],
            "reviewer": None,
            "event_at": None,
            "observed_at": None,
            "fields": list(c["labels"]),
            "disposition": None,
            "references": {},
            "acceptable": {},
            "negatives": {},
            "reason": None,
        }
        for c in selected
    ]
    report = {
        "dataset_id": manifest["dataset_id"],
        "split": "train",
        "correction_count": len(records),
        "field_counts": dict(Counter(r["field"] for r in records)),
        "sample_size": len(selected),
        "seed": seed,
    }
    with artifact_dir(out, overwrite) as dest:
        write_json(dest / "report.json", report)
        write_jsonl(dest / "corrections.jsonl", records)
        write_jsonl(dest / "review-template.jsonl", templates)
        (dest / "report.md").write_text(markdown_report("Training correction review", report))
    return report
