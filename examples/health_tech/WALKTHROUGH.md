# Live demo walkthrough

A presenter runbook for showing Hindsight to a technical audience one stage at a time. It runs the same commands as `scripts/demo.py`, by hand, and looks at the data between steps with `scripts/view_jsonl.py`.

All data is synthetic. Nothing here needs an API key or network access: the decision functions are deterministic and the judges are fixtures. Every command finishes in about a second.

The story in one line: **production logs of AI suggestions, human decisions and outcomes become graded test cases, and those cases catch a regression before it ships.**

## 0. Setup (before the audience arrives)

Run everything from the repo root.

```bash
uv sync --locked --extra dev

EX=examples/health_tech
OUT=artifacts/walkthrough
hs() { uv run hindsight "$@"; }
view() { python3 scripts/view_jsonl.py "$@"; }

rm -rf $OUT .cache/hindsight/candidates
```

- `hs` is just a short alias for `uv run hindsight`. Candidate modules are resolved from the working directory, which is why the commands run from the repo root.
- Clearing `.cache/hindsight/candidates` makes `run` execute the functions for real. Otherwise predictions come back with `cache_hit: true`.
- Every writing command below carries `--overwrite`, so you can rehearse repeatedly.

Exit codes, worth stating up front: `0` pass, `1` a gate failed, `2` input or config error, `3` insufficient evidence. Two steps below exit non-zero on purpose.

## 1. The raw inputs: what was logged

**Say:** three things get logged in production. The AI's suggestion, what the human did with it, and what happened afterwards.

```bash
view $EX/decisions.jsonl --record 1
```

Point at:
- `context`: what the model saw (`prior_state`, `channel`, `history`).
- `policy`: which model, prompt and code version produced it.
- `suggestion`: the three output fields, `next_action`, `follow_ups`, `message`.

```bash
view $EX/events.jsonl --record 1    # human_action, kind "accepted"
view $EX/events.jsonl --record 6    # human_action, kind "overridden" (decision 004)
view $EX/events.jsonl --record 4    # an outcome (decision 003)
```

Point at:
- Record 1: the responder accepted the suggestion unchanged.
- Record 6: the AI suggested `close_case`; the responder escalated instead, with a reason. **Remember decision 004. It comes back in step 8.**
- Record 4: an outcome, with separate `event_at` and `observed_at` timestamps. That gap matters in step 4.

Then show the nine scenarios in the table in [README.md](README.md) (the "Decision suffix" table). Each row is a different evidence situation.

Finally, skim the task definition. It is one large record, so page it:

```bash
view $EX/tasks.jsonl | less
```

Point at `field_roles`, `label_policy`, `split_policy`, `scorers` and `gates`. This file is the whole configuration: how fields are graded, where the time splits fall, and what counts as a pass.

## 2. validate

```bash
hs validate --traces $EX/decisions.jsonl --events $EX/events.jsonl --config $EX/task.yaml
```

Output:

```json
{ "decisions": 9, "events": 12, "tasks": ["health_tech.next_action"], "status": "valid" }
```

**Say:** schemas check out, every event joins to a decision, and the timeline is consistent. Nothing is written.

## 3. compile: logs into evidence-backed cases

This is the core step. Spend the most time here.

```bash
hs compile --traces $EX/decisions.jsonl --events $EX/events.jsonl --config $EX/task.yaml \
  --as-of 2026-10-01T00:00:00Z --out $OUT/dataset --overwrite
```

Output: `"counts": {"train": 4, "dev": 2, "test": 3}`, `"excluded_count": 0`. The split is by time, not random.

```bash
ls $OUT/dataset
view $OUT/dataset/cases.jsonl --record 1    # decision 001: accepted unchanged
view $OUT/dataset/cases.jsonl --record 5    # decision 004: overridden
```

Point at `labels` in each. Every output field gets its own evidence tier, with the reason and the event IDs behind it.

| Record | Decision | `next_action` | `follow_ups` | `message` |
|---|---|---|---|---|
| 1 | 001 | Weak | Weak | Weak |
| 5 | 004 | Silver | Bronze | Weak |

**Say:**
- **Gold**: adjudicated, or backed by a mature outcome.
- **Silver**: the human overrode the action.
- **Bronze**: the human edited the message or follow-ups.
- **Weak**: accepted unchanged. Nobody confirmed it was right, so it is *not* used to score correctness. Record 1 shows `"reason": "accepted_without_confirmation"`, `"eligible": false`.
- **Unknown**: no usable evidence.

