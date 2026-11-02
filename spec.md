# Nodal Hindsight — Reconciled Implementation Specification

Status: proposed implementation specification for review.
Date: 2026-10-31

## 1. Purpose and decisions

Nodal Hindsight turns logged AI suggestions, human decisions, and subsequent outcomes into versioned evaluation cases, then runs and scores candidate decision systems against those cases.

The product owns the semantics of evidence and evaluation: which observations support a reference answer, which fields can be scored, how feedback reaches an optimizer, and whether a candidate improves on a baseline. It provides a lightweight local evaluation runner while keeping scorers and data contracts usable by pytest, external experiment systems, and future orchestrators.

This specification reconciles [the earlier implementation spec](./specs/spec-202d6-10-31.md) and [nodal-hindsight v0 spec.md](./specs/nodal-hindsight%20v0%20spec.md), informed by the [original landscape assessment](./specs/Mining%20Production%20Traces%20into%20Evals%20and%20Hill-Climbing_%20Landscape%20and%20Gap%20Assessment%20for%20a%20Priors%20to%20Evals%20to%20Optimize%20Repo.md). It supersedes their conflicting implementation recommendations. It does not independently validate their market or vendor claims.

Agreed direction:

- A generic task schema, with an Health Tech-like decision assistant as the first example.
- Runnable evaluations in the first release: invoke a supplied application function, persist predictions, score them, and compare candidates.
- Deterministic scorers and injected LLM rubric judges in the first release.
- Component-level evidence, explicit uncertainty, immutable artifacts, and leakage-resistant splits.
- JSONL first; one optimizer integration follows the complete evaluation workflow.

The repo currently contains specification documents, not an implementation. Commands and APIs below are contracts to implement. Writing this specification does not itself start implementation.

## 2. Scope

### Initial release

Deliver a Python library and CLI with:

1. Decision Trace Format (DTF), human-action/outcome events, task configuration, and generated JSON Schemas.
2. Validation, explicit event joins, label adjudication, immutable dataset compilation, and quality reports.
3. A local callable runner with prediction persistence, caching, error handling, and resource limits.
4. Deterministic metrics and an injected judge interface with one optional provider adapter.
5. Rubric definitions, human calibration records, judge validation, and experimental versus gate-eligible judge results.
6. Evaluation and baseline comparison with coverage, slices, regression details, and configurable gates.
7. Correction-review artifacts, JSONL export, a pytest integration example, and an end-to-end synthetic Health Tech-like demo.

### Later releases

- Model-assisted rubric discovery and playbook proposals.
- One tested DSPy/GEPA integration, then source/sink adapters prioritized from pilot evidence.
- Temporal example if needed; production workflows live in the application repository.
- Off-policy evaluation only with explicit identifiability and support checks.
- SFT/DPO exports only after defining appropriate preference-label eligibility.

### Outside the product

No production trace store, annotation website, tracing platform, new optimizer algorithm, automatic production deployment, online exploration policy, or hosted experiment service. Local evaluation artifacts and comparisons are in scope; building a general experiment-tracking platform is not.

## 3. Architecture and package

Proposed distribution: `nodal-hindsight`; Python import: `nodal_hindsight`; CLI: `hindsight`. Package availability and license must be checked before publication.

Use Python 3.11+, Pydantic v2, Typer, safe YAML loading, JSONL artifacts, pytest, Ruff, and a committed uv lockfile. External integrations are optional dependencies. Keep the deterministic core free of model SDK requirements.

```text
src/nodal_hindsight/
  schemas/          # DTF, events, tasks, cases, predictions, verdicts, manifests
  ingest/           # JSONL reader and source adapter protocol
  labels/           # evidence joins, adjudication, review decisions
  datasets/         # compilation, deduplication, splits, exports
  scoring/          # deterministic scorers, composition, feedback
  judges/           # rubric rendering, injected interface, cache, calibration
  runners/          # local callable execution, limits, prediction persistence
  evaluation/       # scoring runs, comparison, intervals, gates
  review/           # corrections, review templates, rubric records
  integrations/     # optional provider and later platform adapters
  cli.py
examples/health_tech/     # synthetic traces/events, task config, candidate functions
examples/pytest/    # application-independent scorer/runner example
schemas/           # generated JSON Schemas
tests/
```

