#!/usr/bin/env python3
"""Workflow-history readiness: profile exports, apply verdict rules, write the report.

Standard library only, so it runs from an installed skill with no package around it.

Subcommands:
  template        print an empty profile to fill from warehouse probe results
  list-tables     list the tables and column names of a SQLite database
  list-columns    describe the columns of exports or SQLite tables without printing values
  profile-files   compute the aggregate profile from CSV/JSONL exports or SQLite tables
  build           apply the verdict rules and write readiness.json + readiness.md
  check           validate a readiness.json
  sample          show a few truncated values of named columns, after the user consents

Only `sample` prints row content, and only with --consent-given. Everywhere else,
free-text columns are tested for presence and compared by fingerprint; the output holds
counts, rates, column names, low-cardinality category values, and reviewer ordinals.
"""
import argparse
import csv
import hashlib
import json
import math
import re
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

SCHEMA_VERSION = "0.1"
PROFILE_VERSION = "0.1"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "assets" / "readiness.schema.json"

HIGH = 0.95  # coverage at or above this is "present"
LOW = 0.50  # coverage below this is "missing"
OUTCOME_HIGH = 0.80
OUTCOME_LOW = 0.20
LATE_LAG_DAYS = 90
OVERWRITE_CLEAN = 0.02
OVERWRITE_BAD = 0.20
RUBBER_STAMP = 0.95
CATEGORY_MAX_VALUES = 50
CATEGORY_MAX_LENGTH = 64
CATEGORY_MAX_DISTINCT_SHARE = 0.5  # a status field repeats; free text mostly does not
CATEGORY_TRACK_LIMIT = 10000
REVIEWER_LIMIT = 20
CLASS_LIMIT = 20

# Zero observed errors, one-sided 95% bound: n = ceil(ln(0.05) / ln(1 - p)).
LABEL_TABLE = [(0.05, 59), (0.02, 149), (0.01, 299), (0.005, 598)]
LABEL_BASIS = (
    "Reference table only: labels needed to bound the error rate with zero observed errors "
    "at one-sided 95% confidence. Not a sample-size advisor."
)

KINDS = [
    "accepted",
    "edited",
    "overridden",
    "independent",
    "rejected",
    "escalated",
    "none",
    "unknown",
]
STATUSES = ["present", "partial", "missing"]
CHECK_STATUSES = [*STATUSES, "not-measured"]
VERDICTS = ["mine-history", "partial", "log-forward-only", "not-measured"]
SOURCE_TYPES = ["repository", "warehouse", "files", "sqlite", "unsupported"]
SOURCE_NOTE_MAX = 300
FINDING_MAX = 400
LOGGING_CHANGES_MAX = 20
SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db")
ANSWER_SOURCES = ["user", "code"]
OUTCOME_KINDS = ["user-response", "downstream-status", "later-review", "business-result"]
OUTCOME_ARRIVES = ["every-decision", "only-on-problems", "some-decisions", "unknown"]
OUTCOME_SIGNALS_MAX = 5
CONSENTS = ["aggregates-only", "samples-approved"]
SAMPLE_ROWS_PER_LOOK = 5
SAMPLE_ROWS_PER_SESSION = 20
SAMPLE_COLUMNS_PER_LOOK = 3
SAMPLE_MAX_CHARS = 300

CHECKS = [
    ("suggestion_separate", "AI suggestion stored separately from the final human output"),
    ("suggestion_not_overwritten", "Suggestion not overwritten by the reviewer's edit"),
    ("exposure_known", "Exposure known (did the reviewer see the suggestion?)"),
    ("timestamps", "Decision, action and observation timestamps"),
    ("reviewer_id", "Reviewer ID present and stable"),
    ("policy_identity", "Policy identity (model, prompt, code version) recoverable"),
    ("frozen_context", "Frozen input context recoverable as of decision time"),
    ("outcome_join", "Join key from decision to outcome, with lag and coverage"),
    ("volume_accept_rate", "Volume per week and accept-unchanged rate"),
    ("request_type_signal", "Request-type signal"),
]
CHECK_IDS = [check_id for check_id, _ in CHECKS]
CHECK_TITLES = dict(CHECKS)

PROFILE_FIELDS = {
    "decisions": {
        "rows": "count",
        "duplicate_id_rate": "rate",
        "suggestion_coverage": "rate",
        "suggestion_equals_final_rate": "rate",
        "suggestion_equals_final_rate_when_edited": "rate",
        "suggestion_modified_after_action_rate": "rate",
        "exposure_counts": "exposure",
        "decided_at_coverage": "rate",
        "policy_model_coverage": "rate",
        "policy_prompt_version_coverage": "rate",
        "policy_code_version_coverage": "rate",
        "context_coverage": "rate",
        "context_snapshot_coverage": "rate",
        "request_content_coverage": "rate",
        "category_field": "str",
        "category_coverage": "rate",
        "category_cardinality": "count",
        "category_values": "values",
        "category_values_suppressed": "str",
        "first_decided_at": "str",
        "last_decided_at": "str",
        "weeks_observed": "count",
        "weekly_volume_median": "number",
    },
    "actions": {
        "rows": "count",
        "decisions_with_action_rate": "rate",
        "orphan_rate": "rate",
        "event_at_coverage": "rate",
        "observed_at_coverage": "rate",
        "actor_coverage": "rate",
        "distinct_actors": "count",
        "kind_counts": "kinds",
        "accept_unchanged_rate": "rate",
        "accept_unchanged_basis": "basis",
        "reviewers": "reviewers",
    },
    "outcomes": {
        "rows": "count",
        "decisions_with_outcome_rate": "rate",
        "orphan_rate": "rate",
        "event_at_coverage": "rate",
        "observed_at_coverage": "rate",
        "lag_days_p50": "number",
        "lag_days_p90": "number",
    },
}

COLUMN_KEYS = {
    "decisions": {
        "id",
        "decided_at",
        "suggestion",
        "suggestion_updated_at",
        "exposure",
        "exposure_values",
        "model",
        "prompt_version",
        "code_version",
        "context",
        "context_snapshot_at",
        "request_content",
        "category",
        "where",
    },
    "actions": {
        "decision_id",
        "event_at",
        "observed_at",
        "actor",
        "kind",
        "kind_values",
        "output",
        "where",
    },
    "outcomes": {"decision_id", "event_at", "observed_at", "where"},
}
NON_COLUMN_KEYS = {"exposure_values", "kind_values", "where"}

# Most valuable first: what to start logging when a check is not present.
LOGGING_ADVICE = [
    (
        "suggestion_separate",
        "Store the AI output in its own field at the moment it is produced, separate from "
        "anything a person writes later.",
    ),
    (
        "suggestion_not_overwritten",
        "Never update the stored AI output. Write a reviewer's edit or a regeneration as a "
        "new record.",
    ),
    (
        "exposure_known",
        "Record on each decision whether the reviewer was shown the AI output before acting "
        "(shown, hidden or unknown).",
    ),
    (
        "outcome_join",
        "Record each outcome as its own event with the decision ID, what happened and when "
        "it was observed.",
    ),
    (
        "timestamps",
        "Record when the decision was made, when each human action and outcome happened, and "
        "when the system observed it.",
    ),
    ("reviewer_id", "Store a stable ID for the person on every human action."),
    (
        "volume_accept_rate",
        "Store what the reviewer did (accepted, edited, rejected) as an explicit value on "
        "each human action.",
    ),
    (
        "policy_identity",
        "Store the model name, prompt version and code version with every decision.",
    ),
    (
        "frozen_context",
        "Save a copy of the inputs the AI saw with each decision, and the time it was captured.",
    ),
    (
        "request_type_signal",
        "Store the request content and a category, queue or tag on each decision.",
    ),
]

CANNOT_DO = [
    "Recover a suggestion that was overwritten, exposure that was never logged, or reviewer "
    "IDs that do not exist. Discovery reports these; it does not fix them.",
    "Make outcomes arrive sooner or correct non-random missing outcomes.",
    "Certify anything. Mined accept-history is descriptive; certification needs a blind or "
    "outcome-supported cohort.",
]


class UsageError(Exception):
    pass


# ---------------------------------------------------------------------------
# Small helpers


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def pct(value):
    if value is None:
        return "not measured"
    text = f"{value * 100:.1f}".rstrip("0").rstrip(".")
    return f"{text}%"


def num(value):
    if value is None:
        return "not measured"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


def ratio(part, whole):
    return round(part / whole, 4) if whole else None


def coverage_status(value, high=HIGH, low=LOW):
    if value is None:
        return None
    if value >= high:
        return "present"
    if value >= low:
        return "partial"
    return "missing"


def worst(statuses):
    order = {"missing": 0, "partial": 1, "present": 2}
    return min(statuses, key=lambda status: order[status])


# ---------------------------------------------------------------------------
# Profile validation


def empty_profile(workflow="workflow-name", as_of=None):
    profile = {
        "profile_version": PROFILE_VERSION,
        "workflow": workflow,
        "as_of": as_of or date.today().isoformat(),
        "sources": [],
        "content_access": {"consent": "aggregates-only", "rows_viewed": 0},
        "outcome_signals": None,
        "code_findings": [],
        "logging_changes": [],
    }
    for section, fields in PROFILE_FIELDS.items():
        profile[section] = {name: None for name in fields}
    profile["answers"] = []
    return profile