In record 5, note that `references.next_action` is `escalate_to_responder_lead` (what the human did), while `suggestion.next_action` is still `close_case` (what the AI said).

Now show what a decision function is allowed to see:

```bash
view $OUT/dataset/inputs/dev.jsonl --record 1
```

Only `case_id` and `input`. No references, no labels, no outcomes.

```bash
less $OUT/dataset/quality-report.md
```

Point at `label_counts`: Weak 11, Bronze 8, Silver 3, Gold 2, Unknown 3. **Say:** most logged "acceptances" are weak evidence. That is the honest picture of what the logs can and cannot prove.

## 4. compile with an earlier cutoff: no hindsight leakage

```bash
hs compile --traces $EX/decisions.jsonl --events $EX/events.jsonl --config $EX/task.yaml \
  --as-of 2026-09-20T00:00:00Z --out $OUT/dataset-earlier --overwrite

view $OUT/dataset-earlier/quarantine.jsonl
```

Output: test drops from 3 to 2, `"excluded_count": 1`, and the quarantine file holds:

```json
{ "decision_id": "health_tech-demo-008", "reason": "after_observation_cutoff" }
```

**Say:** the dataset is a function of the cutoff date. Evidence that had not been observed by then cannot leak in. The `dataset_id` hash also changed, so a result is always tied to an exact dataset.

## 5. run: the baseline decision function

Show the function first:

```bash
sed -n 6,21p $EX/candidates.py
```

Then run it over the dev split:

```bash
hs run --dataset $OUT/dataset --split dev \
  --candidate examples.health_tech.candidates:baseline \
  --run-config $EX/baseline.yaml --out $OUT/baseline-run --overwrite

view $OUT/baseline-run/predictions.jsonl --record 1
```

Point at:
- Summary: `"calls": 2`, `"coverage": 1.0`, `"test_access": false`.
- `candidate`: the callable plus `module_hash`, `prompt_version` and `code_version`. A run is tied to exact code.
- Prediction 1: `output.next_action` is `escalate_to_responder_lead`, `cache_hit` is `false`.

**Say:** each case runs in its own subprocess with a timeout, and the function receives only `case.input`. In real use this function wraps your agent or prompt.

## 6. evaluate the baseline

```bash
hs evaluate --dataset $OUT/dataset --run $OUT/baseline-run \
  --scoring-config $EX/scoring.yaml --out $OUT/baseline-eval --overwrite

view $OUT/baseline-eval/verdicts.jsonl --record 1 | less
```

Output: `"gates": {"pass": 2}`.

Point at, in the verdict:
- `gate`: `pass`.
- `metrics`: one entry per field and metric (`exact_match`, `cost_weighted_match`, `set_f1`), each with `eligible`, `evidence_ids` and `value`.
- `constraints`: hard rules such as `output_schema` and `message_action_consistency`.

## 7. run and evaluate a candidate with a planted regression

```bash
sed -n 24,28p $EX/candidates.py
```

**Say:** the "improved" version closes any case in state `unresolved_request`. Everything else is unchanged.

```bash
hs run --dataset $OUT/dataset --split dev \
  --candidate examples.health_tech.candidates:candidate \
  --run-config $EX/candidate.yaml --out $OUT/candidate-run --overwrite

hs evaluate --dataset $OUT/dataset --run $OUT/candidate-run \
  --scoring-config $EX/scoring.yaml --out $OUT/candidate-eval --overwrite

view $OUT/candidate-eval/verdicts.jsonl --record 1 | less
```

Output: `"gates": {"fail": 1, "pass": 1}`.

Point at the first metric in the verdict:

```json
"details": {
  "corrections": [{
    "event_id": "human-004",
    "suggested": "close_case",
    "observed": "escalate_to_responder_lead",
    "reason": "The request remains unresolved; closing the case is premature."
  }],
  "prediction": "close_case",
  "reference": "escalate_to_responder_lead"
}
```

**Say:** this is decision 004 from step 1. The candidate repeats the exact mistake a human already corrected in production, and the verdict cites the event that proves it.

## 8. compare: the gate catches it

**Warn first:** this command fails on purpose.

