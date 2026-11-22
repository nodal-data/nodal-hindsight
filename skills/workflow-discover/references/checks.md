# The ten checks

Each check is reported as `present`, `partial` or `missing`, with its evidence
and who settled it: `probe`, `user` (an answer from step 3) or `unresolved`.
`scripts/readiness.py build` applies the rules below from the profile; this
file tells you what each check needs and which questions resolve ambiguity.

Coverage thresholds, unless a check says otherwise: at least 95% is `present`,
at least 50% is `partial`, below that is `missing`. A field left `null` in the
profile was not measured, so its check is `missing` and `unresolved`. A field
set to `0` was measured and is absent, so its check is `missing` by `probe`.

Field names in the right-hand column are the Hindsight record fields this
check feeds.

| # | Check ID | Hindsight fields |
|---|---|---|
| 1 | `suggestion_separate` | Decision `suggestion` |
| 2 | `suggestion_not_overwritten` | Decision `suggestion` versus HumanAction `output` |
| 3 | `exposure_known` | Decision `exposure`: `shown`, `hidden`, `unknown` |
| 4 | `timestamps` | Decision `decided_at`; event `event_at`, `observed_at` |
| 5 | `reviewer_id` | HumanAction `actor` |
| 6 | `policy_identity` | Decision `policy`: `model`, `prompt_version`, `code_version` |
| 7 | `frozen_context` | Decision `context`, `context_provenance` |
| 8 | `outcome_join` | Outcome `decision_id`, `observed_at` |
| 9 | `volume_accept_rate` | HumanAction `kind` |
| 10 | `request_type_signal` | Decision `context`, `tags` |

## 1. `suggestion_separate`

Is the AI suggestion stored in a field of its own, distinct from the final
human output? Without it there is no edit or override signal.

- Profile: `decisions.suggestion_coverage`.
- Set coverage to `0` when no separate suggestion field exists anywhere.
- Ask when two fields could be the suggestion (for example `draft_body` and
  `ai_response`): present both with their coverage and let the user choose.

## 2. `suggestion_not_overwritten`

Is the stored suggestion still the text the reviewer saw, or did the
reviewer's edit (or a later regeneration) replace it?

- Profile: `decisions.suggestion_equals_final_rate_when_edited`,
  `decisions.suggestion_modified_after_action_rate`,
  `decisions.suggestion_equals_final_rate`.
- Direct evidence: among decisions the reviewer edited or overrode, the share
  whose stored suggestion is identical to the final output; and the share of
  suggestions modified after the first human action. At most 2% is `present`,
  at most 20% is `partial`, above that is `missing`. The worse of the two
  wins.
- **Ambiguous** when only the overall equality rate is known. Equality cannot
  tell acceptance from overwrite. The check is `partial` and `unresolved`
  until answered. Ask with the number in the question:

  > `ai_drafts.body` equals `messages.body` in 81% of rows. Is `ai_drafts`
  > (a) the suggestion as the reviewer saw it, (b) a later regeneration or a
  > copy updated after the edit, or (c) unknown?

  | Answer | Status |
  |---|---|
  | (a) the suggestion as shown | `present` |
  | (b) regenerated or updated after the edit | `missing` |
  | (c) unknown | `partial` |

## 3. `exposure_known`

Did the reviewer see the suggestion before acting? This decides whether a
human action is Weak or Silver evidence.

- Profile: `decisions.exposure_counts` with `shown`, `hidden`, `unknown`.
- Status comes from the share recorded as `shown` or `hidden`.
- With no exposure field, do not assume `shown`. Ask the one fact that could
  settle it: "In this workflow, is the AI draft always on screen when the
  reviewer opens the item, sometimes hidden (for example in a blind-review
  sample), or do you not know?"

  | Answer | Status |
  |---|---|
  | Always shown, by product design, for the whole window | `present` (record `shown` for every decision) |
  | Sometimes hidden and it is logged somewhere | re-probe that field |
  | Sometimes hidden and not logged, or unknown | `missing` |

## 4. `timestamps`

- Profile: `decisions.decided_at_coverage`, `actions.event_at_coverage`,
  `actions.observed_at_coverage`, `outcomes.observed_at_coverage`.
- `present` when all four are at least 95%. `missing` when `decided_at` is
  below 50%. Otherwise `partial`.
- `event_at` is when something happened; `observed_at` is when the system
  recorded it. Many systems store one timestamp. Ask which it is only when a
  table has a single candidate column: "Is `updated_at` when the reviewer
  acted, or when the row was last written?" If it is a write time that later
  updates overwrite, leave `event_at_coverage` at `0`.

## 5. `reviewer_id`