def _field_problem(kind, value):
    if value is None:
        return None
    if kind == "rate":
        if not is_number(value) or not 0 <= value <= 1:
            return "must be a number between 0 and 1"
    elif kind == "count":
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return "must be a non-negative integer"
    elif kind == "number":
        if not is_number(value):
            return "must be a number"
    elif kind == "str":
        if not isinstance(value, str):
            return "must be a string"
    elif kind == "basis":
        if value not in ("kind", "text-equality"):
            return "must be 'kind' or 'text-equality'"
    elif kind == "exposure":
        if not isinstance(value, dict) or set(value) != {"shown", "hidden", "unknown"}:
            return "must be an object with exactly shown, hidden, unknown"
        if any(_field_problem("count", count) or count is None for count in value.values()):
            return "counts must be non-negative integers"
    elif kind == "kinds":
        if not isinstance(value, dict) or not set(value) <= set(KINDS):
            return f"must be an object keyed by {', '.join(KINDS)}"
        if any(_field_problem("count", count) or count is None for count in value.values()):
            return "counts must be non-negative integers"
    elif kind == "values":
        if not isinstance(value, list):
            return "must be a list of {value, count}"
        for item in value:
            if not isinstance(item, dict) or set(item) != {"value", "count"}:
                return "each item must have exactly value and count"
            if not isinstance(item["value"], str) or len(item["value"]) > CATEGORY_MAX_LENGTH:
                return f"values must be strings of at most {CATEGORY_MAX_LENGTH} characters"
            if _field_problem("count", item["count"]) or item["count"] is None:
                return "counts must be non-negative integers"
        if len(value) > CATEGORY_MAX_VALUES:
            return f"at most {CATEGORY_MAX_VALUES} values; report cardinality only beyond that"
    elif kind == "reviewers":
        if not isinstance(value, list):
            return "must be a list"
        allowed = {"reviewer", "actions", "accept_unchanged_rate", "median_seconds_to_action"}
        for item in value:
            if not isinstance(item, dict) or not set(item) <= allowed:
                return f"each reviewer may only have {', '.join(sorted(allowed))}"
            if not re.fullmatch(r"reviewer_\d+", str(item.get("reviewer", ""))):
                return "reviewer must be an ordinal such as reviewer_1, never a real ID"
            if _field_problem("count", item.get("actions")) or item.get("actions") is None:
                return "reviewer actions must be a non-negative integer"
            if _field_problem("rate", item.get("accept_unchanged_rate")):
                return "reviewer accept_unchanged_rate must be between 0 and 1"
            if _field_problem("number", item.get("median_seconds_to_action")):
                return "reviewer median_seconds_to_action must be a number"
    return None


def content_access(profile):
    """Consent and rows viewed, defaulting to aggregates only."""
    access = profile.get("content_access") or {}
    return {
        "consent": access.get("consent", "aggregates-only"),
        "rows_viewed": access.get("rows_viewed", 0),
    }


def _content_access_problems(access):
    if access is None:
        return []
    if not isinstance(access, dict):
        return ["content_access must be an object"]
    problems = [
        f"content_access: unknown key: {key}"
        for key in sorted(set(access) - {"consent", "rows_viewed"})
    ]
    consent = access.get("consent", "aggregates-only")
    if consent not in CONSENTS:
        problems.append(f"content_access.consent must be one of {', '.join(CONSENTS)}")
    viewed = access.get("rows_viewed", 0)
    if isinstance(viewed, bool) or not isinstance(viewed, int) or viewed < 0:
        problems.append("content_access.rows_viewed must be a whole number, 0 or more")
    elif viewed > SAMPLE_ROWS_PER_SESSION:
        problems.append(
            f"content_access.rows_viewed is over the limit of {SAMPLE_ROWS_PER_SESSION} "
            "sample rows per session"
        )
    elif viewed and consent != "samples-approved":
        problems.append(
            "content_access.rows_viewed must be 0 unless consent is samples-approved"
        )
    return problems


def _outcome_signal_problems(signals):
    if signals is None:
        return []
    if not isinstance(signals, list):
        return ["outcome_signals must be a list, or null when the outcome step was skipped"]
    problems = []
    if len(signals) > OUTCOME_SIGNALS_MAX:
        problems.append(f"outcome_signals holds at most {OUTCOME_SIGNALS_MAX} entries")
    required = ("description", "location")
    optional = ("join_key", "typical_delay")
    for index, signal in enumerate(signals):
        where = f"outcome_signals[{index}]"
        if not isinstance(signal, dict):
            problems.append(f"{where} must be an object")
            continue
        allowed = {"kind", "arrives", "source", *required, *optional}
        for key in sorted(set(signal) - allowed):
            problems.append(f"{where}: unknown key: {key}")
        if signal.get("kind") not in OUTCOME_KINDS:
            problems.append(f"{where}.kind must be one of {', '.join(OUTCOME_KINDS)}")
        if signal.get("arrives") not in OUTCOME_ARRIVES:
            problems.append(f"{where}.arrives must be one of {', '.join(OUTCOME_ARRIVES)}")
        if signal.get("source", "user") not in ANSWER_SOURCES:
            problems.append(f"{where}.source must be one of {', '.join(ANSWER_SOURCES)}")
        for key in required + optional:
            value = signal.get(key)
            if value is None and key in optional:
                continue
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{where}.{key} must be a non-empty string")
            elif len(value) > 200:
                problems.append(f"{where}.{key} must be at most 200 characters")
    return problems


def _text_problem(where, value, limit):
    if not isinstance(value, str) or not value.strip():
        return f"{where} must be a non-empty string"
    if len(value) > limit:
        return f"{where} must be at most {limit} characters"
    return None


def _code_finding_problems(findings):
    if findings is None:
        return []
    if not isinstance(findings, list):
        return ["code_findings must be a list"]
    problems, seen = [], set()
    for index, finding in enumerate(findings):
        where = f"code_findings[{index}]"
        if not isinstance(finding, dict):
            problems.append(f"{where} must be an object")
            continue
        for key in sorted(set(finding) - {"check", "finding", "files", "since", "suggests"}):
            problems.append(f"{where}: unknown key: {key}")
        if finding.get("check") not in CHECK_IDS:
            problems.append(f"{where}.check must be one of {', '.join(CHECK_IDS)}")
        elif finding["check"] in seen:
            problems.append(f"{where}: more than one finding for {finding['check']}")
        seen.add(finding.get("check"))
        problem = _text_problem(f"{where}.finding", finding.get("finding"), FINDING_MAX)
        if problem:
            problems.append(problem)
        if finding.get("suggests") not in STATUSES:
            problems.append(f"{where}.suggests must be one of {', '.join(STATUSES)}")
        files = finding.get("files", [])
        if not isinstance(files, list) or any(not isinstance(f, str) or not f for f in files):
            problems.append(f"{where}.files must be a list of file names")
        since = finding.get("since")
        if since is not None and (
            not isinstance(since, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since)
        ):
            problems.append(f"{where}.since must be a YYYY-MM-DD date")
    return problems


def _logging_change_problems(changes):
    if changes is None:
        return []
    if not isinstance(changes, list):
        return ["logging_changes must be a list"]
    problems = []
    if len(changes) > LOGGING_CHANGES_MAX:
        problems.append(f"logging_changes holds at most {LOGGING_CHANGES_MAX} entries")
    for index, change in enumerate(changes):
        where = f"logging_changes[{index}]"
        if not isinstance(change, dict):
            problems.append(f"{where} must be an object")
            continue
        for key in sorted(set(change) - {"check", "change", "file"}):
            problems.append(f"{where}: unknown key: {key}")
        if change.get("check") not in CHECK_IDS:
            problems.append(f"{where}.check must be one of {', '.join(CHECK_IDS)}")
        problem = _text_problem(f"{where}.change", change.get("change"), FINDING_MAX)
        if problem:
            problems.append(problem)
        if change.get("file") is not None and (
            not isinstance(change["file"], str) or not change["file"]
        ):
            problems.append(f"{where}.file must be a file name")
    return problems


def outcome_signals(profile):
    signals = profile.get("outcome_signals")
    if signals is None:
        return None
    return [
        {
            "kind": signal["kind"],
            "description": signal["description"],
            "location": signal["location"],
            "join_key": signal.get("join_key"),
            "arrives": signal["arrives"],
            "typical_delay": signal.get("typical_delay"),
            "source": signal.get("source", "user"),
        }
        for signal in signals
    ]


