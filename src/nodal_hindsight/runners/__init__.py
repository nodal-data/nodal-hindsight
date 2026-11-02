"""Local serial runner with subprocess timeouts and durable checkpoints."""

import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nodal_hindsight.common import (
    HindsightError,
    artifact_dir,
    canonical,
    digest,
    read_json,
    write_json,
    write_jsonl,
)
from nodal_hindsight.datasets import load_dataset
from nodal_hindsight.ingest import parse_model, validate_schema
from nodal_hindsight.schemas import RunConfig


def run_candidate(
    dataset, split, candidate, run_config, out, allow_test=False, resume=False, overwrite=False
):
    manifest, cases = load_dataset(dataset, split, allow_test)
    cfg = parse_model(RunConfig, run_config)
    for cap, bound in (
        ("max_tokens", "token_upper_bound_per_call"),
        ("max_cost_usd", "cost_upper_bound_per_call"),
    ):
        if cfg[cap] is not None and (not cfg["bounded_adapter"] or cfg[bound] is None):
            raise HindsightError(
                "resource caps require a cooperating bounded adapter and per-call upper bounds"
            )
    if ":" not in candidate:
        raise HindsightError("candidate must be a trusted module:function import")
    try:
        module = importlib.util.find_spec(candidate.split(":", 1)[0])
        module_hash = digest(Path(module.origin).read_text()) if module and module.origin else None
    except (ImportError, OSError, AttributeError):
        raise HindsightError("cannot locate candidate module") from None
    if not module_hash:
        raise HindsightError("candidate module must have inspectable source")
    identity = {
        "callable": candidate,
        "module_hash": module_hash,
        **{
            k: cfg[k]
            for k in ("model", "prompt_version", "code_version", "parameters", "adapter_version")
        },
    }
    run_id = digest(
        {"dataset_id": manifest["dataset_id"], "split": split, "candidate": identity, "config": cfg}
    )
    out = Path(out)
    previous = []
    calls = 0
    if out.exists():
        if resume:
            checkpoint = read_json(out / "checkpoint.json")
            if checkpoint["run_id"] != run_id:
                raise HindsightError("resume identity/configuration mismatch")
            previous = checkpoint["predictions"]
            calls = checkpoint["calls"]
        elif not overwrite:
            raise HindsightError("run destination exists; use --resume or --overwrite")
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "run-manifest.json", {"run_id": run_id, "status": "incomplete"})
    done = {p["case_id"]: p for p in previous if p["status"] == "success"}
    predictions = []
    cache_enabled = cfg["cache"] and all(
        identity[k] != "unknown" for k in ("model", "prompt_version", "code_version")
    )
    for index, case in enumerate(cases):
        if case["case_id"] in done:
            predictions.append(done[case["case_id"]])
            continue
        prediction = {
            "case_id": case["case_id"],
            "output": None,
            "status": "budget_exhausted",
            "candidate": identity,
            "usage": {},
            "latency_seconds": None,
            "cache_hit": False,
            "cache_read_seconds": None,
            "attempts": [],
        }
        key = digest(
            {
                "case_id": case["case_id"],
                "input": case["input"],
                "task": manifest["tasks"][case["task"]],
                "candidate": identity,
            }
        )
        cache = Path(cfg["cache_dir"]) / f"{key}.json"
        if cache_enabled and cache.exists() and index < cfg["max_cases"]:
            start = time.monotonic()
            cached = read_json(cache)
            if cached.get("key") != key:
                raise HindsightError("candidate cache integrity mismatch")
            prediction.update(
                cached["prediction"],
                case_id=case["case_id"],
                cache_hit=True,
                cache_read_seconds=time.monotonic() - start,
            )
        elif index < cfg["max_cases"]:
            for attempt in range(cfg["retries"] + 1):
                reserved_over_cap = any(
                    cfg[cap] is not None and (calls + 1) * cfg[bound] > cfg[cap]
                    for cap, bound in (
                        ("max_tokens", "token_upper_bound_per_call"),
                        ("max_cost_usd", "cost_upper_bound_per_call"),
                    )
                )
                if calls >= cfg["max_calls"] or reserved_over_cap:
                    break
                calls += 1
                # Reserve/persist attempts before executing; a crash cannot reset the call budget.
                write_json(
                    out / "checkpoint.json",
                    {
                        "run_id": run_id,
                        "calls": calls,
                        "predictions": list(
                            {**done, **{p["case_id"]: p for p in predictions}}.values()
                        ),
                    },
                )
                context = {
                    "case_id": case["case_id"],
                    "candidate_config": identity,
                    "timeout_seconds": cfg["timeout_seconds"],
                    "deadline": (
                        datetime.now(timezone.utc) + timedelta(seconds=cfg["timeout_seconds"])
                    ).isoformat(),
                }
                start = time.monotonic()
                usage = {}
                try:
                    env = dict(os.environ)
                    env["PYTHONPATH"] = os.pathsep.join([os.getcwd(), *sys.path])
                    result = subprocess.run(
                        [sys.executable, "-m", "nodal_hindsight.runners.worker"],
                        input=canonical(
                            {"callable": candidate, "input": case["input"], "context": context}
                        ),
                        text=True,
                        capture_output=True,
                        timeout=cfg["timeout_seconds"],
                        env=env,
                    )
                    data = json.loads(result.stdout)
                    if result.returncode or data["status"] != "success":
                        raise ValueError
                    response = data["response"]
                    if (
                        response.get("model")
                        and cfg["model"] != "unknown"
                        and response["model"] != cfg["model"]
                    ):
                        raise ValueError
                    prediction["output"] = response["output"]
                    usage = response.get("usage", {})
                    prediction["usage"] = usage
                    try:
                        validate_schema(
                            manifest["tasks"][case["task"]]["output_schema"], response["output"]
                        )
                        prediction["status"] = "success"
                    except HindsightError:
                        prediction["status"] = "invalid_output"
                except subprocess.TimeoutExpired:
                    prediction["status"] = "timeout"
                except (ValueError, KeyError, OSError):
                    prediction["status"] = "error"
                elapsed = time.monotonic() - start
                prediction["latency_seconds"] = elapsed
                prediction["attempts"].append(
                    {
                        "attempt": attempt + 1,
                        "status": prediction["status"],
                        "latency_seconds": elapsed,
                        "usage": usage,
                    }
                )
                if prediction["status"] == "success":
                    if cache_enabled and cfg["retain_output"]:
                        write_json(cache, {"key": key, "prediction": prediction})
                    break
        if not cfg["retain_output"]:
            prediction["output"] = None
            prediction["status"] = "error"
        predictions.append(prediction)
        write_json(
            out / "checkpoint.json", {"run_id": run_id, "calls": calls, "predictions": predictions}
        )
        write_json(out / "run-manifest.json", {"run_id": run_id, "status": "incomplete"})
    report = {
        "run_id": run_id,
        "dataset_id": manifest["dataset_id"],
        "split": split,
        "candidate": identity,
        "config": cfg,
        "status": "complete",
        "calls": calls,
        "test_access": split == "test",
        "prediction_hash": digest(predictions),
        "coverage": sum(p["status"] == "success" for p in predictions) / len(cases) if cases else 0,
        "resource_reservations": {
            "tokens": calls * cfg["token_upper_bound_per_call"]
            if cfg["token_upper_bound_per_call"]
            else None,
            "cost_usd": calls * cfg["cost_upper_bound_per_call"]
            if cfg["cost_upper_bound_per_call"]
            else None,
        },
        "cost": "Measured only when supplied; configured caps rely on the declared bounded adapter contract.",
    }
    with artifact_dir(out, overwrite=True) as dest:
        write_json(dest / "run-manifest.json", report)
        write_jsonl(dest / "predictions.jsonl", predictions)
        write_jsonl(dest / "errors.jsonl", [p for p in predictions if p["status"] != "success"])
        write_json(
            dest / "checkpoint.json", {"run_id": run_id, "calls": calls, "predictions": predictions}
        )
    return report
