import copy
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "workflow-discover"
SCRIPT = SKILL / "scripts" / "readiness.py"
FIXTURES = ROOT / "tests" / "fixtures" / "workflow_discover"
SCHEMA = json.loads((SKILL / "assets" / "readiness.schema.json").read_text())

_spec = importlib.util.spec_from_file_location("workflow_discover_readiness", SCRIPT)
readiness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(readiness)

# Strings that exist only in free-text columns and reviewer IDs of the raw fixtures.
FORBIDDEN = ["SENTINEL", "Jane", "Roe", "1970", "r-alice", "r-bob", "rewritten", "replaced"]


def run(*args, script=SCRIPT, cwd=None):
    return subprocess.run(
        [sys.executable, str(script), *map(str, args)], capture_output=True, text=True, cwd=cwd
    )


def healthy_profile():
    """A workflow whose history is fully minable."""
    profile = readiness.empty_profile("prior-auth-review", "2026-11-22")
    profile["sources"] = [
        {"type": "warehouse", "location": "acme.analytics", "objects": ["ai_drafts", "messages"]}
    ]
    profile["decisions"].update(
        rows=48000,
        duplicate_id_rate=0.0,
        suggestion_coverage=0.99,
        suggestion_equals_final_rate=0.81,
        suggestion_equals_final_rate_when_edited=0.0,
        suggestion_modified_after_action_rate=0.0,
        exposure_counts={"shown": 47000, "hidden": 1000, "unknown": 0},
        decided_at_coverage=1.0,
        policy_model_coverage=1.0,
        policy_prompt_version_coverage=1.0,
        policy_code_version_coverage=1.0,
        context_coverage=0.99,
        context_snapshot_coverage=0.99,
        request_content_coverage=1.0,
        category_field="queue",
        category_coverage=1.0,
        category_cardinality=2,
        category_values=[{"value": "refill", "count": 36000}, {"value": "billing", "count": 12000}],
        first_decided_at="2026-05-25",
        last_decided_at="2026-11-15",
        weeks_observed=25,
        weekly_volume_median=2000,
    )
    profile["actions"].update(
        rows=47000,
        decisions_with_action_rate=0.97,
        orphan_rate=0.0,
        event_at_coverage=1.0,
        observed_at_coverage=1.0,
        actor_coverage=1.0,
        distinct_actors=40,
        kind_counts={"accepted": 38000, "edited": 7000, "overridden": 2000},
        accept_unchanged_rate=0.81,
        accept_unchanged_basis="kind",
        reviewers=[
            {
                "reviewer": "reviewer_1",
                "actions": 4000,
                "accept_unchanged_rate": 0.99,
                "median_seconds_to_action": 4.0,
            }
        ],
    )
    profile["outcomes"].update(
        rows=40000,
        decisions_with_outcome_rate=0.85,
        orphan_rate=0.0,
        event_at_coverage=1.0,
        observed_at_coverage=1.0,
        lag_days_p50=3.0,
        lag_days_p90=14.0,
    )
    return profile


def overwritten(profile):
    profile["decisions"]["suggestion_equals_final_rate_when_edited"] = 0.97
    profile["decisions"]["suggestion_modified_after_action_rate"] = 0.19


def exposure_missing(profile):
    profile["decisions"]["exposure_counts"] = {"shown": 0, "hidden": 0, "unknown": 48000}


def outcomes_late_and_sparse(profile):
    profile["outcomes"]["decisions_with_outcome_rate"] = 0.31
    profile["outcomes"]["lag_days_p90"] = 140.0


def statuses(result):
    return {check["id"]: check["status"] for check in result["checks"]}


def test_healthy_history_is_minable():
    result = readiness.build_readiness(healthy_profile())
    assert result["verdict"] == "mine-history"
    assert set(statuses(result).values()) == {"present"}
    assert {value["status"] for value in result["record_types"].values()} == {"minable"}
    assert all(check["answered_by"] == "probe" for check in result["checks"])