The library exposes typed equivalents of `validate`, `compile_dataset`, `run_candidate`, `evaluate_predictions`, `compare_evaluations`, and `export_dataset`. The CLI delegates to these functions.

Scorers are reusable independently of the runner. Deterministic scorers perform no I/O. Model-assisted scoring delegates to an injected judge; provider calls, caching, and budgets belong to the judge execution layer. Given identical case data, output, configuration, and judge responses, scoring results must agree across runners.

## 4. Workflow and CLI

```text
validate → compile → run → evaluate → compare → export
```

Each stage writes inspectable artifacts. Rescoring stored outputs must not require rerunning the candidate model.

```bash
uv sync --extra dev

uv run hindsight validate \
  --traces examples/health_tech/decisions.jsonl \
  --events examples/health_tech/events.jsonl \
  --config examples/health_tech/task.yaml

uv run hindsight compile \
  --traces examples/health_tech/decisions.jsonl \
  --events examples/health_tech/events.jsonl \
  --config examples/health_tech/task.yaml \
  --as-of 2026-10-01T00:00:00Z \
  --out artifacts/dataset

uv run hindsight run \
  --dataset artifacts/dataset --split dev \
  --candidate examples.health_tech.candidates:baseline \
  --run-config examples/health_tech/baseline.yaml \
  --out artifacts/baseline-run

uv run hindsight evaluate \
  --dataset artifacts/dataset --run artifacts/baseline-run \
  --scoring-config examples/health_tech/scoring.yaml \
  --out artifacts/baseline-eval

uv run hindsight run \
  --dataset artifacts/dataset --split dev \
  --candidate examples.health_tech.candidates:candidate \
  --run-config examples/health_tech/candidate.yaml \
  --out artifacts/candidate-run

uv run hindsight evaluate \
  --dataset artifacts/dataset --run artifacts/candidate-run \
  --scoring-config examples/health_tech/scoring.yaml \
  --out artifacts/candidate-eval

uv run hindsight compare \
  --baseline artifacts/baseline-eval \
  --candidate artifacts/candidate-eval \
  --gates examples/health_tech/gates.yaml \
  --out artifacts/comparison

uv run hindsight export \
  --dataset artifacts/dataset --split train --format jsonl \
  --out artifacts/train-export
```

The supplied fixture dates and package layout must make these commands work after installation. Synthetic candidates and a fixture judge allow the complete example to run without credentials or network calls.

Additional modes:

- `evaluate --predictions predictions.jsonl --split dev`: score externally produced outputs without using the built-in runner.
- `evaluate --source logged --split dev`: score original logged suggestions as a baseline, preserving original policy identities and flagging mixed-policy cohorts.
- `run --evaluate --scoring-config ...`: convenience orchestration that writes the same separate prediction and evaluation artifacts.
- `calibrate --rubric ... --labels ... --judge-config ... --out ...`: validate a rubric/judge configuration against human labels.

`evaluate` measures one prediction set. `compare` compares two completed, compatible evaluation runs; it does not silently rerun either candidate or judge. `export` packages data for other tools; it does not optimize a prompt.

## 5. Task configuration

Each task has an ID, schema version, input schema, output schema, field roles, and domain rules. Health Tech is configuration rather than a hardcoded canonical output.

Supported output field roles:

| Role | Examples | Scorers |
|---|---|---|
| Categorical | next action | Exact agreement, configured confusion cost |
| Set | follow-up activities | Precision, recall, F1, Jaccard |
| Ranked list | ordered recommended actions | Top-k agreement |
| Prose | draft message | Edit distance, rubric judges |

Configuration specifies vocabularies, required/nullable fields, applicability rules, constraints, outcome definitions, maturity windows, attribution rules, splits, slices, scorer composition, rubric versions, and gate thresholds. Use a validated declarative rule vocabulary rather than arbitrary executable expressions in YAML.

The Health Tech example uses `next_action`, `follow_ups`, and `message`. The initial compiler/scorer must also pass a fixture with different task and field names to demonstrate that these are not embedded in the core.

