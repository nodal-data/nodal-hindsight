# Profile and readiness formats

Three files are written to `hindsight-discovery/<workflow>/`:

| File | Written by | Purpose |
|---|---|---|
| `profile.json` | `profile-files`, or you from warehouse probes | Aggregate measurements and the user's answers |
| `readiness.json` | `build` | Checks, record-type statuses, verdict, volume, label estimate |
| `readiness.md` | `build` | The same, for a person to read |

None of them holds row content, including when the user has consented to
sample rows being viewed in the session.

## `profile.json`

Start one with `python3 <skill-dir>/scripts/readiness.py template --workflow
<workflow>`. `build` rejects unknown keys, so a misspelt field is caught
rather than ignored.

`null` means not measured. `0` means measured and absent. The difference
matters: a `null` check is reported as `unresolved`, a `0` as `missing`.

Rates are fractions from 0 to 1.

### Top level

| Field | Meaning |
|---|---|
| `profile_version` | `"0.1"` |
| `workflow` | Lowercase slug, for example `prior-auth-review` |
| `as_of` | `YYYY-MM-DD`, the day the probes ran |
| `sources` | One entry per source: `type` (`repository`, `warehouse`, `files`, `unsupported`), `location` (repository, account and database, or a directory), `objects` (code files, tables or export files), optional `note` |
| `content_access` | `consent` (`aggregates-only`, the default, or `samples-approved`) and `rows_viewed` (sample rows viewed in the session, at most 20; must be `0` without consent). See `references/content-samples.md` |
| `outcome_signals` | What later shows a decision was right or wrong, most trusted first; `null` if not discussed, `[]` if none exists. Each entry: `kind` (`user-response`, `downstream-status`, `later-review`, `business-result`), `description`, `location`, optional `join_key`, `arrives` (`every-decision`, `only-on-problems`, `some-decisions`, `unknown`), optional `typical_delay`, optional `source` (`user` or `code`). See `references/outcomes.md` |
| `answers` | One entry per settled question: `check` (a check ID), `question`, `answer` (the option chosen, or the code finding with its file), `status` (`present`, `partial`, `missing`), optional `source` (`user`, the default, or `code`). At most one per check; it overrides the probe status |

### `decisions`

| Field | Meaning |
|---|---|
| `rows` | Decision rows in the window |
| `duplicate_id_rate` | Share of rows that repeat a decision ID |
| `suggestion_coverage` | Share with a non-empty suggestion stored apart from the final output |
| `suggestion_equals_final_rate` | Among decisions with both a suggestion and a final output, the share where they are identical |
| `suggestion_equals_final_rate_when_edited` | The same, restricted to decisions whose final action is `edited` or `overridden` |
| `suggestion_modified_after_action_rate` | Among decisions with a suggestion modified-time and a human action, the share modified after the first action |
| `exposure_counts` | `{"shown": n, "hidden": n, "unknown": n}` |
| `decided_at_coverage` | Share with a decision timestamp |
| `policy_model_coverage`, `policy_prompt_version_coverage`, `policy_code_version_coverage` | Share with each policy field |
| `context_coverage` | Share with the input context stored |
| `context_snapshot_coverage` | Share with a context snapshot time at or before the decision |
| `request_content_coverage` | Share with request content present |
| `category_field` | Name of the existing category field, if any |
| `category_coverage`, `category_cardinality` | Its fill rate and number of distinct values |
| `category_values` | `[{"value": "...", "count": n}]`, only when the field passes the status-value test; otherwise `null` |
| `category_values_suppressed` | Why the values are not listed, when they are not |
| `first_decided_at`, `last_decided_at` | Date range covered |
| `weeks_observed` | Weeks in that range |
| `weekly_volume_median` | Median decisions per complete week |

### `actions`

| Field | Meaning |
|---|---|
| `rows` | Human-action rows |
| `decisions_with_action_rate` | Share of decisions with at least one human action |
| `orphan_rate` | Share of action rows whose decision is not in the decisions set |
| `event_at_coverage`, `observed_at_coverage` | Share with each timestamp |
| `actor_coverage`, `distinct_actors` | Share with a reviewer ID; number of distinct reviewers |
| `kind_counts` | Decisions by final action kind: `accepted`, `edited`, `overridden`, `independent`, `rejected`, `escalated`, `none`, `unknown` |
| `accept_unchanged_rate` | Share of reviewed decisions accepted unchanged |
| `accept_unchanged_basis` | `kind` (from the action field) or `text-equality` (suggestion equals final output) |
| `reviewers` | Up to 20, most active first: `reviewer` (`reviewer_1`, never a real ID), `actions`, `accept_unchanged_rate`, `median_seconds_to_action` |

### `outcomes`

| Field | Meaning |
|---|---|
| `rows` | Outcome rows |
| `decisions_with_outcome_rate` | Share of decisions with at least one outcome |
| `orphan_rate` | Share of outcome rows whose decision is not in the decisions set |
| `event_at_coverage`, `observed_at_coverage` | Share with each timestamp |
| `lag_days_p50`, `lag_days_p90` | Days from decision to first observed outcome |

## `readiness.json`

Defined by `assets/readiness.schema.json` (`schema_version` `"0.1"`).
`python3 <skill-dir>/scripts/readiness.py check <file>` validates a file
against that schema, and also confirms the ten checks are all there and the
verdict follows from the record types.

| Field | Meaning |
|---|---|
| `schema_version` | `"0.1"` |
| `workflow`, `as_of`, `sources`, `content_access`, `outcome_signals` | Copied from the profile |
| `checks` | The ten checks in order: `id`, `status` (`present`, `partial`, `missing`), `evidence`, `answered_by` (`probe`, `user`, `code`, `unresolved`) |
| `record_types` | `decision`, `human_action`, `outcome`, each with `status` (`minable`, `partial`, `not-minable`) and `reasons` |
| `verdict` | `mine-history`, `partial` or `log-forward-only` |
| `volume` | Decision count, date range, weekly median, accept-unchanged rate and basis, `kind_counts`, `reviewers`, `classes` (the category field), `flags` |
| `label_estimate` | `basis`, `blind_sample_rate`, `weekly_volume`, `rows` (four error-rate bounds with labels needed and weeks), `per_class` |

## `readiness.md`

Sections, in order:

1. **Verdict**, with a table of the three record types and why.
2. **Sources.**
3. **Checks**: all ten with status, who settled it and the evidence; then any
   that are unresolved.
4. **AI suggestion versus human action**: decisions by action kind. Labelled
   "not evidence of correctness".
5. **Outcome**: why approvals are weak evidence, and the outcome signals
   agreed with the user (or that none exists yet).
6. **Rubber-stamp indicators by reviewer**: actions, accept-unchanged rate and
   median seconds to action per reviewer ordinal, plus flags.
7. **Volume and request types**: weekly volume and the category field.
8. **Labels needed.**
9. **What discovery cannot do.**

## The labels-needed table

A reference table, not a sample-size advisor. It answers: if a blind-reviewed
sample of `n` decisions shows zero errors, what error rate does that rule out
at one-sided 95% confidence? `n = ln(0.05) / ln(1 - p)`, rounded up.

| Error-rate bound | Labels needed |
|---|---|
| 5% | 59 |
| 2% | 149 |
| 1% | 299 |
| 0.5% | 598 |

Weeks to collect is labels needed divided by (weekly volume × blind-sample
rate), rounded up. The per-class rows use each class's share of weekly volume,
because certification is per class. Any observed error means more labels than
this table shows.