def validate_profile(profile):
    problems = []
    if not isinstance(profile, dict):
        return ["profile must be a JSON object"]
    allowed = {
        "profile_version",
        "workflow",
        "as_of",
        "sources",
        "content_access",
        "outcome_signals",
        "code_findings",
        "logging_changes",
        "answers",
        *PROFILE_FIELDS,
    }
    for key in sorted(set(profile) - allowed):
        problems.append(f"unknown key: {key}")
    if profile.get("profile_version") != PROFILE_VERSION:
        problems.append(f"profile_version must be {PROFILE_VERSION!r}")
    workflow = profile.get("workflow")
    if not isinstance(workflow, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", workflow):
        problems.append("workflow must be a lowercase slug such as prior-auth-review")
    as_of = profile.get("as_of")
    if not isinstance(as_of, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        problems.append("as_of must be a YYYY-MM-DD date")
    sources = profile.get("sources")
    if not isinstance(sources, list) or not sources:
        problems.append("sources must list at least one source")
    else:
        for index, source in enumerate(sources):
            where = f"sources[{index}]"
            if not isinstance(source, dict):
                problems.append(f"{where} must be an object")
                continue
            for key in sorted(set(source) - {"type", "location", "objects", "note"}):
                problems.append(f"{where}: unknown key: {key}")
            if source.get("type") not in SOURCE_TYPES:
                problems.append(f"{where}.type must be one of {', '.join(SOURCE_TYPES)}")
            if not isinstance(source.get("location"), str) or not source.get("location"):
                problems.append(f"{where}.location must be a non-empty string")
            objects = source.get("objects", [])
            if not isinstance(objects, list) or any(not isinstance(o, str) for o in objects):
                problems.append(f"{where}.objects must be a list of strings")
            if "note" in source and not isinstance(source["note"], str):
                problems.append(f"{where}.note must be a string")
            elif len(source.get("note", "")) > SOURCE_NOTE_MAX:
                problems.append(
                    f"{where}.note must be at most {SOURCE_NOTE_MAX} characters; put what the "
                    "code shows for a check in code_findings and changes to make in "
                    "logging_changes"
                )
    problems += _content_access_problems(profile.get("content_access"))
    problems += _outcome_signal_problems(profile.get("outcome_signals"))
    problems += _code_finding_problems(profile.get("code_findings"))
    problems += _logging_change_problems(profile.get("logging_changes"))
    for section, fields in PROFILE_FIELDS.items():
        data = profile.get(section, {})
        if data is None:
            continue
        if not isinstance(data, dict):
            problems.append(f"{section} must be an object")
            continue
        for key in sorted(set(data) - set(fields)):
            problems.append(f"{section}: unknown key: {key}")
        for name, kind in fields.items():
            problem = _field_problem(kind, data.get(name))
            if problem:
                problems.append(f"{section}.{name} {problem}")
    answers = profile.get("answers", [])
    if not isinstance(answers, list):
        problems.append("answers must be a list")
    else:
        seen = set()
        for index, answer in enumerate(answers):
            where = f"answers[{index}]"
            if not isinstance(answer, dict):
                problems.append(f"{where} must be an object")
                continue
            for key in sorted(set(answer) - {"check", "question", "answer", "status", "source"}):
                problems.append(f"{where}: unknown key: {key}")
            if answer.get("check") not in CHECK_IDS:
                problems.append(f"{where}.check must be one of {', '.join(CHECK_IDS)}")
            elif answer["check"] in seen:
                problems.append(f"{where}: more than one answer for {answer['check']}")
            seen.add(answer.get("check"))
            if answer.get("status") not in STATUSES:
                problems.append(f"{where}.status must be one of {', '.join(STATUSES)}")
            for key in ("question", "answer"):
                if not isinstance(answer.get(key), str) or not answer.get(key).strip():
                    problems.append(f"{where}.{key} must be a non-empty string")
            if answer.get("source", "user") not in ANSWER_SOURCES:
                problems.append(f"{where}.source must be one of {', '.join(ANSWER_SOURCES)}")
    return problems


# ---------------------------------------------------------------------------
# Verdict rules


def _section(profile, name):
    data = profile.get(name) or {}
    return {field: data.get(field) for field in PROFILE_FIELDS[name]}


def evaluate_checks(profile):
    """Return the ten checks as {id, status, evidence, answered_by}."""
    d = _section(profile, "decisions")
    a = _section(profile, "actions")
    o = _section(profile, "outcomes")
    results = {}

    def put(check_id, status, evidence, resolved=True):
        # Nothing measured is not the same as measured and absent.
        if not resolved and status == "missing":
            status = "not-measured"
        results[check_id] = {
            "id": check_id,
            "status": status,
            "evidence": evidence,
            "answered_by": "probe" if resolved else "unresolved",
            "code": None,
        }

    # 1. Suggestion stored separately.
    coverage = d["suggestion_coverage"]
    if coverage is None:
        put("suggestion_separate", "missing", "No separate suggestion field was measured.", False)
    else:
        put(
            "suggestion_separate",
            coverage_status(coverage),
            f"A suggestion field distinct from the final output is populated on {pct(coverage)} "
            "of decisions.",
        )

    # 2. Suggestion not overwritten.
    when_edited = d["suggestion_equals_final_rate_when_edited"]
    modified = d["suggestion_modified_after_action_rate"]
    equal = d["suggestion_equals_final_rate"]
    if coverage is None or coverage < LOW:
        put(
            "suggestion_not_overwritten",
            "missing",
            "No stored suggestion to compare against the final output.",
            coverage is not None,
        )
    elif when_edited is not None or modified is not None:
        parts, statuses = [], []
        for value, text in (
            (when_edited, "of edited or overridden decisions still hold a stored suggestion "
             "identical to the final output"),
            (modified, "of suggestions were modified after the first human action"),
        ):
            if value is None:
                continue
            parts.append(f"{pct(value)} {text}")
            if value <= OVERWRITE_CLEAN:
                statuses.append("present")
            elif value <= OVERWRITE_BAD:
                statuses.append("partial")
            else:
                statuses.append("missing")
        put("suggestion_not_overwritten", worst(statuses), "; ".join(parts).capitalize() + ".")
    elif equal is not None:
        put(
            "suggestion_not_overwritten",
            "partial",
            f"The stored suggestion equals the final output on {pct(equal)} of decisions. "
            "Equality alone cannot tell acceptance from overwrite or later regeneration; "
            "this needs an answer.",
            False,
        )
    else:
        put(
            "suggestion_not_overwritten",
            "partial",
            "A suggestion is stored, but nothing measured shows whether it is the text the "
            "reviewer saw. This needs an answer.",
            False,
        )

    # 3. Exposure.
    exposure = d["exposure_counts"]
    if exposure is None:
        put("exposure_known", "missing", "No exposure field was measured.", False)
    else:
        total = sum(exposure.values())
        known = ratio(exposure["shown"] + exposure["hidden"], total) or 0.0
        put(
            "exposure_known",
            coverage_status(known),
            f"Exposure is recorded as shown or hidden on {pct(known)} of decisions "
            f"(shown {num(exposure['shown'])}, hidden {num(exposure['hidden'])}, "
            f"unknown {num(exposure['unknown'])}).",
        )

    # 4. Timestamps.
    stamps = [
        ("decided_at", d["decided_at_coverage"], True),
        ("action event_at", a["event_at_coverage"], True),
        ("action observed_at", a["observed_at_coverage"], False),
        ("outcome observed_at", o["observed_at_coverage"], False),
    ]
    measured = [(name, value, core) for name, value, core in stamps if value is not None]
    if not measured:
        put("timestamps", "missing", "No timestamp coverage was measured.", False)
    else:
        evidence = "Coverage: " + ", ".join(f"{name} {pct(value)}" for name, value, _ in measured)
        unmeasured = [name for name, value, _ in stamps if value is None]
        if unmeasured:
            evidence += f". Not measured: {', '.join(unmeasured)}"
        decided = coverage_status(d["decided_at_coverage"]) or "missing"
        if decided == "missing":
            status = "missing"
        elif all(value is not None and value >= HIGH for _, value, _ in stamps):
            status = "present"
        else:
            status = "partial"
        put("timestamps", status, evidence + ".")

    # 5. Reviewer ID.
    actor = a["actor_coverage"]
    if actor is None:
        put("reviewer_id", "missing", "No reviewer ID field was measured.", False)
    else:
        put(
            "reviewer_id",
            coverage_status(actor),
            f"A reviewer ID is present on {pct(actor)} of human actions "
            f"({num(a['distinct_actors'])} distinct). Stability over time is not probed.",
        )

    # 6. Policy identity.
    policy = [
        ("model", d["policy_model_coverage"]),
        ("prompt_version", d["policy_prompt_version_coverage"]),
        ("code_version", d["policy_code_version_coverage"]),
    ]
    if all(value is None for _, value in policy):
        put("policy_identity", "missing", "No model, prompt or code version was measured.", False)
    else:
        evidence = "Coverage: " + ", ".join(f"{name} {pct(value)}" for name, value in policy) + "."
        values = [value or 0.0 for _, value in policy]
        if all(value >= HIGH for value in values):
            status = "present"
        elif any(value >= LOW for value in values):
            status = "partial"
        else:
            status = "missing"
        put("policy_identity", status, evidence)

    # 7. Frozen context.
    context, snapshot = d["context_coverage"], d["context_snapshot_coverage"]
    if context is None:
        put("frozen_context", "missing", "No stored input context was measured.", False)
    else:
        evidence = (
            f"Input context is stored on {pct(context)} of decisions; a snapshot time at or "
            f"before the decision is recorded on {pct(snapshot)}."
        )
        if context >= HIGH and snapshot is not None and snapshot >= HIGH:
            status = "present"
        elif context >= LOW:
            status = "partial"
        else:
            status = "missing"
        put("frozen_context", status, evidence)

    # 8. Outcome join.
    joined = o["decisions_with_outcome_rate"]
    if joined is None:
        put("outcome_join", "missing", "No decision-to-outcome join was measured.", False)
    else:
        status = coverage_status(joined, OUTCOME_HIGH, OUTCOME_LOW)
        evidence = (
            f"{pct(joined)} of decisions join to at least one outcome; lag p50 "
            f"{num(o['lag_days_p50'])} days, p90 {num(o['lag_days_p90'])} days."
        )
        late = o["lag_days_p90"] is not None and o["lag_days_p90"] > LATE_LAG_DAYS
        if late and status == "present":
            status = "partial"
            evidence += f" The p90 lag is over {LATE_LAG_DAYS} days."
        put("outcome_join", status, evidence)

    # 9. Volume and accept-unchanged rate.
    weekly, accept = d["weekly_volume_median"], a["accept_unchanged_rate"]
    known = [value is not None for value in (weekly, accept)]
    basis = f" (by {a['accept_unchanged_basis']})" if a["accept_unchanged_basis"] else ""
    evidence = (
        f"Median weekly volume {num(weekly)} over {num(d['weeks_observed'])} weeks; "
        f"accept-unchanged rate {pct(accept)}{basis}."
    )
    if accept is not None and accept >= RUBBER_STAMP:
        evidence += " Rubber-stamp risk: nearly every suggestion is accepted unchanged."
    status = "present" if all(known) else "partial" if any(known) else "missing"
    put("volume_accept_rate", status, evidence, any(known))

    # 10. Request-type signal.
    content, category = d["request_content_coverage"], d["category_coverage"]
    cardinality = d["category_cardinality"]
    if content is None and category is None:
        put("request_type_signal", "missing", "No request content or category was measured.", False)
    else:
        field = d["category_field"] or "none"
        evidence = (
            f"Request content is present on {pct(content)} of decisions. Category field: "
            f"{field}, coverage {pct(category)}, {num(cardinality)} distinct values."
        )
        usable_category = (
            category is not None
            and category >= OUTCOME_HIGH
            and cardinality is not None
            and 2 <= cardinality <= CATEGORY_MAX_VALUES
        )
        if (content or 0.0) >= HIGH and usable_category:
            status = "present"
        elif (content or 0.0) >= LOW or (category or 0.0) >= LOW:
            status = "partial"
            evidence += (
                " Classes are not directly derivable from an existing field; "
                "taxonomy work is needed."
                if not usable_category
                else " Request content is incomplete."
            )
        else:
            status = "missing"
        put("request_type_signal", status, evidence)

    for answer in profile.get("answers") or []:
        result = results[answer["check"]]
        result["status"] = answer["status"]
        result["answered_by"] = answer.get("source", "user")
        label = "From the code" if result["answered_by"] == "code" else "Answered"
        result["evidence"] = (
            f"{result['evidence']} Asked: {answer['question'].strip()} "
            f"{label}: {answer['answer'].strip()}"
        )
    for finding in profile.get("code_findings") or []:
        results[finding["check"]]["code"] = {
            "finding": finding["finding"].strip(),
            "files": list(finding.get("files", [])),
            "since": finding.get("since"),
            "suggests": finding["suggests"],
        }
    return [results[check_id] for check_id in CHECK_IDS]


def record_types(profile, checks):
    status = {check["id"]: check["status"] for check in checks}
    d = _section(profile, "decisions")
    a = _section(profile, "actions")

    def judge(blocking, gaps, extra=()):
        blocking = list(blocking) + list(extra)
        missing = [name for name, value in blocking if value == "missing"]
        partial = [name for name, value in blocking if value == "partial"]
        unmeasured = [name for name, value in blocking if value == "not-measured"]
        gap_names = [name for name in gaps if status[name] != "present"]
        if missing:
            verdict = "not-minable"
        elif unmeasured:
            verdict = "not-measured"
        elif partial:
            verdict = "partial"
        else:
            verdict = "minable"
        reasons = [f"{name} is missing" for name in missing]
        reasons += [f"{name} is partial" for name in partial]
        reasons += [f"{name} was not measured" for name in unmeasured]
        reasons += [
            f"{name} was not measured"
            if status[name] == "not-measured"
            else f"{name} is {status[name]} (does not block mining)"
            for name in gap_names
        ]
        return {"status": verdict, "reasons": reasons}

    decided = coverage_status(d["decided_at_coverage"]) or "not-measured"
    decision = judge(
        [(name, status[name]) for name in ("suggestion_separate", "suggestion_not_overwritten")],
        ["policy_identity", "frozen_context", "request_type_signal"],
        [("decided_at", decided)],
    )
    # Any joined human action is minable; decisions still awaiting review do not count against it.
    joined = a["decisions_with_action_rate"]
    has_actions = "not-measured" if joined is None else "present" if joined else "missing"
    event_at = coverage_status(a["event_at_coverage"]) or "not-measured"
    human_action = judge(
        [("human actions joined to decisions", has_actions), ("action event_at", event_at)],
        [],
    )
    weak = [name for name in ("exposure_known", "reviewer_id") if status[name] != "present"]
    if weak and human_action["status"] in ("minable", "partial"):
        human_action["status"] = "partial"
    if weak and human_action["status"] != "not-minable":
        human_action["reasons"] += [
            f"{name} was not measured"
            if status[name] == "not-measured"
            else f"{name} is {status[name]} (evidence stays Weak)"
            for name in weak
        ]
    outcome = judge([("outcome_join", status["outcome_join"])], [])
    signals = profile.get("outcome_signals")
    if signals == []:
        outcome["reasons"].append("no outcome signal exists yet; start capturing one")
    elif signals:
        if signals[0]["arrives"] == "only-on-problems":
            outcome["reasons"].append(
                "the outcome is recorded only when something goes wrong, so a decision "
                "without one is not known to be right"
            )
        elif signals[0]["arrives"] == "some-decisions":
            outcome["reasons"].append("only a subset of decisions gets an outcome")
    return {"decision": decision, "human_action": human_action, "outcome": outcome}


def roll_up(types):
    if types["decision"]["status"] == "not-minable":
        return "log-forward-only"
    if "not-measured" in (types["decision"]["status"], types["human_action"]["status"]):
        return "not-measured"
    if types["decision"]["status"] == "minable" and types["human_action"]["status"] == "minable":
        return "mine-history"
    return "partial"


def label_estimate(profile, blind_sample_rate):
    d = _section(profile, "decisions")
    weekly = d["weekly_volume_median"]

    def weeks(labels, volume):
        if not volume or volume <= 0:
            return None
        return math.ceil(labels / (volume * blind_sample_rate))

    rows = [
        {"max_error_rate": rate, "labels_needed": labels, "weeks": weeks(labels, weekly)}
        for rate, labels in LABEL_TABLE
    ]
    per_class = []
    values = d["category_values"] or []
    total = d["rows"] or sum(item["count"] for item in values)
    if weekly and total:
        for item in sorted(values, key=lambda item: -item["count"])[:CLASS_LIMIT]:
            class_weekly = round(weekly * item["count"] / total, 2)
            if class_weekly <= 0:
                continue
            per_class.append(
                {
                    "value": item["value"],
                    "weekly_volume": class_weekly,
                    "weeks": [weeks(labels, class_weekly) for _, labels in LABEL_TABLE],
                }
            )
    return {
        "basis": LABEL_BASIS,
        "blind_sample_rate": blind_sample_rate,
        "weekly_volume": weekly,
        "rows": rows,
        "per_class": per_class,
    }


THEN_TEXT = {
    "not-measured": (
        "Measure the stores listed above and rerun discovery. Until then this report says "
        "what the code does today, not whether the history can be mined."
    ),
    "log-forward-only": (
        "Make the logging changes, let new history build up for a few weeks, then rerun "
        "discovery."
    ),
    "partial": (
        "The parts marked minable can be mapped into Hindsight records now. Make the logging "
        "changes so new history closes the gaps."
    ),
    "mine-history": (
        "The history can be mapped into Hindsight Decision and HumanAction records now."
    ),
}
KEEP_PROFILE = (
    " Keep `profile.json`: its sources and column names are the starting point for mapping "
    "this history into Hindsight records, a step that is not automated yet."
)


def next_steps(profile, checks, verdict):
    """What to measure, what to start logging (most valuable first), and what comes after."""
    measure = []
    for source in profile["sources"]:
        if source["type"] == "unsupported":
            note = source.get("note") or "Export it to CSV or JSONL, or copy it to a warehouse."
            measure.append(f"`{source['location']}`: {note}")
    unmeasured = [check["id"] for check in checks if check["status"] == "not-measured"]
    if unmeasured:
        measure.append(
            "Not measured yet: "
            + ", ".join(unmeasured)
            + ". Profile the stores that hold them (CSV, JSONL or SQLite files, or a warehouse)."
        )
    by_id = {check["id"]: check for check in checks}
    changes = {}
    for change in profile.get("logging_changes") or []:
        changes.setdefault(change["check"], []).append(
            {"change": change["change"].strip(), "file": change.get("file")}
        )
    start_logging = []
    for check_id, advice in LOGGING_ADVICE:
        check = by_id[check_id]
        code = check["code"]
        if check["status"] in ("missing", "partial"):
            status, basis = check["status"], check["answered_by"]
            if basis in ("probe", "unresolved"):
                basis = "measured"
        elif check["status"] == "not-measured" and code and code["suggests"] != "present":
            status, basis = code["suggests"], "code, not yet measured"
        elif check_id in changes:
            status, basis = check["status"], "suggested from the code"
        else:
            continue
        start_logging.append(
            {
                "check": check_id,
                "status": status,
                "basis": basis,
                "advice": advice,
                "changes": changes.get(check_id, []),
            }
        )
    return {
        "measure": measure,
        "start_logging": start_logging,
        "then": THEN_TEXT[verdict] + KEEP_PROFILE,
    }


def build_readiness(profile, blind_sample_rate=0.10):
    problems = validate_profile(profile)
    if problems:
        raise UsageError("invalid profile:\n  " + "\n  ".join(problems))
    if not 0 < blind_sample_rate <= 1:
        raise UsageError("--blind-sample-rate must be greater than 0 and at most 1")
    d = _section(profile, "decisions")
    a = _section(profile, "actions")
    checks = evaluate_checks(profile)
    types = record_types(profile, checks)
    flags = []
    if a["accept_unchanged_rate"] is not None and a["accept_unchanged_rate"] >= RUBBER_STAMP:
        flags.append(
            f"Accept-unchanged rate is {pct(a['accept_unchanged_rate'])}: rubber-stamp risk."
        )
    stampers = [
        r["reviewer"]
        for r in a["reviewers"] or []
        if r.get("accept_unchanged_rate") is not None
        and r["accept_unchanged_rate"] >= RUBBER_STAMP
        and r["actions"] >= 20
    ]
    if stampers:
        flags.append(
            f"Reviewers accepting at least {pct(RUBBER_STAMP)} unchanged over 20 or more "
            f"actions: {', '.join(stampers)}."
        )
    if d["duplicate_id_rate"]:
        flags.append(f"{pct(d['duplicate_id_rate'])} of decision rows repeat a decision ID.")
    classes = None
    if d["category_field"] or d["category_coverage"] is not None:
        classes = {
            "field": d["category_field"],
            "coverage": d["category_coverage"],
            "cardinality": d["category_cardinality"],
            "values": d["category_values"],
            "suppressed": d["category_values_suppressed"],
        }
    sources = []
    for source in profile["sources"]:
        entry = {
            "type": source["type"],
            "location": source["location"],
            "objects": list(source.get("objects", [])),
        }
        if source.get("note"):
            entry["note"] = source["note"]
        sources.append(entry)
    readiness = {
        "schema_version": SCHEMA_VERSION,
        "workflow": profile["workflow"],
        "as_of": profile["as_of"],
        "sources": sources,
        "content_access": content_access(profile),
        "outcome_signals": outcome_signals(profile),
        "checks": checks,
        "record_types": types,
        "verdict": roll_up(types),
        "next_steps": next_steps(profile, checks, roll_up(types)),
        "volume": {
            "decisions": d["rows"],
            "weeks_observed": d["weeks_observed"],
            "weekly_median": d["weekly_volume_median"],
            "first_decided_at": d["first_decided_at"],
            "last_decided_at": d["last_decided_at"],
            "accept_unchanged_rate": a["accept_unchanged_rate"],
            "accept_unchanged_basis": a["accept_unchanged_basis"],
            "kind_counts": a["kind_counts"],
            "reviewers": a["reviewers"] or [],
            "classes": classes,
            "flags": flags,
        },
        "label_estimate": label_estimate(profile, blind_sample_rate),
    }
    problems = check_readiness(readiness)
    if problems:  # pragma: no cover - guards against a rule/schema mismatch
        raise UsageError("built readiness failed its own check:\n  " + "\n  ".join(problems))
    return readiness


# ---------------------------------------------------------------------------
# readiness.md

VERDICT_TEXT = {
    "mine-history": "The stored history can be mined into Decision and HumanAction records.",
    "partial": (
        "Some history can be mined, but the gaps below limit what it supports. "
        "Start logging the missing fields now."
    ),
    "log-forward-only": (
        "The stored history cannot be mined: the AI suggestion as shown is not recoverable. "
        "Log forward from today."
    ),
    "not-measured": (
        "The stored history has not been measured, so this is not yet a verdict on whether "
        "it can be mined. The checks below show what the code does today; the next steps say "
        "what to measure."
    ),
}


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(_cell(cell) for cell in row) + " |" for row in rows]
    return lines


ARRIVES_TEXT = {
    "every-decision": "Every decision",
    "only-on-problems": "Only when something goes wrong",
    "some-decisions": "A subset of decisions",
    "unknown": "Unknown",
}


def _outcome_section(readiness):
    signals = readiness["outcome_signals"]
    status = readiness["record_types"]["outcome"]["status"]
    out = [
        "",
        "## Outcome: what later showed the decision was right or wrong",
        "",
        "A reviewer's approval is weak evidence: people approve quickly and make their own "
        "mistakes. The outcome is what happened afterwards, often days or weeks later and "
        "often stored somewhere else. It is what makes this history safe to evaluate and "
        "train from.",
        "",
    ]
    if signals is None:
        out.append("Not discussed. No outcome signal was identified with the user.")
        return out
    if not signals:
        out += [
            "No outcome signal exists yet. To start: record one event per decision with the "
            "decision ID, what happened and when it was observed. A later second review of a "
            "small random sample is a light way to begin.",
        ]
        return out
    out += _table(
        ["Signal", "Kind", "Where", "Joins on", "Arrives for", "Usual delay", "From"],
        [
            [
                signal["description"],
                signal["kind"],
                signal["location"],
                signal["join_key"] or "no shared key yet",
                ARRIVES_TEXT[signal["arrives"]],
                signal["typical_delay"] or "unknown",
                signal["source"],
            ]
            for signal in signals
        ],
    )
    out += ["", f"Measured Outcome status: `{status}`. Signals are listed most trusted first."]
    if any(signal["arrives"] == "only-on-problems" for signal in signals):
        out += [
            "",
            "A signal recorded only when something goes wrong shows low coverage by design. "
            "A decision without one is not known to be right.",
        ]
    if any(not signal["join_key"] for signal in signals):
        out += ["", "A signal with no shared key cannot be joined to decisions yet."]
    return out


def _code_cell(code):
    if not code:
        return ""
    text = f"Suggests {code['suggests']}"
    if code["since"]:
        text += f" since {code['since']}"
    text += f": {code['finding']}"
    if code["files"]:
        text += " (" + ", ".join(f"`{name}`" for name in code["files"]) + ")"
    return text


def _next_steps_section(steps):
    out = ["", "## What to do next", ""]
    number = 1
    if steps["measure"]:
        out += [f"### {number}. Measure the stored history", ""]
        out += [f"- {item}" for item in steps["measure"]]
        out.append("")
        number += 1
    out += [f"### {number}. Start logging, most valuable first", ""]
    if steps["start_logging"]:
        for index, item in enumerate(steps["start_logging"], 1):
            out.append(
                f"{index}. **{CHECK_TITLES[item['check']]}** ({item['status']}; "
                f"{item['basis']}). {item['advice']}"
            )
            for change in item["changes"]:
                where = f" (`{change['file']}`)" if change["file"] else ""
                out.append(f"   - {change['change']}{where}")
    else:
        out.append("Nothing to add: no check that was measured or read from the code has a gap.")
    out += ["", f"### {number + 1}. After that", "", steps["then"]]
    return out


def _content_line(access):
    if access["consent"] != "samples-approved":
        return "Aggregates only; no row content was read."
    if not access["rows_viewed"]:
        return (
            "The user approved viewing sample rows, but none was viewed. "
            "This report holds aggregates only."
        )
    return (
        f"With the user's approval, {access['rows_viewed']} sample rows were viewed in the "
        "session to resolve ambiguity. This report holds aggregates only; no row content "
        "is recorded here."
    )


def render_markdown(readiness):
    volume = readiness["volume"]
    estimate = readiness["label_estimate"]
    out = [
        f"# Workflow readiness: {readiness['workflow']}",
        "",
        f"As of {readiness['as_of']}. {_content_line(readiness['content_access'])}",
        "",
        f"## Verdict: `{readiness['verdict']}`",
        "",
        VERDICT_TEXT[readiness["verdict"]],
        "",
    ]
    suggested = Counter(
        check["code"]["suggests"]
        for check in readiness["checks"]
        if check["status"] == "not-measured" and check["code"]
    )
    if suggested:
        out += [
            "From the code alone, for the checks not yet measured: "
            + ", ".join(f"{suggested[name]} look {name}" for name in STATUSES if suggested[name])
            + ". Code shows what is written today, not how complete the history is.",
            "",
        ]
    names = {"decision": "Decision", "human_action": "HumanAction", "outcome": "Outcome"}
    out += _table(
        ["Record type", "Status", "Why"],
        [
            [names[key], value["status"], "; ".join(value["reasons"]) or "All required checks present"]
            for key, value in readiness["record_types"].items()
        ],
    )
    out += _next_steps_section(readiness["next_steps"])
    out += ["", "## Sources", ""]
    for source in readiness["sources"]:
        line = f"- {source['type']}: `{source['location']}`"
        if source["objects"]:
            line += " (" + ", ".join(f"`{name}`" for name in source["objects"]) + ")"
        if source.get("note"):
            line += f". {source['note']}"
        out.append(line)
    out += ["", "## Checks", ""]
    out += [
        "Status comes from measurements or answers. \"What the code shows\" is what the "
        "workflow's code writes today; it does not change the status.",
        "",
    ]
    out += _table(
        ["#", "Check", "Status", "Answered by", "Evidence", "What the code shows"],
        [
            [
                index,
                CHECK_TITLES[check["id"]],
                check["status"],
                check["answered_by"],
                check["evidence"],
                _code_cell(check["code"]),
            ]
            for index, check in enumerate(readiness["checks"], 1)
        ],
    )
    unresolved = [
        check
        for check in readiness["checks"]
        if check["answered_by"] == "unresolved" and check["status"] != "not-measured"
    ]
    if unresolved:
        out += ["", "### Needs an answer", ""]
        out += [f"- {CHECK_TITLES[check['id']]}: {check['evidence']}" for check in unresolved]

    out += [
        "",
        "## AI suggestion versus human action",
        "",
        "Descriptive only. **Not evidence of correctness**: a reviewer accepting a suggestion "
        "does not show the suggestion was right.",
        "",
    ]
    kinds = volume["kind_counts"]
    if kinds and sum(kinds.values()):
        total = sum(kinds.values())
        out += _table(
            ["Human action", "Decisions", "Share"],
            [[kind, num(kinds[kind]), pct(kinds[kind] / total)] for kind in KINDS if kinds.get(kind)],
        )
        out.append("")
    basis = f" (by {volume['accept_unchanged_basis']})" if volume["accept_unchanged_basis"] else ""
    out.append(f"Accept-unchanged rate: {pct(volume['accept_unchanged_rate'])}{basis}.")

    out += _outcome_section(readiness)

    out += ["", "## Rubber-stamp indicators by reviewer", ""]
    if volume["reviewers"]:
        out += [
            "Reviewers are shown as ordinals, most active first.",
            "",
        ]
        out += _table(
            ["Reviewer", "Actions", "Accept-unchanged", "Median seconds to action"],
            [
                [
                    r["reviewer"],
                    num(r["actions"]),
                    pct(r.get("accept_unchanged_rate")),
                    num(r.get("median_seconds_to_action")),
                ]
                for r in volume["reviewers"]
            ],
        )
    else:
        out.append("Not measured.")
    for flag in volume["flags"]:
        out += ["", f"- {flag}"]

    out += ["", "## Volume and request types", ""]
    out.append(
        f"{num(volume['decisions'])} decisions from {volume['first_decided_at'] or 'unknown'} "
        f"to {volume['last_decided_at'] or 'unknown'}; median {num(volume['weekly_median'])} "
        f"per week over {num(volume['weeks_observed'])} weeks."
    )
    classes = volume["classes"]
    if classes:
        out += [
            "",
            f"Category field `{classes['field'] or 'unnamed'}`: coverage "
            f"{pct(classes['coverage'])}, {num(classes['cardinality'])} distinct values.",
        ]
        if classes["values"]:
            out.append("")
            out += _table(
                ["Value", "Decisions"],
                [[item["value"], num(item["count"])] for item in classes["values"]],
            )
        elif classes["suppressed"]:
            out += ["", f"Values not listed: {classes['suppressed']}"]

    out += [
        "",
        "## Labels needed",
        "",
        estimate["basis"],
        "",
        f"Weeks assume median weekly volume {num(estimate['weekly_volume'])} and a blind-sample "
        f"rate of {pct(estimate['blind_sample_rate'])}.",
        "",
    ]
    out += _table(
        ["Error-rate bound (zero errors seen)", "Labels needed", "Weeks to collect"],
        [
            [pct(row["max_error_rate"]), row["labels_needed"], num(row["weeks"])]
            for row in estimate["rows"]
        ],
    )
    if estimate["per_class"]:
        out += ["", "Weeks to collect per class, from the category field above:", ""]
        out += _table(
            ["Class", "Weekly volume"] + [pct(rate) for rate, _ in LABEL_TABLE],
            [
                [item["value"], num(item["weekly_volume"])] + [num(w) for w in item["weeks"]]
                for item in estimate["per_class"]
            ],
        )
    out += ["", "## What discovery cannot do", ""]
    out += [f"- {line}" for line in CANNOT_DO]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# check: a small JSON Schema subset, so the schema file stays the single contract


def _type_ok(value, name):
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return is_number(value)
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    raise UsageError(f"schema uses unsupported type {name!r}")


def _validate(value, schema, root, path, problems):
    if "$ref" in schema:
        target = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            target = target[part]
        return _validate(value, target, root, path, problems)
    if "const" in schema and value != schema["const"]:
        problems.append(f"{path}: must be {schema['const']!r}")
        return
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} is not one of {schema['enum']}")
        return
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, name) for name in types):
            problems.append(f"{path}: expected {' or '.join(types)}")
            return
    if is_number(value):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            problems.append(f"{path}: must be greater than {schema['exclusiveMinimum']}")
    if isinstance(value, str) and "pattern" in schema and not re.search(schema["pattern"], value):
        problems.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            problems.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            problems.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate(item, schema["items"], root, f"{path}[{index}]", problems)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{path}: missing {name}")
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                _validate(item, properties[name], root, f"{path}.{name}", problems)
            elif extra is False:
                problems.append(f"{path}: unknown key {name}")
            elif isinstance(extra, dict):
                _validate(item, extra, root, f"{path}.{name}", problems)


