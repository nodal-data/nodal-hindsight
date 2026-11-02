# Nodal Hindsight: project context and handoff

Last updated: 2026-11-02.

## Purpose

Hindsight compiles logged AI suggestions, human decisions, and subsequent outcomes
into evidence-backed evaluation cases. It can run a new decision function, score
saved predictions, and compare a candidate against a baseline. Read `README.md`
for the workflow and `spec.md` for the first-release specification. Health Tech
examples are synthetic and do not establish production accuracy or approved policies.

## Where the conversation left off

The user was clarifying the meaning of `hindsight run`. The agreed interpretation
is a fresh experiment against saved inputs:

- `hindsight run` invokes a configured Python `module:function` on the frozen
  input of each case in a compiled dataset split (`dev` by default).
- Previous LLM outputs, human corrections, reference targets, and later outcomes
  are not passed to the candidate through the runner API. They can inform the
  subsequent evaluation; they are not discarded from the dataset.
- The candidate may call an LLM, an agent, or other application logic. Hindsight
  does not itself require a new LLM call for every case.
- Optional caching and `--resume` can reuse predictions, so "fresh experiment"
  does not necessarily mean every application call executes again.
- Scoring is separate: use `hindsight evaluate`, or add `--evaluate` to `run`.
  The latter writes a sibling output directory ending in `-eval`.
- To score the original logged suggestions, use
  `hindsight evaluate --source logged --split dev` with dataset/output arguments.
- Candidate code is trusted, not sandboxed. Withholding targets is an API
  boundary, not a guarantee that candidate code cannot read local files.

No feature change or rename was requested. The latest request was to preserve
this context for a future session; no implementation task is pending from this
conversation. Do not assume previous test or demo artifacts prove current checks pass.

## Code map

- `src/nodal_hindsight/cli.py`: CLI options and command wiring.
- `src/nodal_hindsight/runners/__init__.py`: candidate execution, caching,
  checkpoints, and run artifacts.
- `src/nodal_hindsight/datasets/`: compilation and export.
- `src/nodal_hindsight/evaluation/`: prediction scoring and comparisons.
- `src/nodal_hindsight/schemas/`: public data contracts.
- `examples/health_tech/candidates.py`: deterministic baseline and intentionally
  regressing candidate, with no network calls.
- `examples/health_tech/baseline.yaml`: example execution configuration.
- `tests/`: automated tests; `scripts/demo.py`: end-to-end demonstration.

## Commands

Python 3.11+ and uv are required.

```bash
uv sync --locked --extra dev
uv run hindsight --help
uv run pytest
uv run ruff check .
```

Given a compiled dataset:

```bash
uv run hindsight run \
  --dataset artifacts/dataset \
  --split dev \
  --candidate examples.health_tech.candidates:baseline \
  --run-config examples/health_tech/baseline.yaml \
  --out artifacts/baseline-run

uv run hindsight evaluate \
  --dataset artifacts/dataset \
  --run artifacts/baseline-run \
  --scoring-config examples/health_tech/scoring.yaml \
  --out artifacts/baseline-eval
```

Run artifacts include `predictions.jsonl`, `errors.jsonl`, `run-manifest.json`,
and `checkpoint.json`. Use `--resume` for compatible completed cases;
`--overwrite` explicitly replaces an artifact directory.

For the complete synthetic workflow, run `uv run python scripts/demo.py`.
An existing destination requires `--overwrite`. The demo intentionally produces
a failed comparison and insufficient calibration evidence, handles those expected
results, and should finish successfully.

## Guardrails for future work

- Keep candidate execution, evaluation, and comparison responsibilities clear.
- Preserve the input-only candidate interface and evidence/time/split boundaries.
- Test-split run, evaluation, and export require `--allow-test`; do not use an
  inspected test set for iterative candidate selection.
- Keep live model calls opt-in. Example candidates and fixture judges are
  deterministic; do not treat their output as evidence of model quality.
- Run checks appropriate to code changes and report actual results. This handoff
  is documentation only; tests were not run when it was written.
