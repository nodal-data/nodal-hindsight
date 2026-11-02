# Nodal Hindsight

Compile logged AI suggestions, human decisions, and subsequent outcomes into evidence-backed evaluation cases. Run a decision function, score saved outputs, and compare a candidate against a baseline.

This repository implements the first-release workflow in [spec.md](spec.md). The examples are synthetic; they do not establish production accuracy or approved Health Tech policies.

## Quick start

Python 3.11+ and uv are required.

```bash
uv sync --locked --extra dev
uv run python scripts/demo.py
```

The demo creates `artifacts/demo/`. It validates and compiles the Health Tech fixtures, runs two candidate functions, evaluates their outputs, compares them, exports training cases, creates a correction report, and exercises judge calibration. It also compiles an earlier dataset snapshot to show evidence maturity changing a case revision.

The candidate deliberately closes an unresolved case. **The comparison must fail.** The demo handles that expected exit code and finishes successfully. The two-row calibration fixture must return insufficient evidence; it is not approved for gates.

To rerun into the same destination:

```bash
uv run python scripts/demo.py --overwrite
```

Inspect:

- `artifacts/demo/dataset/quality-report.md`: counts, tiers, censoring, exclusions, and bias diagnostics.
- `artifacts/demo/comparison/report.md`: regression details and component-score changes.
- `artifacts/demo/logged-train-eval/report.md`: original suggestions scored against eligible references, plus experimental judge responses.
- `artifacts/demo/review/corrections.jsonl`: training-only human corrections and draft diffs.
- `artifacts/demo/calibration/report.md`: calibration confusion counts and uncertainty.

## Commands

| Command | Purpose |
|---|---|
| `hindsight validate` | Validate canonical records, task schemas, event joins, and chronology |
| `hindsight compile` | Produce immutable cases, splits, definitions, input-only views, and a manifest |
| `hindsight run` | Invoke a trusted `module:function` on input-only cases in timed subprocesses |
| `hindsight evaluate` | Score one run, external prediction file, or original logged suggestions |
| `hindsight compare` | Compare compatible evaluations on identical cases and apply gates |
| `hindsight calibrate` | Measure a rubric/judge against independently reviewed calibration labels |
| `hindsight review` | Generate training-only correction reports and review templates |
| `hindsight export` | Write canonical cases and definitions, or input-only records |
| `hindsight schemas` | Generate public JSON Schema contracts |

Every command supports `--help`. Commands in the spec work directly; `examples/health_tech/task.yaml` points to `tasks.jsonl` as the task configuration's single source of truth. `--config` also accepts the JSONL catalog directly.

```bash
uv run hindsight validate --traces examples/health_tech/decisions.jsonl --events examples/health_tech/events.jsonl --config examples/health_tech/tasks.jsonl
uv run hindsight compile --traces examples/health_tech/decisions.jsonl --events examples/health_tech/events.jsonl --config examples/health_tech/task.yaml --as-of 2026-10-01T00:00:00Z --out artifacts/dataset
uv run hindsight run --dataset artifacts/dataset --split dev --candidate examples.health_tech.candidates:baseline --run-config examples/health_tech/baseline.yaml --out artifacts/baseline-run
uv run hindsight evaluate --dataset artifacts/dataset --run artifacts/baseline-run --scoring-config examples/health_tech/scoring.yaml --out artifacts/baseline-eval
```

Use `evaluate --source logged --split dev` for the historical baseline, or `evaluate --predictions file.jsonl --split dev` for external predictions. `run --evaluate --scoring-config ...` writes a separate sibling directory ending in `-eval`. `--resume` reuses completed compatible cases; `--overwrite` explicitly replaces an artifact directory.

Test-split run, evaluation, and export require `--allow-test`. Do not reuse an inspected test set for iterative selection. Calibration groups are configured in `split_policy.calibration_groups`, must come from the training interval, and are removed from training/evaluation cohorts.

Exit codes: 0 success/pass; 1 failed comparison gate; 2 input/configuration/processing error; 3 insufficient evidence for a requested comparison or calibration. Evaluating predictions successfully writes reports even when individual cases fail; use `compare` for a process-level gate.

## Your decision function

```python
from nodal_hindsight.schemas import CandidateResponse, RunContext


def predict(context: dict, run: RunContext) -> CandidateResponse:
    # Call your application here. Context contains only the frozen application input.
    # run.candidate_config contains declared model/prompt/code identity and parameters.
    output = your_application(context)
    return CandidateResponse(
        output=output,
        usage={"input_tokens": 100, "output_tokens": 40},
        model=run.candidate_config["model"],
    )
```

The callable is trusted application code, not sandboxed by Hindsight. The runner withholds targets by API design, but it cannot prevent deliberately written application code from reading local files or making its own network calls. It suppresses application stdout/stderr to keep exception payloads out of diagnostics. Candidate results must be JSON-serializable.

Runner configuration includes model/prompt/code identities, generation parameters, timeout, retry and call/case limits, and opt-in caching. Update `code_version` when transitive dependencies or prompt rendering change; the runner also hashes the callable module's source. Unknown identities disable shared candidate caching. Model versions returned by the application must agree with the declared model when it is known.

Candidate and judge limits are separate. Arbitrary callable-internal requests cannot be metered automatically. Optional `max_tokens`/`max_cost_usd` require `bounded_adapter: true` and matching `token_upper_bound_per_call`/`cost_upper_bound_per_call`: the runner conservatively reserves those bounds before each attempt, including retries. The adapter must actually enforce its declared bounds; otherwise use call limits only. Usage/cost summaries are unavailable when the application does not provide measurements.