def check_readiness(readiness):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    problems = []
    _validate(readiness, schema, schema, "readiness", problems)
    if problems:
        return problems
    ids = [check["id"] for check in readiness["checks"]]
    if ids != CHECK_IDS:
        problems.append("readiness.checks: must hold the ten checks once each, in order")
    expected = roll_up(readiness["record_types"])
    if readiness["verdict"] != expected:
        problems.append(
            f"readiness.verdict: {readiness['verdict']!r} does not follow from record_types "
            f"(expected {expected!r})"
        )
    return problems


# ---------------------------------------------------------------------------
# profile-files


def _empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("", "null", "\\n")
    if isinstance(value, (dict, list)):
        return not value
    return False


def _get(row, path):
    if not path:
        return None
    if path in row:
        return row[path]
    value = row
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _fingerprint(value):
    if _empty(value):
        return None
    if isinstance(value, str):
        text = " ".join(value.split())
    else:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).digest()


def _time(value):
    if _empty(value) or isinstance(value, bool):
        return None
    if is_number(value) or (isinstance(value, str) and re.fullmatch(r"\d{9,13}", value.strip())):
        seconds = float(value)
        if seconds > 1e11:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _matches(row, where):
    return all(str(_get(row, key)) == str(expected) for key, expected in (where or {}).items())