Example scoring weights may be 0.5 action, 0.2 follow-ups, and 0.3 message, but are illustrative. No universal weights, outcome window, action-cost matrix, or channel limits become production defaults.

## 6. Decision Trace Format and events

### 6.1 Common rules

Records are append-only JSONL and include `schema_version` and `record_type`. IDs are opaque nonempty strings; UUIDv7 is recommended for new producers but not required for backfills. All timestamps require offsets and normalize to UTC.

Unknown canonical fields fail validation; namespaced `extensions` permit additional producer data. Identical repeated IDs/payloads are idempotent. Conflicting payloads under one ID fail. Missing values are not converted to empty lists, empty strings, negative outcomes, or zero scores.

### 6.2 Decision event

Required fields:

- `decision_id`, `task`, `group_id`, `decided_at`.
- `context`: the frozen application input supplied at decision time.
- `context_provenance`: snapshot timestamp and explicit producer assertion of decision-time completeness; configurable paths identify timestamped history fields.
- `policy`: model, prompt version, and application/code version; explicit unknown values are permitted for historical imports and reported.
- `suggestion`: structured logged output matching the task schema.
- `exposure`: `shown`, `hidden`, or `unknown`.

Optional fields include original request/response metadata, tags, source provenance, sampling mechanism/probability, and logging-policy information.

A trace adapter may retain raw LLM request/response fields as evidence, but candidate inputs come only from the task's frozen context. Hidden exposure does not require the suggestion to have been omitted from logging.

A future snapshot or timestamped history item is rejected. An adapter may explicitly truncate before canonicalization, with transformation provenance. The compiler cannot detect future knowledge embedded in untimestamped free text; the producer assertion and this limitation must be visible.

### 6.3 HumanActionEvent

Fields: `event_id`, `decision_id`, `group_id`, `event_at`, `observed_at`, optional pseudonymous actor, `kind`, observed output, optional reason, source, and optional attribution metadata.

Kinds: `accepted`, `edited`, `overridden`, `independent`, `rejected`, `escalated`, `none`, `unknown`. Rejection or escalation does not imply a complete alternative reference. Explicit null, observed empty values, and absent fields have distinct semantics.

The human output uses the task's field definitions but may be partial. Derive field-level differences and compare them with the declared kind. Contradictions are review flags. An action override and a prose edit can coexist.

For subsequent follow-ups, an event can record a partial follow-up observation; configuration must specify whether events are complete snapshots or additive activities and how the observation window closes. Do not interpret an incomplete window as an empty reference set.

### 6.4 OutcomeEvent

Fields: `event_id`, `decision_id`, `group_id`, `event_at`, `observed_at`, `outcome_type`, optional value, source, and attribution evidence.

`event_at` controls the outcome window. `observed_at` controls when the evidence was available to compilation. Both matter for delayed reporting and backfills.

Join by explicit decision ID and verify group consistency. Shared customer/group identity alone is not attribution. Ambiguous resolution after several decisions remains ambiguous unless domain rules or a review decision resolve it. Missing outcomes remain unknown/censored.

### 6.5 ReviewDecision

An immutable review record identifies the decision/case, affected fields, reviewer, timestamp, references or acceptable alternatives, exclusions or negative targets, disposition, and reason. Review evidence must respect the same knowledge-time boundaries as other labels.

### 6.6 Logging-policy metadata

Preserve the randomized variable, policy version, action space, selected value, probability distribution when available, and assignment mechanism. Validate finite probabilities, normalization, and consistency. A partial selected propensity is preserved as incomplete metadata, not presented as OPE-ready.

AI suggestion probabilities must never be reinterpreted as probabilities of the human's executed action.

## 7. Evidence and label adjudication

Assign evidence per output field. Store exposure, outcome status, and negative evidence separately; a single case can have a Silver action reference and a Bronze draft reference.