@pytest.mark.parametrize(
    "defect, verdict, record_type, check, status",
    [
        (overwritten, "log-forward-only", "decision", "suggestion_not_overwritten", "missing"),
        (exposure_missing, "partial", "human_action", "exposure_known", "missing"),
        (outcomes_late_and_sparse, "mine-history", "outcome", "outcome_join", "partial"),
    ],
)
def test_planted_defects_decide_the_verdict(defect, verdict, record_type, check, status):
    profile = healthy_profile()
    defect(profile)
    result = readiness.build_readiness(profile)
    assert result["verdict"] == verdict
    assert statuses(result)[check] == status
    assert result["record_types"][record_type]["status"] != "minable"
    jsonschema.validate(result, SCHEMA)


def test_equality_alone_is_ambiguous_until_answered():
    profile = healthy_profile()
    profile["decisions"]["suggestion_equals_final_rate_when_edited"] = None
    profile["decisions"]["suggestion_modified_after_action_rate"] = None
    result = readiness.build_readiness(profile)
    check = result["checks"][1]
    assert (check["status"], check["answered_by"]) == ("partial", "unresolved")
    assert "81%" in check["evidence"]
    assert result["verdict"] == "partial"

    answer = {
        "check": "suggestion_not_overwritten",
        "question": "Is ai_drafts the suggestion as shown, a later regeneration, or unknown?",
        "answer": "(a) the suggestion as shown",
        "status": "present",
    }
    profile["answers"] = [answer]
    answered = readiness.build_readiness(profile)
    assert answered["checks"][1]["answered_by"] == "user"
    assert answered["verdict"] == "mine-history"

    profile["answers"] = [{**answer, "answer": "(b) a later regeneration", "status": "missing"}]
    assert readiness.build_readiness(profile)["verdict"] == "log-forward-only"


def test_code_evidence_settles_a_check_and_is_attributed_to_code():
    profile = healthy_profile()
    exposure_missing(profile)
    profile["sources"].insert(
        0, {"type": "repository", "location": "acme/review-agent", "objects": ["app/review.py"]}
    )
    profile["answers"] = [
        {
            "check": "exposure_known",
            "question": "Is the draft always rendered before the reviewer can act?",
            "answer": "app/review.py renders the draft unconditionally; unchanged in git since 2026-01.",
            "status": "present",
            "source": "code",
        }
    ]
    result = readiness.build_readiness(profile)
    check = result["checks"][2]
    assert (check["status"], check["answered_by"]) == ("present", "code")
    assert "From the code:" in check["evidence"]
    assert result["verdict"] == "mine-history"
    jsonschema.validate(result, SCHEMA)
    profile["answers"][0]["source"] = "guess"
    with pytest.raises(readiness.UsageError, match="source must be one of"):
        readiness.build_readiness(profile)


def test_unmeasured_profile_is_unresolved_not_usable():
    profile = readiness.empty_profile("crm-replies", "2026-11-22")
    profile["sources"] = [
        {
            "type": "unsupported",
            "location": "trace platform",
            "objects": [],
            "note": "Needs an export of traces to JSONL.",
        }
    ]
    result = readiness.build_readiness(profile)
    assert result["verdict"] == "log-forward-only"
    assert {check["answered_by"] for check in result["checks"]} == {"unresolved"}
    assert "No stored history was measured" in readiness.render_markdown(result)


def test_report_never_calls_accept_history_correctness():
    result = readiness.build_readiness(healthy_profile())
    markdown = readiness.render_markdown(result)
    assert "Not evidence of correctness" in markdown
    assert "reviewer_1" in markdown
    assert "Not a sample-size advisor" in markdown
    assert any("reviewer_1" in flag for flag in result["volume"]["flags"])
    assert not re.search(r"\baccura", markdown, re.I)


def test_label_table_matches_the_zero_error_bound():
    for rate, labels in readiness.LABEL_TABLE:
        assert labels == math.ceil(math.log(0.05) / math.log(1 - rate))
    estimate = readiness.build_readiness(healthy_profile(), 0.10)["label_estimate"]
    assert [row["labels_needed"] for row in estimate["rows"]] == [59, 149, 299, 598]
    # 2000 per week at a 10% blind sample is 200 labels per week.
    assert [row["weeks"] for row in estimate["rows"]] == [1, 1, 2, 3]
    billing = next(item for item in estimate["per_class"] if item["value"] == "billing")
    assert billing["weekly_volume"] == 500
    assert billing["weeks"] == [2, 3, 6, 12]