def sqlite_target(path):
    """Split `state.sqlite3#posts` into (database path, table); None for other files."""
    if "#" in path.name and not path.is_file():
        database, _, table = str(path).rpartition("#")
        return Path(database), table
    if path.suffix.lower() in SQLITE_SUFFIXES:
        raise UsageError(
            f"{path.name}: name a table as {path.name}#table (run list-tables to see them)"
        )
    return None


def _sqlite_open(database):
    if not database.is_file():
        raise UsageError(f"no such file: {database}")
    try:
        connection = sqlite3.connect(f"file:{quote(str(database.resolve()))}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.Error:
        raise UsageError(f"{database.name}: cannot be read as a SQLite database") from None
    return connection


def _sqlite_tables(connection):
    return {
        name: kind
        for name, kind in connection.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    }


def _sqlite_name(name):
    return '"' + name.replace('"', '""') + '"'


def _sqlite_rows(database, table, where, columns):
    connection = _sqlite_open(database)
    try:
        if table not in _sqlite_tables(connection):
            raise UsageError(f"{database.name}: no table named {table}")
        names = [row[1] for row in connection.execute(f"PRAGMA table_info({_sqlite_name(table)})")]
        absent = sorted(set(columns) - set(names))
        if absent:
            raise UsageError(f"{database.name}#{table}: no column named {', '.join(absent)}")
        try:
            for values in connection.execute(f"SELECT * FROM {_sqlite_name(table)}"):
                row = {
                    name: f"<{len(value)} bytes>" if isinstance(value, bytes) else value
                    for name, value in zip(names, values)
                }
                if _matches(row, where):
                    yield row
        except sqlite3.Error:
            raise UsageError(f"{database.name}#{table}: could not be read") from None
    finally:
        connection.close()


def list_tables(database):
    """Table and column names with row counts. Metadata only, never values."""
    connection = _sqlite_open(database)
    try:
        tables = []
        for name, kind in _sqlite_tables(connection).items():
            quoted = _sqlite_name(name)
            columns = [
                {"column": row[1], "declared_type": row[2] or None}
                for row in connection.execute(f"PRAGMA table_info({quoted})")
            ]
            rows = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            tables.append({"table": name, "type": kind, "rows": rows, "columns": columns})
    except sqlite3.Error:
        raise UsageError(f"{database.name}: cannot be read as a SQLite database") from None
    finally:
        connection.close()
    return {"file": database.name, "tables": tables}


def read_rows(path, where=None, columns=()):
    """Yield dict rows. Errors name the file and line, never the content."""
    target = sqlite_target(path)
    if target:
        yield from _sqlite_rows(*target, where, columns)
        return
    suffix = path.suffix.lower()
    if not path.is_file():
        raise UsageError(f"no such file: {path}")
    if suffix in (".jsonl", ".ndjson"):
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    raise UsageError(f"{path.name}: line {number} is not valid JSON") from None
                if isinstance(row, dict) and _matches(row, where):
                    yield row
    elif suffix in (".csv", ".tsv"):
        csv.field_size_limit(2**31 - 1)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t" if suffix == ".tsv" else ",")
            header = set(reader.fieldnames or [])
            absent = sorted(set(columns) - header)
            if absent:
                raise UsageError(f"{path.name}: no column named {', '.join(absent)}")
            for row in reader:
                if _matches(row, where):
                    yield row
    else:
        raise UsageError(
            f"{path.name}: expected a .csv, .tsv, .jsonl or .ndjson file, or a SQLite table "
            "written as database#table"
        )


def _normaliser(mapping, canonical, default):
    lookup = {name: name for name in canonical}
    for name, raw_values in (mapping or {}).items():
        if name not in canonical:
            raise UsageError(f"unknown value {name!r}; expected one of {', '.join(canonical)}")
        for raw in raw_values:
            lookup[str(raw).strip().lower()] = name

    def normalise(value):
        if isinstance(value, bool):
            value = "true" if value else "false"
        if _empty(value):
            return default
        return lookup.get(str(value).strip().lower(), default)

    return normalise


def not_status_like(distinct, max_length, filled):
    """Why a column's values must not be listed, or None when it behaves like a status field."""
    if distinct > CATEGORY_MAX_VALUES:
        return f"more than {CATEGORY_MAX_VALUES} distinct values; cardinality only"
    if max_length > CATEGORY_MAX_LENGTH:
        return f"values longer than {CATEGORY_MAX_LENGTH} characters look like free text"
    if filled and distinct / filled > CATEGORY_MAX_DISTINCT_SHARE:
        return (
            "values rarely repeat (distinct values exceed half of the filled rows), "
            "which looks like free text or an identifier"
        )
    return None


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _columns(config):
    return [value for key, value in config.items() if key not in NON_COLUMN_KEYS and value]


def load_columns(text):
    path = Path(text)
    try:
        raw = path.read_text(encoding="utf-8") if path.is_file() else text
        columns = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        raise UsageError("--columns must be a JSON file path or an inline JSON object") from None
    if not isinstance(columns, dict):
        raise UsageError("--columns must be a JSON object")
    for table, config in columns.items():
        if table not in COLUMN_KEYS:
            raise UsageError(f"--columns: unknown table {table!r}")
        if not isinstance(config, dict):
            raise UsageError(f"--columns.{table} must be an object")
        unknown = sorted(set(config) - COLUMN_KEYS[table])
        if unknown:
            raise UsageError(f"--columns.{table}: unknown key {', '.join(unknown)}")
    return columns


def profile_files(decisions_path, actions_path, outcomes_path, columns, workflow, as_of=None):
    dc = columns.get("decisions") or {}
    if not dc.get("id"):
        raise UsageError("--columns.decisions.id is required")
    profile = empty_profile(workflow, as_of)
    objects = []
    for path in (decisions_path, actions_path, outcomes_path):
        if path and path.name not in objects:
            objects.append(path.name)
    paths = [path for path in (decisions_path, actions_path, outcomes_path) if path]
    all_sqlite = all("#" in path.name and not path.is_file() for path in paths)
    profile["sources"] = [
        {
            "type": "sqlite" if all_sqlite else "files",
            "location": str(decisions_path.parent),
            "objects": objects,
        }
    ]

    exposure_of = _normaliser(
        {
            "shown": ["true", "1", "yes", "y", "t"],
            "hidden": ["false", "0", "no", "n", "f"],
            **(dc.get("exposure_values") or {}),
        },
        ["shown", "hidden", "unknown"],
        "unknown",
    )

    # Decisions.
    rows = 0
    decisions = {}
    counts = Counter()
    exposure = Counter({"shown": 0, "hidden": 0, "unknown": 0})
    categories = Counter()
    category_overflow = False
    weeks = Counter()
    first = last = None
    for row in read_rows(decisions_path, dc.get("where"), _columns(dc)):
        rows += 1
        decision_id = _get(row, dc["id"])
        if _empty(decision_id):
            counts["no_id"] += 1
            continue
        decided = _time(_get(row, dc.get("decided_at")))
        suggestion = _fingerprint(_get(row, dc.get("suggestion")))
        decisions[str(decision_id)] = (
            decided,
            suggestion,
            _time(_get(row, dc.get("suggestion_updated_at"))),
        )
        if decided:
            counts["decided_at"] += 1
            first = decided if first is None or decided < first else first
            last = decided if last is None or decided > last else last
            weeks[(decided - timedelta(days=decided.weekday())).date()] += 1
        if suggestion:
            counts["suggestion"] += 1
        exposure[exposure_of(_get(row, dc.get("exposure")))] += 1
        for key in ("model", "prompt_version", "code_version", "context", "request_content"):
            if not _empty(_get(row, dc.get(key))):
                counts[key] += 1
        snapshot = _time(_get(row, dc.get("context_snapshot_at")))
        if snapshot and (decided is None or snapshot <= decided):
            counts["snapshot"] += 1
        category = _get(row, dc.get("category"))
        if not _empty(category):
            counts["category"] += 1
            value = str(category).strip()
            if value in categories or len(categories) < CATEGORY_TRACK_LIMIT:
                categories[value] += 1
            else:
                category_overflow = True

    d = profile["decisions"]
    distinct = len(decisions)
    d["rows"] = rows
    d["duplicate_id_rate"] = ratio(rows - counts["no_id"] - distinct, rows)

    def covered(key, column):
        return ratio(counts[key], rows) if dc.get(column) else None

    d["suggestion_coverage"] = covered("suggestion", "suggestion")
    d["exposure_counts"] = dict(exposure) if dc.get("exposure") else None
    d["decided_at_coverage"] = covered("decided_at", "decided_at")
    d["policy_model_coverage"] = covered("model", "model")
    d["policy_prompt_version_coverage"] = covered("prompt_version", "prompt_version")
    d["policy_code_version_coverage"] = covered("code_version", "code_version")
    d["context_coverage"] = covered("context", "context")
    d["context_snapshot_coverage"] = covered("snapshot", "context_snapshot_at")
    d["request_content_coverage"] = covered("request_content", "request_content")
    if dc.get("category"):
        d["category_field"] = dc["category"]
        d["category_coverage"] = ratio(counts["category"], rows)
        d["category_cardinality"] = len(categories) + (1 if category_overflow else 0)
        longest = max((len(value) for value in categories), default=0)
        refusal = not_status_like(d["category_cardinality"], longest, counts["category"])
        if refusal:
            d["category_values_suppressed"] = refusal
        else:
            d["category_values"] = [
                {"value": value, "count": count}
                for value, count in sorted(categories.items(), key=lambda item: (-item[1], item[0]))
            ]
    if weeks:
        d["first_decided_at"] = first.date().isoformat()
        d["last_decided_at"] = last.date().isoformat()
        week, end, series = min(weeks), max(weeks), []
        while week <= end:
            series.append(weeks.get(week, 0))
            week += timedelta(days=7)
        d["weeks_observed"] = len(series)
        complete = series[1:-1] if len(series) >= 4 else series
        d["weekly_volume_median"] = round(statistics.median(complete), 1)

    # Human actions.
    ac = columns.get("actions") or {}
    final = {}
    if actions_path:
        if not ac.get("decision_id"):
            raise UsageError("--columns.actions.decision_id is required with --actions")
        kind_of = _normaliser(ac.get("kind_values"), KINDS, "unknown")
        signal = [ac.get(key) for key in ("event_at", "actor", "kind", "output") if ac.get(key)]
        tally = Counter()
        actors = set()
        first_action = {}
        for sequence, row in enumerate(read_rows(actions_path, ac.get("where"), _columns(ac))):
            if signal and all(_empty(_get(row, column)) for column in signal):
                continue  # a wide export row with no human action on it
            tally["rows"] += 1
            decision_id = str(_get(row, ac["decision_id"]))
            event = _time(_get(row, ac.get("event_at")))
            actor = _get(row, ac.get("actor"))
            if event:
                tally["event_at"] += 1
            if _time(_get(row, ac.get("observed_at"))):
                tally["observed_at"] += 1
            if not _empty(actor):
                tally["actor"] += 1
                actors.add(str(actor).strip())
            if decision_id not in decisions:
                tally["orphan"] += 1
                continue
            if event and (decision_id not in first_action or event < first_action[decision_id]):
                first_action[decision_id] = event
            order = (event or datetime.min.replace(tzinfo=timezone.utc), sequence)
            if decision_id not in final or order > final[decision_id][0]:
                final[decision_id] = (
                    order,
                    event,
                    kind_of(_get(row, ac.get("kind"))) if ac.get("kind") else None,
                    _fingerprint(_get(row, ac.get("output"))),
                    None if _empty(actor) else str(actor).strip(),
                )

        a = profile["actions"]
        total = tally["rows"]
        a["rows"] = total
        a["decisions_with_action_rate"] = ratio(len(final), distinct)
        a["orphan_rate"] = ratio(tally["orphan"], total)
        a["event_at_coverage"] = ratio(tally["event_at"], total) if ac.get("event_at") else None
        a["observed_at_coverage"] = (
            ratio(tally["observed_at"], total) if ac.get("observed_at") else None
        )
        if ac.get("actor"):
            a["actor_coverage"] = ratio(tally["actor"], total)
            a["distinct_actors"] = len(actors)

        kinds = Counter()
        accepted = compared = equal = edited = edited_equal = modified = modifiable = 0
        by_actor = {}
        for decision_id, (_, event, kind, output, actor) in final.items():
            decided, suggestion, updated = decisions[decision_id]
            same = suggestion == output if suggestion and output else None
            if kind:
                kinds[kind] += 1
                unchanged = kind == "accepted"
            else:
                unchanged = same
            if same is not None:
                compared += 1
                equal += same
                if kind in ("edited", "overridden"):
                    edited += 1
                    edited_equal += same
            if unchanged is not None:
                tally["judged"] += 1
                accepted += unchanged
            started = first_action.get(decision_id)
            if updated and started:
                modifiable += 1
                modified += updated > started
            if actor:
                stats = by_actor.setdefault(actor, {"actions": 0, "judged": 0, "accepted": 0, "s": []})
                stats["actions"] += 1
                if unchanged is not None:
                    stats["judged"] += 1
                    stats["accepted"] += unchanged
                if event and decided:
                    stats["s"].append((event - decided).total_seconds())
        if ac.get("kind"):
            a["kind_counts"] = {kind: kinds.get(kind, 0) for kind in KINDS}
            a["accept_unchanged_basis"] = "kind"
        elif compared:
            a["accept_unchanged_basis"] = "text-equality"
        a["accept_unchanged_rate"] = ratio(accepted, tally["judged"])
        d["suggestion_equals_final_rate"] = ratio(equal, compared)
        d["suggestion_equals_final_rate_when_edited"] = ratio(edited_equal, edited)
        d["suggestion_modified_after_action_rate"] = ratio(modified, modifiable)
        ranked = sorted(by_actor.values(), key=lambda stats: -stats["actions"])[:REVIEWER_LIMIT]
        a["reviewers"] = [
            {
                "reviewer": f"reviewer_{index}",
                "actions": stats["actions"],
                "accept_unchanged_rate": ratio(stats["accepted"], stats["judged"]),
                "median_seconds_to_action": (
                    round(statistics.median(stats["s"]), 1) if stats["s"] else None
                ),
            }
            for index, stats in enumerate(ranked, 1)
        ]

    # Outcomes.
    oc = columns.get("outcomes") or {}
    if outcomes_path:
        if not oc.get("decision_id"):
            raise UsageError("--columns.outcomes.decision_id is required with --outcomes")
        tally = Counter()
        earliest = {}
        joined = set()
        signal = [oc.get(key) for key in ("event_at", "observed_at") if oc.get(key)]
        for row in read_rows(outcomes_path, oc.get("where"), _columns(oc)):
            if signal and all(_empty(_get(row, column)) for column in signal):
                continue
            tally["rows"] += 1
            decision_id = str(_get(row, oc["decision_id"]))
            event = _time(_get(row, oc.get("event_at")))
            observed = _time(_get(row, oc.get("observed_at")))
            tally["event_at"] += bool(event)
            tally["observed_at"] += bool(observed)
            if decision_id not in decisions:
                tally["orphan"] += 1
                continue
            joined.add(decision_id)
            seen = observed or event
            if seen and (decision_id not in earliest or seen < earliest[decision_id]):
                earliest[decision_id] = seen
        o = profile["outcomes"]
        total = tally["rows"]
        o["rows"] = total
        o["decisions_with_outcome_rate"] = ratio(len(joined), distinct)
        o["orphan_rate"] = ratio(tally["orphan"], total)
        o["event_at_coverage"] = ratio(tally["event_at"], total) if oc.get("event_at") else None
        o["observed_at_coverage"] = (
            ratio(tally["observed_at"], total) if oc.get("observed_at") else None
        )
        lags = [
            (seen - decisions[decision_id][0]).total_seconds() / 86400
            for decision_id, seen in earliest.items()
            if decisions[decision_id][0]
        ]
        if lags:
            o["lag_days_p50"] = round(_percentile(lags, 0.5), 2)
            o["lag_days_p90"] = round(_percentile(lags, 0.9), 2)
    return profile


# ---------------------------------------------------------------------------
# list-columns


def _flatten(row, prefix="", depth=0):
    for key, value in row.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and value and depth < 2:
            yield from _flatten(value, f"{name}.", depth + 1)
        else:
            yield name, value


def _shape(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (dict, list)):
        return "object"
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return "boolean"
    if is_number(value):
        return "number"
    try:
        float(value)
        return "number"
    except (TypeError, ValueError):
        pass
    return "timestamp" if _time(value) else "text"


def list_columns(path, value_columns=()):
    """Describe each column by shape only. Values are listed solely for --values columns."""
    stats = {}
    rows = 0
    for row in read_rows(path):
        rows += 1
        for name, value in _flatten(row):
            entry = stats.setdefault(
                name, {"filled": 0, "seen": set(), "max_length": 0, "shapes": Counter(), "values": Counter()}
            )
            if _empty(value):
                continue
            entry["filled"] += 1
            entry["shapes"][_shape(value)] += 1
            text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
            entry["max_length"] = max(entry["max_length"], len(text))
            if len(entry["seen"]) <= 1000:
                entry["seen"].add(hashlib.sha256(text.encode("utf-8")).digest())
            if name in value_columns and len(entry["values"]) <= CATEGORY_MAX_VALUES:
                entry["values"][text.strip()] += 1
    absent = sorted(set(value_columns) - set(stats))
    if absent:
        raise UsageError(f"{path.name}: no column named {', '.join(absent)}")
    columns = []
    for name, entry in stats.items():
        distinct = len(entry["seen"])
        column = {
            "column": name,
            "fill_rate": ratio(entry["filled"], rows),
            "distinct": "over 1000" if distinct > 1000 else distinct,
            "max_length": entry["max_length"],
            "looks_like": entry["shapes"].most_common(1)[0][0] if entry["shapes"] else "empty",
        }
        if name in value_columns:
            refusal = not_status_like(distinct, entry["max_length"], entry["filled"])
            if refusal:
                column["values_refused"] = refusal
            else:
                column["values"] = [
                    {"value": value, "count": count}
                    for value, count in entry["values"].most_common()
                ]
        columns.append(column)
    return {"file": path.name, "rows": rows, "columns": columns}


# ---------------------------------------------------------------------------
# sample: the only command that shows row content, and only after consent


def sample_rows(path, columns, rows=SAMPLE_ROWS_PER_LOOK):
    """Up to `rows` rows spread across the file, named columns only, values truncated."""
    if not columns:
        raise UsageError("sample needs at least one --column")
    if len(columns) > SAMPLE_COLUMNS_PER_LOOK:
        raise UsageError(f"sample takes at most {SAMPLE_COLUMNS_PER_LOOK} columns at a time")
    if not 1 <= rows <= SAMPLE_ROWS_PER_LOOK:
        raise UsageError(f"--rows must be between 1 and {SAMPLE_ROWS_PER_LOOK}")

    def filled(row):
        return any(not _empty(_get(row, column)) for column in columns)

    names = {name for row in read_rows(path) for name, _ in _flatten(row)}
    absent = sorted(set(columns) - names)
    if absent:
        raise UsageError(f"{path.name}: no column named {', '.join(absent)}")
    total = sum(1 for row in read_rows(path) if filled(row))
    take = min(rows, total)
    wanted = {(index * total) // take for index in range(take)} if take else set()
    shown = []
    position = 0
    for row in read_rows(path):
        if not filled(row):
            continue
        if position in wanted:
            entry = {}
            for column in columns:
                value = _get(row, column)
                if _empty(value):
                    entry[column] = None
                    continue
                text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
                if len(text) > SAMPLE_MAX_CHARS:
                    text = text[:SAMPLE_MAX_CHARS] + " [truncated]"
                entry[column] = text
            shown.append(entry)
        position += 1
    return {
        "file": path.name,
        "columns": list(columns),
        "rows_shown": len(shown),
        "rows": shown,
        "reminder": (
            "Add rows_shown to content_access.rows_viewed in the profile. "
            "Do not copy these values into any file or answer."
        ),
    }


# ---------------------------------------------------------------------------
# CLI


def _write(text, out):
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError:
        raise UsageError(f"cannot read {path}") from None
    except json.JSONDecodeError as exc:
        raise UsageError(f"{path} is not valid JSON: {exc.msg} at line {exc.lineno}") from None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="readiness.py", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    template = commands.add_parser("template", help="print an empty profile")
    template.add_argument("--workflow", default="workflow-name")
    template.add_argument("--as-of")
    template.add_argument("--out")

    tables = commands.add_parser("list-tables", help="tables and column names of a SQLite file")
    tables.add_argument("database", type=Path)

    listing = commands.add_parser("list-columns", help="column shapes, never values")
    listing.add_argument("files", nargs="+", type=Path)
    listing.add_argument(
        "--values",
        action="append",
        default=[],
        metavar="COLUMN",
        help="also list the values of a status column the user named (category, kind, exposure)",
    )

    files = commands.add_parser("profile-files", help="aggregate profile from exports or SQLite tables")
    files.add_argument("--decisions", required=True, type=Path)
    files.add_argument("--actions", type=Path)
    files.add_argument("--outcomes", type=Path)
    files.add_argument("--columns", required=True, help="JSON file or inline JSON column map")
    files.add_argument("--workflow", required=True)
    files.add_argument("--as-of")
    files.add_argument("--out")

    build = commands.add_parser("build", help="write readiness.json and readiness.md")
    build.add_argument("--profile", required=True)
    build.add_argument("--out", required=True, help="output directory")
    build.add_argument("--blind-sample-rate", type=float, default=0.10)

    check = commands.add_parser("check", help="validate a readiness.json")
    check.add_argument("readiness")

    sample = commands.add_parser(
        "sample", help="show a few truncated values of named columns, after consent"
    )
    sample.add_argument("file", type=Path)
    sample.add_argument("--column", action="append", default=[], metavar="COLUMN")
    sample.add_argument("--rows", type=int, default=SAMPLE_ROWS_PER_LOOK)
    sample.add_argument(
        "--consent-given",
        action="store_true",
        help="the user has approved this agent reading row content for this workflow",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            profile = empty_profile(args.workflow, args.as_of)
            _write(json.dumps(profile, indent=2) + "\n", args.out)
        elif args.command == "list-tables":
            _write(json.dumps(list_tables(args.database), indent=2) + "\n", None)
        elif args.command == "list-columns":
            found = set()
            described = []
            for path in args.files:
                names = {name for row in read_rows(path) for name, _ in _flatten(row)}
                wanted = [name for name in args.values if name in names]
                found.update(wanted)
                described.append(list_columns(path, wanted))
            absent = sorted(set(args.values) - found)
            if absent:
                raise UsageError(f"no file has a column named {', '.join(absent)}")
            _write(json.dumps(described, indent=2) + "\n", None)
        elif args.command == "profile-files":
            profile = profile_files(
                args.decisions,
                args.actions,
                args.outcomes,
                load_columns(args.columns),
                args.workflow,
                args.as_of,
            )
            problems = validate_profile(profile)
            if problems:
                raise UsageError("invalid profile:\n  " + "\n  ".join(problems))
            _write(json.dumps(profile, indent=2) + "\n", args.out)
        elif args.command == "build":
            readiness = build_readiness(_load(args.profile), args.blind_sample_rate)
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            (out / "readiness.json").write_text(
                json.dumps(readiness, indent=2) + "\n", encoding="utf-8"
            )
            (out / "readiness.md").write_text(render_markdown(readiness), encoding="utf-8")
            print(f"verdict: {readiness['verdict']}")
            for check_result in readiness["checks"]:
                print(
                    f"  {check_result['id']}: {check_result['status']} "
                    f"({check_result['answered_by']})"
                )
            print(f"wrote {out / 'readiness.json'} and {out / 'readiness.md'}")
        elif args.command == "sample":
            if not args.consent_given:
                raise UsageError(
                    "sample shows row content. Ask the user the consent question in "
                    "references/content-samples.md first; on yes, pass --consent-given."
                )
            shown = sample_rows(args.file, args.column, args.rows)
            _write(json.dumps(shown, indent=2) + "\n", None)
        elif args.command == "check":
            problems = check_readiness(_load(args.readiness))
            if problems:
                for problem in problems:
                    print(f"readiness check: ERROR: {problem}", file=sys.stderr)
                return 1
            print("readiness check: OK")
    except UsageError as exc:
        print(f"readiness.py: ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