| Tier | Evidence | Default use |
|---|---|---|
| Gold | Explicit adjudication or mature, attributable outcome support under configured rules | Reference scoring; distinguish adjudicated versus outcome-supported |
| Silver | Human action override or independently chosen action | Agreement with one observed acceptable answer |
| Bronze | Edited/sent prose or observed follow-ups | Reference similarity/agreement for those fields |
| Weak | Accepted unchanged without qualifying confirmation | Behavioral drift only; excluded from trusted objectives |
| Unknown | Missing, disputed, ambiguous, or insufficient evidence | Coverage and review reports; no invented reference |

Rules:

1. A good outcome may support the executed action without validating prose or follow-ups. It does not establish that the original suggestion was wrong or that the executed action caused success.
2. Mature negative outcomes are evidence, not automatic known-bad answers. A complaint, escalation, or no reply becomes an explicit negative target only through appropriate domain rules or adjudication. Escalation can itself be correct behavior.
3. A negative-only case can support negative-target checks but need not have a positive reference. Never invent the alternative action.
4. An override is one acceptable reference, not proof that every disagreement is wrong. Multiple references require adjudication; conflicting observed choices are surfaced rather than automatically merged into an acceptable set.
5. Hidden suggestions remove direct suggestion exposure, not all bias or human error. Report hidden cases as an exposure slice and preserve randomization/sampling metadata.
6. Weak references may measure drift from accepted behavior, but their agreement cannot silently enter correctness metrics, composite objectives, or promotion gates.
7. Outcome upgrades require configured success/failure definitions, attribution, maturity, and conflict resolution. Without those rules, outcomes are retained without promotion.
8. Tier weights are optional configured policy choices, not confidence probabilities. Default reports are stratified and unweighted.
9. Every eligibility decision carries evidence IDs and machine-readable reasons.

## 8. Cases, identity, and compilation

An EvalCase contains:

- `case_id`, `version`, task/schema version, decision and group IDs, decision timestamp, split.
- `input`: frozen decision-time application context.
- Field references, acceptable alternatives where adjudicated, and explicit negative targets with provenance.
- Per-field evidence tiers, eligibility, reason codes, exposure, observation completeness, and review status.
- Rubric IDs/versions, structured correction feedback, source references, policy identity, and slice tags.

Scoring cases are self-contained: no production database joins are needed at runtime. Dataset bundles include referenced task/rubric definitions. The candidate receives only `case.input`, not the scoring case or answer keys.

Case identity derives from task and source decision ID, not context hash alone. A correction group additionally identifies identical normalized task/context snapshots. Preserve original events and occurrence counts; never merge distinct-time observations or labels merely because inputs match.

Case revisions are immutable. Use a deterministic revision digest as `version`, derived from scoring-relevant case content. New evidence/configuration creates a new dataset manifest; changed cases receive a new revision while retaining their case IDs. Original evidence remains available locally under the configured retention/redaction policy.

Compilation:

1. Validate configuration and records.
2. Apply explicit redaction and canonicalization with provenance.
3. Detect ID conflicts and duplicate contexts.
4. Join eligible events as of the requested cutoff.
5. Apply evidence rules and review decisions.
6. Assign groups/temporal splits and enforce partition-specific label cutoffs.
7. Write cases, input-only views, definitions, diagnostics, and manifest atomically.

Default invalid-input behavior is fail-closed. Explicit quarantine mode may continue but must report every exclusion and its reason without leaking raw sensitive text in diagnostics.

Dataset artifacts:

```text
manifest.json
cases.jsonl
inputs/{train,dev,test}.jsonl
definitions/tasks.json
definitions/rubrics.json
splits.json
quality-report.json
quality-report.md
quarantine.jsonl             # diagnostics when requested
```

The manifest records source/configuration hashes, compiler/schema versions, observation cutoff, split and evidence policies, counts, redaction version, and parent dataset identity. Deterministic semantic hashes exclude wall-clock metadata. Existing destinations are not overwritten without an explicit flag.

## 9. Split and leakage policy

Require explicit UTC `train_end`, `dev_end`, and compilation `as_of`. Partition decisions into half-open intervals; test is the newest interval.

