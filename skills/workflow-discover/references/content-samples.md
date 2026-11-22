# Viewing sample rows, with consent

Discovery runs on aggregates by default. A small sample of row content is
allowed only after the user says yes to the consent question below. Without a
clear yes, stay on aggregates; the report is still complete.

## Why a question is needed

The user being allowed to query the data is not the issue. Anything you read
is sent to the AI provider behind this session and is kept in the session
history on this machine. For health data and other regulated content, the
company has to have approved that provider for it. The user knows whether it
has; you do not.

## When to ask

Ask once, the first time a sample would help, and not before. Aggregates and
the repository settle most checks. A sample helps when:

- two columns could be the suggestion, and their shapes do not tell them apart;
- a category field passed or failed the status-value test and you need to see
  whether its values are real request types;
- a status or exposure column holds values whose meaning is not clear from the
  code.

If none of these comes up, do not ask.

## The consent question

Ask it as a choice, in these terms:

> Seeing a few example rows would help me with [the specific ambiguity]. Rows
> I read are sent to [the AI provider behind this session] and stay in this
> session's history on this machine. Is this agent approved to read row
> content for this workflow?
>
> (a) Yes, it is approved.
> (b) No, or not sure. Stay on aggregates.

Rules:

- Only (a) is consent. "Not sure", "probably" or silence is (b).
- The answer holds for this workflow and this session only. Do not ask again,
  and do not carry a yes over to another workflow.
- On (a), set `content_access.consent` to `samples-approved` in the profile.
  On (b), leave it as `aggregates-only` and resolve the ambiguity with a
  choice question instead.
- The user can withdraw the yes at any time. Stop sampling when they do.

## Limits after a yes

- **Named columns only.** At most three columns per look, chosen for the
  ambiguity in hand. No `SELECT *`, no whole rows.
- **Never identifier columns.** Do not sample a reviewer, patient, member,
  name, email, phone or address column. Reviewers stay `reviewer_1`,
  `reviewer_2`.
- **At most 5 rows per look and 20 rows in the session.** Values are cut at
  300 characters.
- **Session only.** Never write a sampled value to `profile.json`, an answer,
  the report or any other file, and do not quote one back in the summary.
  Describe what you learned in your own words ("`reply_text` holds the final
  sent message, not the draft").
- **Count what you view.** After each look, add the rows returned to
  `content_access.rows_viewed` in the profile. The report states the total.
- **A sample does not settle a check.** Five rows show what a column holds;
  they do not show how complete the history is. Use a sample to pick which
  column to measure or to write better options for a question. Statuses still
  come from the measurements, the code or the user's answer.
- Everything else in the hard fences still applies: read-only, bounded by the
  date window, no credentials.

## Files

```bash
python3 <skill-dir>/scripts/readiness.py sample decisions.csv \
  --column ai_draft --column reply_text --rows 5 --consent-given
```

The script refuses without `--consent-given`, takes at most three columns and
five rows, spreads the rows across the file, and cuts each value at 300
characters. Pass `--consent-given` only after the user chose (a). Do not open
the file any other way, even after consent.

## Warehouse

Use this probe and no other shape. Replace `{{sample_column_1}}` and
`{{sample_column_2}}` with the columns in question (drop the second line for
one column), and the other placeholders as in
`references/source-warehouse.md`.

Snowflake:

```sql
SELECT
  LEFT({{sample_column_1}}::string, 300) AS sample_1,
  LEFT({{sample_column_2}}::string, 300) AS sample_2
FROM {{decisions}}
WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
  AND {{sample_column_1}} IS NOT NULL
ORDER BY {{decided_at}} DESC
LIMIT 5
```

BigQuery:

```sql
SELECT
  LEFT(CAST({{sample_column_1}} AS STRING), 300) AS sample_1,
  LEFT(CAST({{sample_column_2}} AS STRING), 300) AS sample_2
FROM {{decisions}}
WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
  AND {{sample_column_1}} IS NOT NULL
ORDER BY {{decided_at}} DESC
LIMIT 5
```

For a column in the actions or outcomes table, use that table and its
timestamp in place of `{{decisions}}` and `{{decided_at}}`.
