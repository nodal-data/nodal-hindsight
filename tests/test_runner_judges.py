import json

import pytest

from nodal_hindsight.common import HindsightError, digest, read_jsonl
from nodal_hindsight.datasets import load_dataset
from nodal_hindsight.judges import Judge, anthropic_judge, calibrate, judge_request
from nodal_hindsight.runners import run_candidate
from nodal_hindsight.scoring import score_case


def config(tmp_path):
    return {
        "model": "fixture-v1",
        "prompt_version": "p1",
        "code_version": "c1",
        "cache": True,
        "cache_dir": str(tmp_path / "cache"),
        "max_calls": 10,
        "timeout_seconds": 5,
    }


def test_runner_cache_resume_and_input_boundary(fixture_data, tmp_path):
    *_, build = fixture_data
    dataset = build()
    cfg = config(tmp_path)
    first = run_candidate(dataset, "dev", "tests.candidates:assert_inputs", cfg, tmp_path / "first")
    assert first["calls"] == 2 and first["coverage"] == 1
    second = run_candidate(
        dataset, "dev", "tests.candidates:assert_inputs", cfg, tmp_path / "second"
    )
    assert second["calls"] == 0
    assert all(x["cache_hit"] for x in read_jsonl(tmp_path / "second/predictions.jsonl"))
    resumed = run_candidate(
        dataset, "dev", "tests.candidates:assert_inputs", cfg, tmp_path / "first", resume=True
    )
    assert resumed["calls"] == 2
    cfg["code_version"] = "changed"
    third = run_candidate(dataset, "dev", "tests.candidates:assert_inputs", cfg, tmp_path / "third")
    assert third["calls"] == 2
    with pytest.raises(HindsightError, match="resume identity"):
        run_candidate(
            dataset, "dev", "tests.candidates:assert_inputs", cfg, tmp_path / "first", resume=True
        )


@pytest.mark.parametrize(
    "fn,status", [("timeout", "timeout"), ("invalid", "invalid_output"), ("error", "error")]
)
def test_runner_failures(fixture_data, tmp_path, fn, status):
    *_, build = fixture_data
    cfg = config(tmp_path)
    cfg.update(timeout_seconds=0.5 if fn == "timeout" else 5, cache=False)
    run_candidate(build(), "dev", f"tests.candidates:{fn}", cfg, tmp_path / "run")
    ps = read_jsonl(tmp_path / "run/predictions.jsonl")
    assert all(p["status"] == status for p in ps)
    assert "SENSITIVE CUSTOMER" not in (tmp_path / "run/errors.jsonl").read_text()


def test_runner_budget(fixture_data, tmp_path):
    *_, build = fixture_data
    cfg = config(tmp_path)
    cfg["max_calls"] = 1
    run_candidate(build(), "dev", "tests.candidates:assert_inputs", cfg, tmp_path / "run")
    p = read_jsonl(tmp_path / "run/predictions.jsonl")
    assert p[0]["status"] == "success" and p[1]["status"] == "budget_exhausted"


def test_judge_malformed_budget_cache_and_identity(tmp_path):
    rubric = {
        "id": "r",
        "version": "1",
        "criterion": "Grounded?",
        "field": "message",
        "permitted_input_fields": [],
    }
    req = {"candidate": {"message": "hello"}}
    judge = Judge(
        {
            "provider": "fixture",
            "default_response": {"passed": "yes", "reason": "bad"},
            "max_calls": 1,
        }
    )
    with pytest.raises(HindsightError):
        judge(req, rubric)
    with pytest.raises(HindsightError, match="budget"):
        judge(req, rubric)
    cfg = {
        "provider": "fixture",
        "model": "fixture",
        "default_response": {"passed": True, "reason": "fixture"},
        "cache_dir": str(tmp_path / "cache"),
    }
    a = Judge(cfg)
    a(req, rubric)
    assert a.calls == 1
    b = Judge(cfg)
    b(req, rubric)
    assert b.calls == 0
    c = Judge({**cfg, "model": "changed"})
    c(req, rubric)
    assert c.calls == 1


def test_experimental_judge_not_gate_eligible(fixture_data):
    *_, build = fixture_data
    m, cases = load_dataset(build(), "train")
    c = next(c for c in cases if c["decision_id"].endswith("002"))
    judge = Judge(
        {
            "provider": "fixture",
            "default_response": {"passed": False, "reason": "Synthetic failed criterion"},
        }
    )
    v = score_case(c, c["references"], m["tasks"][c["task"]], judge)
    rubric = next(x for x in v["metrics"] if x["metric"].startswith("rubric:"))
    assert rubric["value"] == 0 and not rubric["eligible"] and rubric["details"]["experimental"]