- No group appears in more than one split. Boundary-spanning groups are quarantined and counted rather than moved backward in time.
- Training evidence must have been observed before `train_end`; dev evidence before `dev_end`; test evidence by `as_of`. Outcome maturity is evaluated at each boundary.
- Configurable embargo intervals protect overlapping decision/outcome windows.
- Exact normalized input duplicates across splits are removed from later partitions and reported. Near-duplicate detection is deferred and documented as a limitation.
- Rubric discovery, exemplar selection, and optimizer reflection use training data only. Calibration uses a separately designated group-disjoint cohort, excluded from reported dev/test performance; report its dates and provenance.
- Test access requires an explicit flag on run/evaluate/export and is recorded. Optimization never receives test data through its adapter. Inspecting test results and iterating requires a new future holdout for an honest final assessment.
- Logged suggestion, human actions, outcome evidence, references, and feedback must not enter the candidate input view.

Context is preserved as a JSON value, not guaranteed byte-identical serialization. Any deliberate input redaction/transformation creates an explicit dataset variant. Candidate adapters may render that context into an application/model request and must version the rendering code; they may not fetch future production state or add reference answers.

## 10. Local runner

A candidate is an explicitly user-selected trusted Python callable imported by module path. YAML cannot dynamically evaluate code. Application dependencies and credentials remain the caller's responsibility.

Proposed contract:

```python
Candidate = Callable[[dict, RunContext], CandidateResponse]
```

`RunContext` provides a case ID, timeout/deadline, and injected application services if configured, but no targets or label evidence. `CandidateResponse` contains structured output and optional measured model identity, usage, latency, and cost metadata. The runner also measures wall-clock latency and records errors.

Required behavior:

- Identify the candidate using application/code version, prompt version, model, parameters, and adapter/configuration hash.
- Execute only the selected split's input-only records.
- Persist one status per attempted case: success, timeout, invalid output, provider/application error, or skipped due to exhausted budget.
- Preserve raw candidate outputs where the data policy permits; parse failures do not disappear from coverage.
- Enforce configured case limits, concurrency, timeout, retries, and candidate-call limits. Count retries as attempts and preserve their usage.
- Before another call, stop if its configured upper-bound reservation exceeds the remaining budget. Hard monetary/token caps require an adapter that supplies enforceable bounds; otherwise offer call caps and explicitly report monetary usage as measured/estimated/unavailable rather than promising a hard cost limit.
- Candidate and judge budgets are separate. Arbitrary application callables cannot have all internal model calls counted unless they cooperate with the adapter contract.
- Initial implementation supports synchronous callables and serial execution; concurrency is optional and must not change scoring semantics.

Candidate caching is opt-in and keyed by canonical input, task/schema, application/code identity, prompt/template identity, model/version, generation parameters, and relevant adapter configuration. Unknown identities disable shared cache reuse. Temperature zero is not a reproducibility guarantee. Cached results reproduce recorded outputs, not fresh-provider behavior.

Run artifacts include `run-manifest.json`, `predictions.jsonl`, and `errors.jsonl`. Resume checks identity/configuration compatibility and reuses only completed compatible cases. Cache hits retain original invocation metadata and separately report current cache-read timing.

## 11. Scorer and result contracts

Each field scorer returns:

- Metric ID/version, field, direction, value or null.
- Applicability and eligibility, reason codes, evidence references.
- Status: `scored`, `inapplicable`, `error`, or `insufficient_evidence`.
- Pass/fail when a threshold is defined; otherwise null.
- Actionable textual feedback plus structured details.

A composed verdict includes field metrics, optional normalized score, hard-constraint results, coverage, and a tri-state gate result. Missing data or errors must not be represented as passing or zero-valued observations by accident.

The composite is explicit task configuration. Define scaling, metric direction, weights, reference-eligible cohorts, legitimately inapplicable fields, and missing-output treatment. Applicability must derive from frozen case/task evidence rather than allowing a candidate to evade a message score by claiming its own message is unnecessary. Candidate-induced omission receives a configured penalty or blocks the objective.

Feedback includes rubric failures, constraint failures, and relevant correction evidence. Optimizer feedback is available for training cases; dev feedback exposure is an explicit integration policy and test feedback is not sent to optimization.

## 12. Built-in metrics

### Categorical and ranked outputs