- Profile: `actions.actor_coverage`, `actions.distinct_actors`.
- Probes cannot show that an ID is stable. If the field looks like a name, a
  shared login or a queue, ask: "Is `reviewed_by` one person per value and
  the same value for that person over time, a shared or team login, or
  unknown?"

  | Answer | Status |
  |---|---|
  | One stable ID per person | keep the probe status |
  | Shared login, queue, or IDs that are reissued | `partial` |
  | Unknown | `partial` |

## 6. `policy_identity`

- Profile: `decisions.policy_model_coverage`,
  `decisions.policy_prompt_version_coverage`,
  `decisions.policy_code_version_coverage`.
- `present` when all three are at least 95%; `partial` when at least one is at
  least 50%; otherwise `missing`.
- When nothing is stored per decision, ask whether it is recoverable another
  way: "Can the model, prompt version and code version in use on a given date
  be recovered from (a) a deploy log or release history, (b) nowhere, or
  (c) unknown?" (a) is `partial`; (b) and (c) are `missing`.

## 7. `frozen_context`

Can the input the AI saw be recovered as it was at decision time? Replay with
`hindsight run` needs it.

- Profile: `decisions.context_coverage`, `decisions.context_snapshot_coverage`
  (a snapshot time at or before the decision).
- `present` when both are at least 95%. `partial` when context is stored on at
  least 50% of decisions but its as-of time is not shown. Otherwise `missing`.
- If the context is a join to live tables rather than a stored copy, ask: "Is
  the input stored with the decision as it was at that moment, or looked up
  from tables that have changed since?" Looked up from mutable tables is
  `missing`; looked up from tables with full history is `partial`.

## 8. `outcome_join`

- Profile: `outcomes.decisions_with_outcome_rate`, `outcomes.lag_days_p50`,
  `outcomes.lag_days_p90`.
- At least 80% of decisions joined is `present`, at least 20% is `partial`,
  below that is `missing`. A p90 lag over 90 days caps the status at
  `partial`.
- Recent decisions have not had time to produce an outcome. Measure coverage
  on decisions older than the p90 lag, and say so in the summary.
- Ask when more than one event could be the outcome (a reply, a claim result,
  a reopened ticket): list the candidates with their join coverage and let the
  user choose which one says the decision was right or wrong.
- Low outcome coverage does not block mining Decision and HumanAction records.
  It marks the Outcome record type `partial` or `not-minable`.

## 9. `volume_accept_rate`

- Profile: `decisions.weekly_volume_median`, `decisions.weeks_observed`,
  `actions.accept_unchanged_rate`, `actions.accept_unchanged_basis`,
  `actions.kind_counts`, `actions.reviewers`.
- `present` when both volume and accept rate are known, `partial` with one.
- HumanAction `kind` is one of `accepted`, `edited`, `overridden`,
  `independent`, `rejected`, `escalated`, `none`, `unknown`. When the source
  has its own action values, list them (status-value rule) and ask the user to
  map any that are not obvious, as a choice per value.
- An accept-unchanged rate of 95% or more is flagged as rubber-stamp risk. It
  is never reported as accuracy.

## 10. `request_type_signal`

Can request classes be derived, so a router can be certified class by class?

- Profile: `decisions.request_content_coverage`, `decisions.category_field`,
  `decisions.category_coverage`, `decisions.category_cardinality`,
  `decisions.category_values`.
- `present` when request content is on at least 95% of decisions and an
  existing category field covers at least 80% with 2 to 50 values. `partial`
  when content or a category is present on at least 50% but classes are not
  directly usable, which means taxonomy work is needed. Otherwise `missing`.
- Candidate fields are queue, category, tag, intent, topic, reason code. Ask
  when more than one exists: present each with its cardinality and coverage.
- Do not read request content to judge whether classes look sensible. That is
  taxonomy work for a later step with its own consent.

## Record types and the verdict

`build` rolls the checks up per record type, then to one verdict.

| Record type | `not-minable` when | `partial` when | Reported but not blocking |
|---|---|---|---|
| Decision | check 1 or 2 is `missing`, or `decided_at` is under 50% | check 1 or 2 is `partial`, or `decided_at` is under 95% | checks 6, 7, 10 |
| HumanAction | no human action joins to a decision, or action `event_at` is under 50% | action `event_at` is under 95%, or check 3 or 5 is not `present` | |
| Outcome | check 8 is `missing` | check 8 is `partial` | |

| Verdict | Rule |
|---|---|
| `log-forward-only` | Decision is `not-minable` |
| `mine-history` | Decision and HumanAction are both `minable` |
| `partial` | anything else |

The Outcome record type is reported next to the verdict and does not change
it.