def test_profile_rejects_typos_and_real_reviewer_ids():
    profile = healthy_profile()
    profile["decisions"]["sugestion_coverage"] = 0.9
    profile["actions"]["reviewers"][0]["reviewer"] = "jane.roe@example.com"
    profile["outcomes"]["lag_days_p50"] = "3"
    with pytest.raises(readiness.UsageError) as error:
        readiness.build_readiness(profile)
    message = str(error.value)
    assert "decisions: unknown key: sugestion_coverage" in message
    assert "never a real ID" in message
    assert "outcomes.lag_days_p50 must be a number" in message


@pytest.fixture(scope="module")
def fixture_profile(tmp_path_factory):
    out = tmp_path_factory.mktemp("discover") / "profile.json"
    result = run(
        "profile-files",
        "--decisions", FIXTURES / "decisions.csv",
        "--actions", FIXTURES / "actions.jsonl",
        "--outcomes", FIXTURES / "outcomes.jsonl",
        "--columns", FIXTURES / "columns.json",
        "--workflow", "fixture-workflow",
        "--as-of", "2026-11-22",
        "--out", out,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    return out, result


def test_profile_files_reproduces_expected_aggregates(fixture_profile):
    profile = json.loads(fixture_profile[0].read_text())
    decisions, actions, outcomes = profile["decisions"], profile["actions"], profile["outcomes"]
    assert decisions == {
        "rows": 10,
        "duplicate_id_rate": 0.0,
        "suggestion_coverage": 0.9,
        "suggestion_equals_final_rate": 0.75,
        "suggestion_equals_final_rate_when_edited": 0.3333,
        "suggestion_modified_after_action_rate": 0.25,
        "exposure_counts": {"shown": 6, "hidden": 1, "unknown": 3},
        "decided_at_coverage": 1.0,
        "policy_model_coverage": 1.0,
        "policy_prompt_version_coverage": 0.8,
        "policy_code_version_coverage": 0.0,
        "context_coverage": 1.0,
        "context_snapshot_coverage": 0.5,
        "request_content_coverage": 1.0,
        "category_field": "queue",
        "category_coverage": 0.9,
        "category_cardinality": 3,
        "category_values": [
            {"value": "refill", "count": 5},
            {"value": "billing", "count": 3},
            {"value": "scheduling", "count": 1},
        ],
        "category_values_suppressed": None,
        "first_decided_at": "2026-09-08",
        "last_decided_at": "2026-10-06",
        "weeks_observed": 5,
        "weekly_volume_median": 3,
    }
    assert actions == {
        "rows": 11,
        "decisions_with_action_rate": 0.9,
        "orphan_rate": 0.0909,
        "event_at_coverage": 1.0,
        "observed_at_coverage": 0.8182,
        "actor_coverage": 0.9091,
        "distinct_actors": 2,
        "kind_counts": {
            "accepted": 5,
            "edited": 2,
            "overridden": 1,
            "independent": 0,
            "rejected": 0,
            "escalated": 0,
            "none": 0,
            "unknown": 1,
        },
        "accept_unchanged_rate": 0.5556,
        "accept_unchanged_basis": "kind",
        "reviewers": [
            {
                "reviewer": "reviewer_1",
                "actions": 5,
                "accept_unchanged_rate": 0.6,
                "median_seconds_to_action": 600.0,
            },
            {
                "reviewer": "reviewer_2",
                "actions": 3,
                "accept_unchanged_rate": 0.6667,
                "median_seconds_to_action": 600.0,
            },
        ],
    }
    assert outcomes == {
        "rows": 6,
        "decisions_with_outcome_rate": 0.4,
        "orphan_rate": 0.1667,
        "event_at_coverage": 0.8333,
        "observed_at_coverage": 1.0,
        "lag_days_p50": 2.0,
        "lag_days_p90": 10.0,
    }


def test_no_free_text_or_reviewer_id_reaches_any_output(fixture_profile, tmp_path):
    profile_path, profiled = fixture_profile
    built = run("build", "--profile", profile_path, "--out", tmp_path)
    assert built.returncode == 0, built.stderr
    listed = run(
        "list-columns", FIXTURES / "decisions.csv", FIXTURES / "actions.jsonl",
        "--values", "queue", "--values", "action", "--values", "message_body",
    )  # fmt: skip
    assert listed.returncode == 0, listed.stderr
    outputs = [
        profile_path.read_text(),
        (tmp_path / "readiness.json").read_text(),
        (tmp_path / "readiness.md").read_text(),
        profiled.stdout + profiled.stderr,
        built.stdout + built.stderr,
        listed.stdout + listed.stderr,
    ]
    raw = (FIXTURES / "decisions.csv").read_text() + (FIXTURES / "actions.jsonl").read_text()
    for word in FORBIDDEN:
        assert word in raw  # the fixtures really do contain it
        for output in outputs:
            assert word not in output
    columns = {
        column["column"]: column
        for described in json.loads(listed.stdout)
        for column in described["columns"]
    }
    assert [item["value"] for item in columns["queue"]["values"]] == [
        "refill",
        "billing",
        "scheduling",
    ]
    assert "values" not in columns["message_body"]
    assert "rarely repeat" in columns["message_body"]["values_refused"]
    assert "values" not in columns["ai_draft"]


def test_free_text_mapped_as_category_is_suppressed(tmp_path):
    columns = json.loads((FIXTURES / "columns.json").read_text())
    columns["decisions"]["category"] = "message_body"
    profile = readiness.profile_files(
        FIXTURES / "decisions.csv", None, None, columns, "fixture-workflow", "2026-11-22"
    )
    assert profile["decisions"]["category_values"] is None
    assert "rarely repeat" in profile["decisions"]["category_values_suppressed"]
    assert profile["decisions"]["category_cardinality"] == 9
    assert "SENTINEL" not in json.dumps(profile)


def test_wide_file_and_text_equality_basis(tmp_path):
    wide = tmp_path / "wide.csv"
    wide.write_text(
        "id,created,draft,final,acted_at\n"
        "a,2026-09-07 10:00:00,hello  there,hello there,2026-09-07 10:05:00\n"
        "b,2026-09-07 11:00:00,one,two,2026-09-07 11:05:00\n"
        "c,2026-09-08 11:00:00,pending,,\n"
    )
    columns = {
        "decisions": {"id": "id", "decided_at": "created", "suggestion": "draft"},
        "actions": {"decision_id": "id", "event_at": "acted_at", "output": "final"},
    }
    profile = readiness.profile_files(wide, wide, None, columns, "wide", "2026-11-22")
    assert profile["actions"]["rows"] == 2
    assert profile["actions"]["accept_unchanged_basis"] == "text-equality"
    assert profile["actions"]["accept_unchanged_rate"] == 0.5
    assert profile["decisions"]["suggestion_equals_final_rate"] == 0.5
    assert profile["outcomes"]["rows"] is None
    result = readiness.build_readiness(profile)
    assert result["checks"][1]["answered_by"] == "unresolved"


def test_profile_files_reports_bad_input_without_echoing_it(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id": "a"}\nPHI-SENTINEL not json\n')
    result = run(
        "profile-files", "--decisions", bad, "--columns", '{"decisions": {"id": "id"}}',
        "--workflow", "bad",
    )  # fmt: skip
    assert result.returncode == 2
    assert "line 2 is not valid JSON" in result.stderr
    assert "SENTINEL" not in result.stderr

    missing = run(
        "profile-files", "--decisions", FIXTURES / "decisions.csv",
        "--columns", '{"decisions": {"id": "decision_uuid"}}', "--workflow", "bad",
    )  # fmt: skip
    assert missing.returncode == 2
    assert "no column named decision_uuid" in missing.stderr


def test_check_accepts_built_output_and_rejects_damage(fixture_profile, tmp_path):
    assert run("build", "--profile", fixture_profile[0], "--out", tmp_path).returncode == 0
    path = tmp_path / "readiness.json"
    accepted = run("check", path)
    assert accepted.returncode == 0 and "OK" in accepted.stdout
    good = json.loads(path.read_text())
    jsonschema.validate(good, SCHEMA)

    def damaged(change):
        document = copy.deepcopy(good)
        change(document)
        path.write_text(json.dumps(document))
        return run("check", path), document

    def unknown_key(document):
        document["confidence"] = "high"

    def bad_status(document):
        document["checks"][0]["status"] = "ok"

    def real_reviewer(document):
        document["volume"]["reviewers"][0]["reviewer"] = "r-alice"

    def dropped_check(document):
        document["checks"].pop()

    for change in (unknown_key, bad_status, real_reviewer, dropped_check):
        result, document = damaged(change)
        assert result.returncode == 1, change.__name__
        assert "ERROR" in result.stderr
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(document, SCHEMA)

    def wrong_verdict(document):
        document["verdict"] = "mine-history"

    result, document = damaged(wrong_verdict)
    assert result.returncode == 1
    assert "does not follow from record_types" in result.stderr


def test_script_runs_as_an_installed_skill(tmp_path):
    installed = tmp_path / "installed" / "workflow-discover"
    shutil.copytree(SKILL, installed)
    script = installed / "scripts" / "readiness.py"
    work = tmp_path / "work"
    work.mkdir()
    template = run("template", "--workflow", "copied", "--out", "profile.json", script=script, cwd=work)
    assert template.returncode == 0, template.stderr
    profile = json.loads((work / "profile.json").read_text())
    profile["sources"] = [{"type": "warehouse", "location": "acme", "objects": ["t"]}]
    (work / "profile.json").write_text(json.dumps(profile))
    built = run(
        "build", "--profile", "profile.json", "--out", "hindsight-discovery/copied",
        script=script, cwd=work,
    )  # fmt: skip
    assert built.returncode == 0, built.stderr
    out = work / "hindsight-discovery" / "copied"
    assert (out / "readiness.md").is_file()
    assert run("check", out / "readiness.json", script=script, cwd=work).returncode == 0


def test_script_uses_only_the_standard_library():
    imports = re.findall(r"^(?:import|from) (\w+)", SCRIPT.read_text(), re.M)
    assert set(imports) <= sys.stdlib_module_names


FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|CALL|COPY|PUT|"
    r"EXECUTE|EXPORT|UNLOAD|REPLACE\s+TABLE|INTO)\b",
    re.I,
)
FREE_TEXT = ["suggestion", "a_output", "request_content", "context"]


def test_warehouse_probes_are_single_read_only_selects():
    text = (SKILL / "references" / "source-warehouse.md").read_text()
    blocks = re.findall(r"```sql\n(.*?)```", text, re.S)
    assert len(blocks) >= 15
    for block in blocks:
        statement = block.strip()
        assert re.match(r"(SELECT|WITH)\b", statement), statement[:60]
        assert ";" not in statement, statement[:60]
        assert not FORBIDDEN_SQL.search(statement), statement[:60]
        assert not re.search(r"SELECT\s+\*", statement, re.I), statement[:60]
        # A free-text column may be counted, compared or trimmed into a CTE, never returned.
        final_select = statement[statement.rfind("\nSELECT") + 1 :]
        for column in FREE_TEXT:
            for line in final_select.splitlines():
                if "{{" + column + "}}" in line:
                    assert re.search(r"COUNT|COUNTIF|COUNT_IF", line), line


def test_skill_lint_and_manifests_agree():
    lint = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "lint_skills.py")], capture_output=True, text=True
    )
    assert lint.returncode == 0, lint.stderr
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    listed = marketplace["plugins"]
    assert len(listed) == 1
    assert (listed[0]["name"], listed[0]["version"]) == (plugin["name"], plugin["version"])
    assert plugin["name"] == "nodal-hindsight"
    assert re.fullmatch(r"\d+\.\d+\.\d+", plugin["version"])
    assert (ROOT / plugin["skills"] / "workflow-discover" / "SKILL.md").is_file()
    link = ROOT / ".claude" / "skills" / "workflow-discover"
    assert link.resolve() == SKILL.resolve()


def test_references_named_in_the_skill_exist_and_cover_all_checks():
    checks = (SKILL / "references" / "checks.md").read_text()
    for check_id in readiness.CHECK_IDS:
        assert f"`{check_id}`" in checks
    formats = (SKILL / "references" / "readiness-format.md").read_text()
    for fields in readiness.PROFILE_FIELDS.values():
        for name in fields:
            assert f"`{name}`" in formats, name
    assert {check["id"] for check in readiness.build_readiness(healthy_profile())["checks"]} == set(
        SCHEMA["$defs"]["check"]["properties"]["id"]["enum"]
    )