- Exact match against a singleton reference or adjudicated acceptable set.
- Confusion counts: reference rows, prediction columns.
- Mean configured error cost, with finite nonnegative values and zero diagonal. For an acceptable set, use the minimum cost and disclose that definition.
- Optional normalized cost score uses a configured positive scale; invalid cost scales fail configuration validation.
- Top-k agreement for unique ranked valid actions.
- Explicit-negative avoidance only for adjudicated/rule-qualified negative targets. Avoidance alone does not establish overall correctness.

### Sets

Per-case precision, recall, F1, and Jaccard, plus macro summaries and micro precision/recall/F1. Both empty scores 1; exactly one empty scores 0 for all four per-case measures. Missing or incomplete reference sets are inapplicable, not empty.

### Prose

Use Unicode NFC normalization and normalized character Levenshtein distance:

`distance = levenshtein(candidate, reference) / max(len(candidate), len(reference), 1)`

Report distance and `1 - distance`. Preserve whitespace/case unless explicitly configured. Edit similarity is correspondence to one human message, not semantic correctness.

### Constraints

Output schema, valid vocabulary, action/channel compatibility, required fields, channel formatting/length, and configured business-hour rules. Domain timezone must be explicit for time-dependent rules. Constraints specify severity and scope; absent configuration never implies compliance.

### Behavioral drift

Separately report agreement with accepted-but-unconfirmed logged behavior. Label it descriptive and exclude it from trusted quality objectives and promotion gates.

## 13. LLM rubric judges in the first release

A rubric is a versioned binary criterion with ID, applicability, criterion text, permitted input fields, optional reference usage, expected structured response, provenance, and approval/calibration status.

Examples: factual consistency with prior state, unsupported commitments, channel appropriateness, and action plausibility. Rubrics may assess structured decisions as well as prose. Reference-free rubric scoring can cover cases without a human reference if the criterion is applicable and the judge has appropriate calibration.

The injected judge takes a structured request containing the rubric, projected context, candidate output, and explicitly permitted reference evidence. It returns a parsed `{passed, reason}` response plus provider/model identity and available usage metadata. Deterministic rendering is versioned. Trace text is delimited as untrusted evidence; it cannot alter rubric instructions or invoke tools.

Ship a fixture judge and one optional live provider adapter selected and documented during implementation. No default live calls; users configure the adapter and permitted fields. Verify the provider's current API against primary documentation when implementing it.

Judge execution must:

- Cache by full rendered request, rubric/version, model/version, parameters, response schema, and adapter version—not prompt text alone.
- Validate structured responses; malformed answers, timeouts, and provider failures are errors, never automatic passes.
- Bound attempts and usage under an explicit judge budget.
- Record cached versus fresh calls, judge identity, response, and reason.
- Avoid fetching case data outside the self-contained evaluation bundle.

Uncalibrated rubrics can run in experimental mode and appear in reports. They are excluded from trusted composite scores and promotion gates. An objective requiring a calibrated judge cannot silently substitute an uncalibrated one or omit its errors.

## 14. Rubric calibration and correction review

Initial release includes human-authored rubrics, review templates, and calibration; automatic rubric mining is later.

Calibration records identify rubric/version, case and candidate-output identity, human pass/fail, reviewer, and review timestamp. Define positive as pass. Report confusion counts, sensitivity/TPR, specificity/TNR, class denominators, and confidence intervals.

Task policy specifies minimum positive/negative counts, thresholds, and how confidence bounds affect approval. Missing classes or insufficient samples return insufficient evidence. A threshold such as 0.8 may appear in illustrative configuration but is not a universally justified default.

Changing rubric criterion, prompt rendering, model/version, generation parameters, or relevant response/adapter behavior invalidates calibration reuse. Approval records bind the full judge configuration and calibration dataset identity. Human approval remains explicit.

Correction reports summarize field transitions, prose diffs, reasons, evidence tiers, slice frequencies, and conflicts using training data. Provide a seeded review sample; 30 cases is a useful starting exercise, not a sufficiency guarantee.

Later assisted mining can cluster human coding notes into proposed rubrics. Generated definitions require review and calibration. No automatic promotion of model-written criteria, playbooks, or exemplars into production policy.