## Data and labels

See [the Health Tech examples](examples/health_tech/README.md) for the nine scenarios. `tasks.jsonl` defines task semantics, not responder activities. Activities and outcomes are separate append-only events.

- Accepted unchanged is Weak and descriptive, not a trusted training target.
- An action override can support Silver; an edited message can support Bronze.
- Only mature, explicitly attributed outcomes upgrade configured fields to outcome-supported Gold. They do not prove causal impact or validate every field.
- Human adjudication can supply acceptable alternatives or explicit negative targets.
- Missing references, null/not-applicable messages, and observed empty follow-up sets remain distinct.
- Evidence is bounded by both `as_of` and the end of the case's train/dev/test partition.
- Boundary-spanning groups are quarantined; duplicate inputs cannot cross partitions; embargoes are configurable.

`schema_version: "1.0"` is required for supported records. Unknown top-level canonical fields fail validation. Vendor data belongs in namespaced `extensions`. Task input/output JSON Schemas may use local references; external schema lookups are disallowed. Invalid rows fail compilation unless explicitly quarantined. Conflicting IDs and broken attribution links always fail rather than silently selecting one answer.

Task settings are deliberately constrained: unsupported rule kinds/settings fail rather than executing arbitrary expressions. Initial field roles are categorical, set, ranked-list, and prose. `scorers` supports exact/cost-weighted agreement, top-k, set measures, edit similarity, explicit-negative avoidance, and rubric scoring. Composition is optional; keys are `field/metric`, with `rubric:<id>` for rubric metrics. Null/not-applicable fields may be explicitly renormalized; unknown/weak evidence cannot be silently omitted from a required objective.

## Judges and calibration

The fixture judge returns explicit canned JSON responses. It is useful for testing the workflow, not for assessing language quality.

The optional live adapter uses the [Anthropic Messages API](https://platform.claude.com/docs/en/api/http/messages/create), verified against its primary reference during implementation. It uses Python's standard library and needs no model SDK. Select your own model explicitly:

```yaml
# scoring-live.yaml
judge:
  provider: anthropic
  model: YOUR_EXPLICIT_MODEL_ID
  api_key_env: ANTHROPIC_API_KEY
  api_version: '2023-06-01'
  adapter_version: '1'
  parameters:
    max_tokens: 512
  timeout_seconds: 30
  max_calls: 20
  retries: 1
  cache_dir: .cache/hindsight/live-judges
```

Configure the key in your environment, then pass `--scoring-config scoring-live.yaml`. The rubric's `permitted_input_fields` controls context projection; only its selected candidate field is sent, and references are sent only when `use_reference` is true. Live calls are never enabled implicitly. Model responses must parse as exactly a boolean `passed` and string `reason`; malformed responses and exhausted budgets remain errors.

Judge caches bind the rendered request, rubric, model, parameters, and adapter identity. Fresh model calls are not assumed deterministic. Rubric results remain experimental until calibration and explicit approval. A valid approval must match the full rubric/judge identity; changing either invalidates reuse.

```bash
uv run hindsight calibrate --rubric examples/health_tech/calibration-rubric.yaml --labels examples/health_tech/calibration-labels.jsonl --judge-config examples/health_tech/calibration-judge.yaml --out artifacts/calibration
```

The sample intentionally has too few labels and exits 3. For real calibration, provide group-disjoint reviewed calibration records and explicit minimum class counts, TPR/TNR thresholds, and a point-estimate or lower-confidence-bound policy. `--approved-by REVIEWER` records explicit approval only if thresholds pass. Add the resulting `calibration.json` path to `judge.approval_files` for scoring. Calibration groups may not overlap the evaluation cohort. A fixture judge is never evidence of live-model quality, even if its canned responses match labels.

## Reviews and exports

`hindsight review` creates a seeded training-only review template. To import completed reviews, add `schema_version`, `record_type: review`, and a unique `event_id`; fill reviewer/timestamps/disposition/field evidence/reason; append the records to the events stream and compile a new dataset. Never modify an existing compiled bundle. See `schemas/ReviewDecision.schema.json` for the contract.

JSONL export includes definitions and a sidecar manifest. Training exports strip weak/unknown positive targets. Input-only exports contain no answers. Platform-native optimizer adapters are deliberately deferred; these exports do not claim DSPy/GEPA/MLflow compatibility yet.

## Validation and limits

```bash
uv run pytest -q
uv run ruff check src tests scripts/demo.py
uv run pytest examples/pytest -q
uv run hindsight schemas --out schemas
```

Normal tests use local fixtures and a mocked HTTP adapter. An optional real-provider smoke test requires `HINDSIGHT_LIVE_TEST=1`, `HINDSIGHT_JUDGE_MODEL`, and `ANTHROPIC_API_KEY`; it incurs a model call. No live provider smoke test was required for the implementation demo.

Current boundaries: serial local execution; exact duplicate detection rather than semantic deduplication; no automatic rubric mining, production connectors, optimizer execution, OPE estimator, or production rollout. Monetary caps depend on a cooperating adapter, not interception of arbitrary user code. Comparison confidence intervals quantify variation across case groups, not uncertainty from repeated live judge sampling. The supplied cohort is too small to establish production effectiveness.

Real Health Tech mapping and production thresholds remain pilot decisions.

## License

Nodal Hindsight is licensed under the [Apache License 2.0](LICENSE).
Copyright 2026 Nodal Data, Inc.