```bash
hs compare --baseline $OUT/baseline-eval --candidate $OUT/candidate-eval \
  --gates $EX/gates.yaml --out $OUT/comparison --overwrite > /dev/null; echo "exit=$?"
```

Output: `exit=1`. The full report is long, so pull out the headline:

```bash
python3 -c "import json; c=json.load(open('$OUT/comparison/comparison.json')); print(json.dumps({k: c[k] for k in ('status','paired_cases','failures','regressions','fixes')}, indent=2))"
```

```json
{
  "status": "fail",
  "paired_cases": 2,
  "failures": [
    "metric_regression:follow_ups/set_f1",
    "metric_regression:next_action/cost_weighted_match",
    "metric_regression:next_action/exact_match",
    "too_many_new_regressions"
  ],
  "regressions": ["ed8c494193b6a7a4cbcad0bc"],
  "fixes": []
}
```

**Say:** baseline and candidate are paired on the same cases, and the gates in `gates.yaml` use a seeded bootstrap, so the result is reproducible. Exit code 1 is what a CI job would block on. The regressed case ID is the decision 004 case.

## 9. Score what actually shipped

```bash
hs evaluate --dataset $OUT/dataset --source logged --split train \
  --scoring-config $EX/scoring.yaml --out $OUT/logged-train-eval --overwrite
```

Output: `"count": 4`, `"gates": {"insufficient_evidence": 1, "fail": 3}`.

**Say:** `--source logged` grades the original production suggestions with the same scorer. Three of four fail because humans corrected them. The fourth is decision 001: accepted unchanged, all Weak, so the tool reports `insufficient_evidence` rather than calling it a pass.

## 10. review: where humans corrected the AI

```bash
hs review --dataset $OUT/dataset --out $OUT/review --overwrite

view $OUT/review/corrections.jsonl --record 1
view $OUT/review/review-template.jsonl --record 1
```

Output: `"correction_count": 7`, by field: message 3, next_action 2, follow_ups 2.

Point at the `diff` in correction 1 (decision 002):

```diff
-**Hello!** We are delighted to confirm your appointment tomorrow at 10am.
+Would tomorrow at 10am work for you?
```

**Say:** the AI confirmed an appointment nobody had agreed to; the responder turned it into a question. The review template is the blank form a reviewer fills in to promote weak evidence to a higher tier. The report covers the training split only.

## 11. export: training data

```bash
hs export --dataset $OUT/dataset --split train --out $OUT/train-export --overwrite
view $OUT/train-export/cases.jsonl --record 1
```

Output: `"count": 3`. Train has 4 cases; decision 001 is dropped because all of its fields are Weak. Record 1 (decision 002) keeps only `references.message`, the one field a human actually corrected.

Optional, to show the test-split guard:

```bash
hs export --dataset $OUT/dataset --split test --out $OUT/test-export --overwrite; echo "exit=$?"
```

Output: `test access requires --allow-test`, `exit=2`.

## 12. calibrate: can the LLM judge be trusted?

**Warn first:** this one exits 3 on purpose.

```bash
hs calibrate --rubric $EX/calibration-rubric.yaml --labels $EX/calibration-labels.jsonl \
  --judge-config $EX/calibration-judge.yaml --out $OUT/calibration --overwrite; echo "exit=$?"
```

Point at:
- `"status": "insufficient_evidence"`, `exit=3`.
- `counts`: 1 true positive, 1 true negative. The judge agreed with the human both times.
- `tpr_interval`: `[0.2065, 1]`. Two labels prove very little.
- `policy`: needs 10 positive and 10 negative labels and a lower bound of 0.8.

**Say:** a rubric judge has to earn trust against human labels before its scores count. Perfect agreement on two examples is not enough, and the tool says so.

## Short version (about 10 minutes)

Steps 0, 1, 3, 5, 6, 7, 8. That covers logs, graded cases, a run, and the regression gate.

## Caveats to say out loud

- The data is synthetic and tiny. Dev has 2 cases, so confidence intervals are wide. This shows the mechanics, not production effectiveness.
- The judges are fixtures with canned answers. A live Anthropic judge exists but is opt-in, costs money and is non-deterministic, so it stays out of the demo.
- Latencies and timestamps differ between runs. Case IDs, scores and gate results do not.

## If something goes wrong

Regenerate every output in one go, then carry on with the `view` commands:

```bash
uv run python scripts/demo.py --out artifacts/walkthrough --overwrite
```