## 15. Evaluation, comparison, and gates

Evaluation consumes cases, predictions, scoring configuration, and optional judge configuration. It writes per-case verdicts, aggregate/slice metrics, coverage/errors, and a manifest identifying all inputs and scorer/rubric/calibration versions.

Comparison requires the same dataset/case revisions, split, scoring definitions, reference eligibility, and judge/calibration configuration. Incompatible results fail with a diagnostic; the user must explicitly reevaluate. Do not silently compare different metrics or filter down to an easier shared subset.

Reports include:

- Per-field and composite deltas, eligible denominators, coverage, and error counts.
- Regressions (baseline pass → candidate fail), fixes, unchanged failures, and unresolved cases.
- Slices by tier, channel/state, exposure, policy, and time where configured.
- Seeded paired bootstrap intervals resampling whole groups, with minimum-group requirements.
- Hard constraint regressions and measured resource differences when comparable measurements exist.

Duplicate or unknown prediction IDs fail validation. Missing outputs and candidate failures count against coverage and cannot improve scores by disappearing. Judge errors similarly remain visible and block required-judge gates when coverage is insufficient.

Gates are configurable: absolute validity/coverage floors, critical constraint requirements, regression tolerances, minimum evidence, metric margins, and resource limits when measurable. Zero new regressions may be a configured rule for a critical slice but is not a universal default.

Gate result: `pass`, `fail`, or `insufficient_evidence`. Passing is an offline recommendation, not proof of causal business improvement or automatic deployment.

Evaluation artifacts:

```text
evaluation-manifest.json
verdicts.jsonl
report.json
report.md
```

Comparison artifacts include compatible run/evaluation identities, configured gates, paired case counts, deltas/intervals, regression/fix case IDs, and final status.

## 16. Export and integration boundaries

Initial export is a directory containing `cases.jsonl`, definitions, and `manifest.json`. Use sidecar metadata rather than inserting a non-case header into generic JSONL datasets. Export records include field evidence and feedback according to eligibility and split policy.

Input-only exports contain no references. Training exports exclude Weak/Unknown targets by default and preserve applicable component eligibility. Test export requires explicit protected-split acknowledgment. No platform-native claim is made for plain JSONL output.

Adapters should take caller-created clients where needed; credentials and connection state stay outside the data model. A source adapter maps production semantics into DTF and declares any unknown exposure, attribution, or timing. Never default missing exposure to shown without producer evidence.

The first subsequent optimizer adapter should support maintained DSPy/GEPA interfaces after API/version verification. It must preserve case identities, approved objectives, budgets, training feedback, and holdout separation. Reuse the optimizer; do not implement search logic here.

SFT/DPO is deferred: edits and overrides are not automatically valid chosen/rejected preference pairs. Temporal is an external execution option; it consumes the same cases/scorers and uses activities for model calls. It is not an initial production deployment deliverable.

## 17. Bias and OPE limits

The initial release reports exposure, sampling, propensity completeness, censoring, attribution gaps, and evidence coverage. It does not estimate IPW/DR or claim that tiering removes bias.

Future OPE requires a defined intervention and reward, matching behavior and target probabilities, support/overlap, justified missingness and attribution assumptions, and an explicit treatment of downstream human response. A randomized suggestion can support evaluation of that suggestion intervention under assumptions; it does not identify a policy directly selecting the human's executed action.

Missing/invalid propensities, unsupported actions, undefined rewards, or unmodeled sampling block estimates. No propensity imputation or silent clipping. A future estimator reports weight distributions, effective sample size, uncertainty, and clipping sensitivity if enabled. Doubly robust estimation additionally requires leakage-safe reward-model fitting.

Suggestion-hidden experiments and stochastic production policies belong to the application team and require its operational decisions. This repo does not introduce them automatically or prescribe a default traffic percentage.

## 18. Operational and data requirements