def calibration_rows(rubric):
    result = []
    for i, passed in enumerate([True, False]):
        out = {"message": str(i)}
        result.append(
            {
                "case_id": str(i),
                "group_id": str(i),
                "split": "calibration",
                "input": {},
                "references": {},
                "output": out,
                "output_identity": digest(out),
                "rubric_id": rubric["id"],
                "reviewer": "human",
                "reviewed_at": "2026-08-01T00:00:00Z",
                "human_passed": passed,
            }
        )
    return result


def test_calibration_counts_approval_and_invalidation(tmp_path):
    rubric = {
        "id": "r",
        "version": "1",
        "criterion": "Check",
        "field": "message",
        "permitted_input_fields": [],
    }
    rows = calibration_rows(rubric)
    cfg = {
        "provider": "fixture",
        "model": "fixture",
        "responses": {
            digest(r["output"]): {"passed": r["human_passed"], "reason": "Fixture label"}
            for r in rows
        },
        "calibration_policy": {
            "min_positive": 1,
            "min_negative": 1,
            "min_tpr": 0.8,
            "min_tnr": 0.8,
            "uncertainty": "point",
        },
    }
    report = calibrate(rubric, rows, cfg, tmp_path / "cal", approved_by="reviewer")
    assert (
        report["status"] == "pass" and report["counts"]["tp"] == 1 and report["counts"]["tn"] == 1
    )
    approved = {**cfg, "approval_files": [str(tmp_path / "cal/calibration.json")]}
    assert Judge(approved).approved(rubric)
    assert not Judge({**approved, "model": "changed"}).approved(rubric)
    assert not Judge(approved).approved({**rubric, "criterion": "Changed"})
    r = calibrate(rubric, rows[:1], cfg, tmp_path / "missing")
    assert r["status"] == "insufficient_evidence"
    r = calibrate(
        rubric,
        rows,
        {**cfg, "calibration_policy": {**cfg["calibration_policy"], "uncertainty": "lower_bound"}},
        tmp_path / "uncertain",
    )
    assert r["status"] == "fail"


def test_judge_projection_does_not_leak_reference():
    case = {"input": {"history": [], "secret": "private"}, "references": {"message": "answer"}}
    rubric = {
        "id": "r",
        "field": "message",
        "criterion": "grounded",
        "permitted_input_fields": ["history"],
        "use_reference": False,
    }
    request = judge_request(case, {"message": "candidate", "other": "hidden"}, rubric)
    assert request["context"] == {"history": []}
    assert "reference" not in request and request["candidate"] == {"message": "candidate"}


def test_live_adapter_contract_mock(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps(
                {
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": '{"passed":true,"reason":"Supported"}'}],
                    "usage": {"input_tokens": 12, "output_tokens": 8},
                }
            ).encode()

    def urlopen(req, timeout):
        captured.update(payload=json.loads(req.data), headers=dict(req.headers), timeout=timeout)
        return Response()

    monkeypatch.setenv("TEST_JUDGE_KEY", "fake-test-key")
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    response, usage = anthropic_judge(
        {"criterion": "Check"},
        {
            "api_key_env": "TEST_JUDGE_KEY",
            "model": "explicit-model",
            "parameters": {"max_tokens": 100},
        },
    )
    assert response["passed"] and usage["output_tokens"] == 8
    assert captured["payload"]["model"] == "explicit-model"
    assert captured["payload"]["max_tokens"] == 100
    assert "tools" not in captured["payload"]


def test_resource_caps_require_bounded_adapter_and_stop_before_call(fixture_data, tmp_path):
    *_, build = fixture_data
    dataset = build()
    cfg = config(tmp_path)
    cfg.update(max_tokens=100)
    with pytest.raises(HindsightError, match="bounded adapter"):
        run_candidate(
            dataset, "dev", "tests.candidates:assert_inputs", cfg, tmp_path / "invalid-budget"
        )
    cfg.update(bounded_adapter=True, token_upper_bound_per_call=60)
    result = run_candidate(
        dataset, "dev", "tests.candidates:assert_inputs", cfg, tmp_path / "bounded"
    )
    assert result["calls"] == 1
    assert result["resource_reservations"]["tokens"] == 60
    assert read_jsonl(tmp_path / "bounded/predictions.jsonl")[1]["status"] == "budget_exhausted"
