# The outcome: what later showed the decision was right or wrong

Use this at step 2, after tracing the code and before measuring anything.
The outcome is the record users most often have not thought about, and it is
often stored somewhere other than the suggestion and the review. Agree what
it is first, so the measurement step knows where to look.

## 1. Explain what you are looking for

Say this to the user in your own words, briefly, before asking anything:

- So far we have two records: what the AI suggested, and what the reviewer did
  with it.
- A reviewer's approval is weak evidence that the suggestion was right.
  People approve quickly, trust the draft, and make their own mistakes. An
  edit can be wrong too.
- What we want as well is the **outcome**: something that happened afterwards
  and shows whether the final decision held up. It is often days or weeks
  later, and often recorded in a different place.
- With outcomes, the history becomes a dataset that is safe to evaluate and
  train from. Without them, it only records what reviewers agreed with.

Give one or two examples that fit their workflow, taken from the table below.
Do not describe the accept rate as accuracy while explaining this.

## 2. Find the candidates

Start from what the code showed (`references/source-repo.md`, the outcome
row): a callback, a sync job, a status column that changes later, a reply
handler. Then offer the user a choice of the candidates you found, plus the
kinds below that you did not find, plus "nothing like this exists yet".

| Kind | What it is | Examples |
|---|---|---|
| `user-response` | The person on the receiving end reacts later | The customer replies again, reopens the ticket, complains, rates the answer, asks for a human |
| `downstream-status` | A later status in a system of record | A claim paid or denied, an appeal overturned, a refund issued, an order returned, a job that ran or failed |
| `later-review` | A second person checks the decision afterwards | A quality audit, a supervisor review, a sampled re-check, a compliance finding |
| `business-result` | A measured result tied to the item | The invoice was paid, the lead converted, the forecast matched the actual |

More than one can apply. Ask which one the user would trust most to say "that
decision was wrong"; list that one first.

## 3. Ask what you need about each one

One question at a time, as choices where there are distinct readings. Skip
anything the code already answered, and say what the code showed.

1. **Where is it stored?** A table and column, a file, another system. Name
   the store, not its contents. If it is in a system this session cannot
   reach, record it anyway; the report says it was not measured.
2. **What ties it back to the decision?** The decision ID, or another key
   (ticket, claim, order, conversation) that the decision also carries. No
   shared key means the outcome cannot be joined yet.
3. **When does it arrive?**
   - `every-decision`: each decision eventually gets one, good or bad.
   - `only-on-problems`: it is recorded only when something goes wrong (a
     complaint, a reopen, an appeal). Then a missing outcome is not a good
     outcome, only an absence of a recorded problem.
   - `some-decisions`: a sample or a subset gets one (an audit of 5%).
   - `unknown`.
4. **How long does it usually take?** The user's rough figure ("about two
   weeks", "up to 90 days"). The measured lag replaces it when there is one.

## 4. Record it

Put what you learned in the profile's `outcome_signals` list, most trusted
first:

```json
"outcome_signals": [
  {
    "kind": "downstream-status",
    "description": "Appeal result on the claim",
    "location": "claims.appeal_status in Snowflake",
    "join_key": "claim_id",
    "arrives": "only-on-problems",
    "typical_delay": "30 to 60 days",
    "source": "user"
  }
]
```

- Leave `outcome_signals` as `null` only if the step was skipped. Set it to
  `[]` when the user confirmed nothing like this exists yet.
- `description` and `location` are your words: a store and field name, never
  row content.
- `source` is `code` when the repository showed it and the user did not need
  to say, otherwise `user`.

## 5. What happens next

- **A signal this session can reach** (a warehouse table, an export): it is
  the outcome source for the measurement step. Run the outcome probes or map
  the outcomes file against it. Coverage, join rate and lag come from the
  data, not from this conversation.
- **A signal it cannot reach:** leave the outcome measurements `null`. The
  report lists the signal and says it was not measured.
- **No signal:** the Outcome record type is `not-minable`. That does not
  change the verdict for Decision and HumanAction. Tell the user what to
  start capturing: one event per decision, with the decision ID, what
  happened and when it was observed. A light option is a later second review
  of a small random sample.
- **`only-on-problems` signals** are still useful, but coverage will look
  low. Say so, and do not read the decisions without an outcome as correct.