- Synthetic data only in committed examples; ignore real data, local artifacts, caches, and secrets.
- Explicit redaction/pseudonymization before artifact persistence; record policy identity without recording secrets. Free-text redaction is not assumed complete.
- Diagnostics/logs identify fields and reasons without raw customer text. Detailed review artifacts are explicitly generated under the data policy.
- No external judge transmission without a configured provider and allowed field projection.
- Atomic final artifact writes; partial/resumable runs clearly marked incomplete.
- Deterministic compilation and scoring given fixed judge responses; live inference itself is not promised deterministic.
- Trusted callable imports are intentional user code execution. Trace content, event reasons, and YAML expressions are never executed.
- Exit codes: 0 successful operation/passed gate; 1 failed gate; 2 invalid input/configuration or processing failure; 3 insufficient evidence for a requested gate.

## 19. Implementation sequence and acceptance

### M1 — Contracts and compiler

Deliver DTF/events, generic task schemas, evidence adjudication, compilation, provenance, split rules, and immutable manifests.

Acceptance: fixtures cover missing/partial human actions, edits/overrides, delayed outcomes, conflicting IDs, ambiguous attribution, immature windows, negative-only evidence, weak acceptance, unknown exposure, future context, cross-split groups/duplicates, and evidence observed too late for training. Repeat compilation produces identical semantic artifacts. A second task demonstrates generic field handling.

### M2 — Deterministic scoring and comparison

Deliver reusable scorers, explicit composition, coverage, paired comparisons, reports, and gates.

Acceptance: hand-calculated fixtures verify cost orientation, acceptable sets, empty/missing sets, Unicode distance, applicability, invalid outputs, and timezone rules. Missing predictions cannot improve a candidate. Small cohorts yield insufficient evidence. A deliberately worse candidate fails configured gates.

### M3 — Runnable evaluations

Deliver callable runner, versioned prediction artifacts, cache/resume behavior, budgets, logged-baseline mode, and external prediction support.

Acceptance: supplied baseline/candidate functions run end to end; candidate code never receives references; changed code/model/config invalidates cache identity; failures/retries/budget exhaustion remain visible; rerunning evaluation reuses saved predictions without candidate calls.

### M4 — Judges and calibration

Deliver fixture judge, one optional live adapter, rubric definitions, caching, calibration, and experimental versus approved modes.

Acceptance: malformed judge responses never pass; calibration rejects missing classes; changed judge configuration invalidates approval; uncalibrated results cannot drive promotion gates; fixed judge responses yield identical scores through the CLI and direct library calls. CI uses fixtures/mocks; an explicit opt-in live smoke test exercises the provider adapter.

### M5 — Complete first-release demo and review

Deliver correction review/import, JSONL export, documentation, pytest integration example, and the complete synthetic Health Tech workflow.

Acceptance: the documented commands work after dependency installation without credentials using fixtures. The demo shows a logged baseline, a newly executed candidate, deterministic and rubric scores, a known regression, a delayed-evidence dataset revision, and a reproducible comparison. Export preserves definitions/provenance and excludes protected target fields from input-only views. Schema consistency, meaningful unit/integration tests, and lint checks pass.

M1–M5 collectively define the first release. A deterministic compiler alone is not completion of this specification.

### Subsequent milestones

Validate with an authorized Health Tech export, implement one optimizer adapter, then prioritize assisted rubric mining, connectors, playbooks, Temporal examples, and OPE from demonstrated needs.

## 20. Pilot inputs and unresolved choices

A real Health Tech pilot requires the application callable/interface, historical export mapping, action/follow-up vocabulary, decision-time input guarantees, event attribution, outcome windows, channel rules, confusion costs, slices, and regression thresholds. Its Temporal migration and Docker import path must be confirmed against actual code rather than assumed from the source spec.

The first live judge provider, production scale/performance targets, license, and publication names remain implementation/pilot decisions. A provider-neutral judge contract and credential-free fixtures allow development before production credentials exist.

## 21. Definition of done

A reviewer can compile evidence-backed cases, run an actual supplied decision function against input-only cases, score its outputs with deterministic metrics and configured rubric judges, inspect label provenance and judge calibration, compare it with a baseline without omission or split leakage, and export eligible cases for other tools. The workflow works locally with fixtures and can use an explicitly configured live candidate and judge. Reports distinguish behavioral agreement, semantic rubric judgments, and observed outcomes without presenting them as interchangeable proof of correctness.
