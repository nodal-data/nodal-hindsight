# Source: file exports

Use this when the workflow history is in CSV, TSV or JSONL files on this
machine. `scripts/readiness.py` reads the files and computes every aggregate
locally. Row content never enters the conversation, so do not open the files
any other way. The one exception is the `sample` command, which needs the
user's consent first: see `references/content-samples.md`.

Commands below write `<skill-dir>` for the directory holding `SKILL.md`.

## 1. Describe the columns

```bash
python3 <skill-dir>/scripts/readiness.py list-columns decisions.csv actions.jsonl
```

For each column this prints the fill rate, the number of distinct values, the
longest value's length and what the values look like (`timestamp`, `number`,
`boolean`, `text`, `object`). It prints no values. JSONL keys are flattened to
dotted paths two levels deep, for example `policy.model`.

To see the values of a status column, name it with `--values`:

```bash
python3 <skill-dir>/scripts/readiness.py list-columns actions.jsonl --values action
```

Only pass a column the user has named as a category, action-kind or exposure
field. Never pass a reviewer, patient, message or note column. The script
refuses a column with more than 50 distinct values, values longer than 64
characters, or values that rarely repeat.

## 2. Write the column map

Propose a map from the column names and shapes. Where two columns could fill
the same role, ask the user to choose between them (see `SKILL.md`, step 4).
Leave out any role with no column; its check is then reported as not measured.

```json
{
  "decisions": {
    "id": "id",
    "decided_at": "created_at",
    "suggestion": "ai_draft",
    "suggestion_updated_at": "draft_updated_at",
    "exposure": "shown",
    "exposure_values": {"shown": ["Y"], "hidden": ["N"]},
    "model": "model",
    "prompt_version": "prompt_v",
    "code_version": "code_v",
    "context": "input_json",
    "context_snapshot_at": "snapshot_at",
    "request_content": "message_body",
    "category": "queue"
  },
  "actions": {
    "decision_id": "decision_id",
    "event_at": "at",
    "observed_at": "seen_at",
    "actor": "reviewer",
    "kind": "action",
    "kind_values": {"accepted": ["approve"], "edited": ["edit"], "overridden": ["override"]},
    "output": "final_text"
  },
  "outcomes": {
    "decision_id": "decision_id",
    "event_at": "happened_at",
    "observed_at": "recorded_at"
  }
}
```

| Table | Key | Meaning |
|---|---|---|
| decisions | `id` (required) | Decision identifier; the join key for the other two tables |
| decisions | `decided_at` | When the AI produced the suggestion |
| decisions | `suggestion` | The AI suggestion, stored apart from the final output |
| decisions | `suggestion_updated_at` | Last-modified time of the stored suggestion |
| decisions | `exposure`, `exposure_values` | Whether the reviewer saw the suggestion; map raw values to `shown` and `hidden`. Unmapped values count as `unknown` |
| decisions | `model`, `prompt_version`, `code_version` | Policy identity |
| decisions | `context`, `context_snapshot_at` | The input the AI saw, and when it was captured |
| decisions | `request_content` | The request text. Only its presence is measured |
| decisions | `category` | An existing queue, category, tag or intent field |
| actions | `decision_id` (required) | Join key to decisions |
| actions | `event_at`, `observed_at` | When the reviewer acted; when the system recorded it |
| actions | `actor` | Reviewer ID. Reported as ordinals only |
| actions | `kind`, `kind_values` | The reviewer's action; map raw values to `accepted`, `edited`, `overridden`, `independent`, `rejected`, `escalated`, `none`. Unmapped values count as `unknown` |
| actions | `output` | The final human output, compared with the suggestion by fingerprint |
| outcomes | `decision_id` (required) | Join key to decisions |
| outcomes | `event_at`, `observed_at` | When the outcome happened; when it was recorded |

Every table also accepts `where`, an equality filter for files that mix record
types: `"where": {"record_type": "human_action"}`.

Column names may be dotted paths into JSONL objects (`policy.model`).

Shapes this handles:

- **One wide file.** Pass the same file as `--decisions` and `--actions` (and
  `--outcomes`). A row counts as a human action only when one of its mapped
  action columns is filled.
- **Several actions per decision.** The latest by `event_at` is the final
  action. The earliest is used to test whether the suggestion was modified
  after the reviewer first acted.
- **No `kind` column.** Accept-unchanged is then measured by comparing the
  suggestion with the final output, and reported as `text-equality`.

Text is compared after collapsing whitespace. Timestamps may be ISO 8601,
`YYYY-MM-DD HH:MM:SS`, `MM/DD/YYYY`, or epoch seconds or milliseconds; values
with no zone are read as UTC.

## 3. Profile

```bash
python3 <skill-dir>/scripts/readiness.py profile-files \
  --decisions decisions.csv --actions actions.jsonl --outcomes outcomes.jsonl \
  --columns columns.json --workflow <workflow> \
  --out hindsight-discovery/<workflow>/profile.json
```

`--columns` takes a file path or inline JSON. `--actions` and `--outcomes` are
optional. The profile holds counts, rates, column names, category values that
pass the status-value test, and reviewer ordinals.

Read the profile, not the files, to decide which questions to ask. Add the
answers to the profile's `answers` list, then run `build` (see `SKILL.md`,
step 5).

## Limits

- Files are read in one pass and joined in memory by decision ID. Exports of a
  few million rows are fine; for more, profile a recent date range.
- A decision ID repeated in the decisions file keeps its last row; the share
  of repeats is reported.
- A mapped column that does not exist in a CSV header is an error. In JSONL a
  missing key simply reports zero coverage.
