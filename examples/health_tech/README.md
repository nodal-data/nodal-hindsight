# Synthetic Health Tech workflow examples

These files illustrate the contracts in [the current spec](../../spec.md). They contain no real Health Tech data. Action names, rules, costs, and the seven-day outcome window are illustrative, not approved production policy.

| File | Contents |
|---|---|
| `decisions.jsonl` | Nine frozen inputs, logged model outputs, policy identities, and exposure states |
| `events.jsonl` | Eight responder decisions and four downstream outcome events; join by `decision_id` and verify `group_id` |
| `tasks.jsonl` | One definition of `health_tech.next_action`, including input/output schemas, field roles, evidence policy, splits, metrics, and an experimental rubric |

Here, a **task** means an evaluation task definition. Responder activities belong in `events.jsonl`, not `tasks.jsonl`. `follow_ups` contains a complete set of **planned** follow-ups when present; it does not assert those activities were later performed. An absent field means unknown; `[]` is an observed empty set; `message: null` means no outbound text for that action.

The JSONL files contain one JSON object per line, without header records. For readable inspection, for example:

```bash
# Browse records with headings (press q to leave the pager).
python3 scripts/view_jsonl.py examples/health_tech/decisions.jsonl | less

# View a single record, or all three files.
python3 scripts/view_jsonl.py examples/health_tech/decisions.jsonl --record 2
python3 scripts/view_jsonl.py examples/health_tech/*.jsonl | less
```

The CLI now loads this catalog directly via `--config examples/health_tech/tasks.jsonl`, or through `task.yaml`, which points to that same file. Run `uv run python scripts/demo.py` from the repository root for the complete workflow. See [the repository README](../../README.md) for live judges, callable adapters, calibration, and comparison gates.

## Scenarios

Use observation cutoff `2026-10-01T00:00:00Z`, training boundary September 1, and dev boundary September 15. Evidence is also bounded by the end of its partition.

| Decision suffix | Split | Evidence and intended interpretation |
|---|---|---|
| 001 | train | Accepted unchanged, no outcome: Weak accepted behavior, not trusted correctness |
| 002 | train | Responder removes an unsupported appointment confirmation and Markdown: Bronze message reference; unchanged action remains Weak |
| 003 | train | Suggested text overridden with a call; explicitly linked resolution and mature window support the executed action under the example policy, not the discarded draft |
| 004 | dev | Suggested closure overridden with escalation: Silver action reference; no outcome needed to observe the override |
| 005 | dev | Suggestion hidden; human independently chooses to wait: Silver action evidence, not automatically Gold or unbiased |
| 006 | test | Accepted email followed by a complaint: negative evidence for review, not an automatic known-bad action or invented alternative |
| 007 | test | Accepted SMS followed by an explicitly linked reply: outcome-supported action after maturity; message is not automatically promoted |
| 008 | test | Unknown exposure, no responder event, and only three days before cutoff: Unknown references and immature outcome window |
| 009 | train | Action override, reply on August 27, but reply only observed September 3: Silver action in this training partition; the later-observed reply cannot upgrade its label |

Unchanged accepted fields remain Weak unless qualifying evidence supports that field. Complete follow-up sets changed or independently supplied by the responder can be Bronze references; copied accepted sets are not independently confirmed. Changing the global observation cutoff alone must not allow September evidence into a training partition ending September 1.

No propensity metadata is invented. The hidden case is fixture-authored, not evidence of a real randomized holdout. Synthetic model IDs are placeholders; no provider calls generated these records.

## Scoring choices

The task includes categorical agreement and asymmetric costs, follow-up set F1, and message edit distance. Its message-grounding rubric is draft/uncalibrated, so results would be experimental and cannot drive promotion. Overall score composition and promotion gates are disabled. The tiny dataset illustrates shapes and edge cases, not statistical improvement or calibration.

The cost matrix uses reference rows and predicted columns, with zero diagonal. False closure costs 5; missed escalation costs 4 except false closure still costs 5; other disagreements cost 1. These values require real operational review before use.

Input-only candidate calls must receive only `context`. Human outputs, outcomes, scenario notes, rubric references, and evidence labels must not be added to candidate inputs. `events.jsonl` is ordered by observation time to make delayed knowledge explicit.


## Executable demonstration

`baseline.yaml` and `candidate.yaml` configure local synthetic callables in `candidates.py`. The candidate intentionally closes an unresolved request, so the demo comparison fails. `scoring.yaml` uses explicit canned judge responses; it does not perform semantic evaluation. The calibration fixtures contain just one positive and one negative label, deliberately below the configured minimum. `gates.yaml` supplies demonstration comparison thresholds independently of the task catalog's disabled production promotion policy. These are not approved production thresholds.
