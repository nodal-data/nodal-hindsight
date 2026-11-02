"""Field-level, knowledge-time-bounded evidence adjudication."""

from datetime import timedelta

from nodal_hindsight.common import canonical, timestamp


def adjudicate(decision, events, task, cutoff, inclusive=False):
    known = [
        e
        for e in events
        if (
            timestamp(e["observed_at"]) <= cutoff
            if inclusive
            else timestamp(e["observed_at"]) < cutoff
        )
    ]
    humans = [e for e in known if e["record_type"] == "human_action"]
    reviews = [e for e in known if e["record_type"] == "review"]
    window_end = timestamp(decision["decided_at"]) + timedelta(
        days=task["outcome_policy"].get("window_days", 7)
    )
    outcomes = [
        e for e in known if e["record_type"] == "outcome" and timestamp(e["event_at"]) <= window_end
    ]
    mature = window_end <= cutoff
    negative_evidence = any(
        e["outcome_type"] in task["outcome_policy"].get("negative_evidence_types", [])
        for e in outcomes
    )
    refs, labels, acceptable, negatives, flags, feedback = {}, {}, {}, {}, [], []
    for h in humans:
        if h["kind"] == "accepted" and any(
            v != decision["suggestion"].get(f) for f, v in h["output"].items()
        ):
            flags.append("declared_acceptance_disagrees_with_output")
        if h["kind"] == "independent" and decision["exposure"] == "shown":
            flags.append("independent_action_with_shown_suggestion")
    for field, role in task["field_roles"].items():
        observations = [h for h in humans if field in h["output"]]
        label = {
            "tier": "Unknown",
            "eligible": False,
            "reason": "missing_human_reference",
            "evidence_ids": [],
            "subtype": None,
        }
        complete = [h for h in observations if h.get("observation_complete", True)]
        vals = {canonical(h["output"][field]) for h in observations}
        additive = role == "set" and task["human_action_semantics"].get("output_mode") == "additive"
        if observations and (len(vals) == 1 or additive) and complete:
            h = complete[-1]
            value = h["output"][field]
            if additive:
                value = sorted({item for obs in observations for item in obs["output"][field]})
            refs[field] = value
            changed = value != decision["suggestion"].get(field)
            independent = decision["exposure"] == "hidden" or h["kind"] == "independent"
            tier = "Silver" if role in ("categorical", "ranked_list") else "Bronze"
            label = {
                "tier": tier if changed or independent else "Weak",
                "eligible": changed or independent,
                "reason": "human_correction"
                if changed
                else "independent_human_choice"
                if independent
                else "accepted_without_confirmation",
                "evidence_ids": [x["event_id"] for x in observations],
                "subtype": None,
            }
            if value is None:
                label["eligible"] = False
                label["reason"] = "observed_not_applicable"
            if changed:
                feedback.append(
                    {
                        "field": field,
                        "suggested": decision["suggestion"].get(field),
                        "observed": value,
                        "reason": h.get("reason"),
                        "event_id": h["event_id"],
                    }
                )
            if mature and not negative_evidence:
                for out in outcomes:
                    attr = out["attribution"]
                    linked = next(
                        (x for x in complete if x["event_id"] == attr.get("human_action_event_id")),
                        None,
                    )
                    if linked is None:
                        continue
                    for rule in task["outcome_policy"].get("positive_rules", []):
                        action_field = rule.get(
                            "action_field",
                            next(
                                (f for f, r in task["field_roles"].items() if r == "categorical"),
                                "",
                            ),
                        )
                        if (
                            out["outcome_type"] == rule["outcome_type"]
                            and field in rule["supported_fields"]
                            and attr.get("method") == rule["required_attribution"]
                            and linked["output"].get(action_field) == rule["executed_action"]
                        ):
                            label.update(
                                tier="Gold",
                                eligible=value is not None,
                                reason="attributed_mature_outcome",
                                subtype="outcome_supported",
                            )
                            label["evidence_ids"].append(out["event_id"])
        elif len(vals) > 1 and not additive:
            label["reason"] = "conflicting_human_references"
            flags.append(f"conflicting_human_references:{field}")
        elif observations:
            label["reason"] = "incomplete_observation_window"
        field_reviews = [r for r in reviews if field in r["fields"]]
        if field_reviews:
            # A later explicit reviewer decision supersedes prior review decisions, all retained as evidence.
            r = sorted(field_reviews, key=lambda x: (x["observed_at"], x["event_id"]))[-1]
            if r["disposition"] == "approve" and (
                field in r["references"] or r["acceptable"].get(field)
            ):
                refs[field] = r["references"].get(field, r["acceptable"].get(field, [None])[0])
                if field in r["acceptable"]:
                    acceptable[field] = r["acceptable"][field]
                label = {
                    "tier": "Gold",
                    "eligible": refs[field] is not None,
                    "reason": "human_adjudication",
                    "evidence_ids": [r["event_id"]],
                    "subtype": "adjudicated",
                }
            elif r["disposition"] == "exclude":
                refs.pop(field, None)
                label = {
                    "tier": "Unknown",
                    "eligible": False,
                    "reason": "review_excluded",
                    "evidence_ids": [r["event_id"]],
                    "subtype": None,
                }
            elif r["disposition"] == "negative":
                negatives[field] = r["negatives"].get(field, [])
                label["evidence_ids"].append(r["event_id"])
        labels[field] = label
    if negative_evidence:
        flags.append("negative_outcome_requires_review")
    if any(e["attribution"].get("method") != "explicit_activity_link" for e in outcomes):
        flags.append("outcome_attribution_requires_review")
    return {
        "references": refs,
        "acceptable": acceptable,
        "negatives": negatives,
        "labels": labels,
        "feedback": feedback,
        "review_flags": sorted(set(flags)),
        "outcome_status": "censored"
        if not mature
        else "negative_evidence"
        if negative_evidence
        else "observed"
        if outcomes
        else "unknown",
        "evidence_ids": [e["event_id"] for e in known],
    }
