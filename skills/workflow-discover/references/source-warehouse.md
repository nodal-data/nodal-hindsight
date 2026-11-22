# Source: data warehouse

Use this when the workflow history is in Snowflake or BigQuery and this
session already has a way to query it (a warehouse MCP server or a CLI the
user has signed in to). Every statement here is one read-only aggregate
query. Run nothing else against the warehouse.

## 1. Probe the connection

Run one trivial read first:

```sql
SELECT 1 AS ok
```

If it fails on authentication, tell the user the sign-in step for their tool
and offer the file-export path meanwhile. Do not ask for a password, key or
token.

## 2. Find the tables and columns from metadata

Ask the user which schema or dataset holds the workflow, then read column
names and types from the catalog. This returns metadata, never row content.
None of the probes in this file returns row content; the only probe that does
is in `references/content-samples.md` and needs the user's consent first.

Snowflake:

```sql
SELECT table_name, column_name, data_type
FROM {{database}}.information_schema.columns
WHERE table_schema = '{{schema}}'
ORDER BY table_name, ordinal_position
LIMIT 2000
```

BigQuery:

```sql
SELECT table_name, column_name, data_type
FROM `{{project}}.{{dataset}}.INFORMATION_SCHEMA.COLUMNS`
ORDER BY table_name, ordinal_position
LIMIT 2000
```

From the names and types, propose which table holds decisions, which holds
human actions and which holds outcomes, and which column fills each role
below. Where two tables or columns could fill a role, ask the user to choose
(see `SKILL.md`, step 3). One wide table can serve as both decisions and
actions; use it for both placeholders.

## 3. Parameters

Replace each `{{placeholder}}` before running. When the source has no column
for a role, delete the lines that use it and leave that profile field `null`,
or set it to `0` when you have confirmed the field exists nowhere.

| Placeholder | Meaning |
|---|---|
| `{{decisions}}`, `{{actions}}`, `{{outcomes}}` | Fully qualified tables |
| `{{since}}`, `{{until}}` | Window bounds as timestamp literals. Default: the last 180 days. Use Mondays so no week is cut short |
| `{{d_id}}`, `{{decided_at}}` | Decision ID and decision time |
| `{{suggestion}}`, `{{suggestion_updated_at}}` | Stored AI suggestion and its last-modified time |
| `{{exposure}}`, `{{shown_values}}`, `{{hidden_values}}` | Exposure column and quoted value lists, for example `'Y'` and `'N'` |
| `{{model}}`, `{{prompt_version}}`, `{{code_version}}` | Policy identity columns |
| `{{context}}`, `{{context_snapshot_at}}` | Stored input context and its capture time |
| `{{request_content}}` | Request text. Only its presence is counted |
| `{{category}}` | Existing queue, category, tag or intent column |
| `{{a_decision_id}}`, `{{a_event_at}}`, `{{a_observed_at}}` | Action join key and timestamps |
| `{{a_actor}}`, `{{a_output}}` | Reviewer ID and final human output |
| `{{a_kind}}` | An expression giving the Hindsight kind, written from the user's mapping, for example `CASE action WHEN 'approve' THEN 'accepted' WHEN 'edit' THEN 'edited' ELSE 'unknown' END` |
| `{{o_decision_id}}`, `{{o_event_at}}`, `{{o_observed_at}}` | Outcome join key and timestamps |
| `{{status_column}}`, `{{status_table}}`, `{{status_time}}` | For the status-value probes below |

Result column names match the profile field names in
`references/readiness-format.md` unless a probe says otherwise.

## 4. Status-value probes

The values of a column may be listed only for a category, action-kind or
exposure column the user has named. Never for a reviewer, patient, message or
note column. First measure its shape:

Snowflake:

```sql
SELECT
  COUNT({{status_column}}) AS filled,
  COUNT(DISTINCT {{status_column}}) AS distinct_values,
  MAX(LENGTH({{status_column}}::string)) AS max_length
FROM {{status_table}}
WHERE {{status_time}} >= {{since}} AND {{status_time}} < {{until}}
```

BigQuery:

```sql
SELECT
  COUNT({{status_column}}) AS filled,
  COUNT(DISTINCT {{status_column}}) AS distinct_values,
  MAX(LENGTH(CAST({{status_column}} AS STRING))) AS max_length
FROM {{status_table}}
WHERE {{status_time}} >= {{since}} AND {{status_time}} < {{until}}
```

List the values only when `distinct_values` is at most 50, `max_length` is at
most 64, and `distinct_values` is at most half of `filled`. Otherwise report
the cardinality and stop. Both dialects:

```sql
SELECT {{status_column}} AS status_value, COUNT(*) AS decisions
FROM {{status_table}}
WHERE {{status_time}} >= {{since}} AND {{status_time}} < {{until}}
GROUP BY 1
ORDER BY 2 DESC
LIMIT 50
```

For the category column this result fills `decisions.category_values`
(`status_value` is `value`, `decisions` is `count`).

## 5. Decision probes

Fills the `decisions` section. `decision_rows` is `rows`; the three
`exposure_*` columns fill `exposure_counts`.

Snowflake:

```sql
SELECT
  COUNT(*) AS decision_rows,
  1 - COUNT(DISTINCT {{d_id}}) / NULLIF(COUNT(*), 0) AS duplicate_id_rate,
  COUNT_IF({{suggestion}} IS NOT NULL AND TRIM({{suggestion}}::string) <> '')
    / NULLIF(COUNT(*), 0) AS suggestion_coverage,
  COUNT_IF({{exposure}}::string IN ({{shown_values}})) AS exposure_shown,
  COUNT_IF({{exposure}}::string IN ({{hidden_values}})) AS exposure_hidden,
  COUNT_IF({{exposure}} IS NULL
    OR {{exposure}}::string NOT IN ({{shown_values}}, {{hidden_values}})) AS exposure_unknown,
  COUNT({{decided_at}}) / NULLIF(COUNT(*), 0) AS decided_at_coverage,
  COUNT({{model}}) / NULLIF(COUNT(*), 0) AS policy_model_coverage,
  COUNT({{prompt_version}}) / NULLIF(COUNT(*), 0) AS policy_prompt_version_coverage,
  COUNT({{code_version}}) / NULLIF(COUNT(*), 0) AS policy_code_version_coverage,
  COUNT({{context}}) / NULLIF(COUNT(*), 0) AS context_coverage,
  COUNT_IF({{context_snapshot_at}} <= {{decided_at}})
    / NULLIF(COUNT(*), 0) AS context_snapshot_coverage,
  COUNT({{request_content}}) / NULLIF(COUNT(*), 0) AS request_content_coverage,
  COUNT({{category}}) / NULLIF(COUNT(*), 0) AS category_coverage,
  COUNT(DISTINCT {{category}}) AS category_cardinality,
  MIN({{decided_at}})::date AS first_decided_at,
  MAX({{decided_at}})::date AS last_decided_at
FROM {{decisions}}
WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
```

BigQuery:

```sql
SELECT
  COUNT(*) AS decision_rows,
  1 - SAFE_DIVIDE(COUNT(DISTINCT {{d_id}}), COUNT(*)) AS duplicate_id_rate,
  SAFE_DIVIDE(
    COUNTIF({{suggestion}} IS NOT NULL AND TRIM(CAST({{suggestion}} AS STRING)) <> ''),
    COUNT(*)) AS suggestion_coverage,
  COUNTIF(CAST({{exposure}} AS STRING) IN ({{shown_values}})) AS exposure_shown,
  COUNTIF(CAST({{exposure}} AS STRING) IN ({{hidden_values}})) AS exposure_hidden,
  COUNTIF({{exposure}} IS NULL
    OR CAST({{exposure}} AS STRING) NOT IN ({{shown_values}}, {{hidden_values}})) AS exposure_unknown,
  SAFE_DIVIDE(COUNT({{decided_at}}), COUNT(*)) AS decided_at_coverage,
  SAFE_DIVIDE(COUNT({{model}}), COUNT(*)) AS policy_model_coverage,
  SAFE_DIVIDE(COUNT({{prompt_version}}), COUNT(*)) AS policy_prompt_version_coverage,
  SAFE_DIVIDE(COUNT({{code_version}}), COUNT(*)) AS policy_code_version_coverage,
  SAFE_DIVIDE(COUNT({{context}}), COUNT(*)) AS context_coverage,
  SAFE_DIVIDE(COUNTIF({{context_snapshot_at}} <= {{decided_at}}), COUNT(*)) AS context_snapshot_coverage,
  SAFE_DIVIDE(COUNT({{request_content}}), COUNT(*)) AS request_content_coverage,
  SAFE_DIVIDE(COUNT({{category}}), COUNT(*)) AS category_coverage,
  COUNT(DISTINCT {{category}}) AS category_cardinality,
  DATE(MIN({{decided_at}})) AS first_decided_at,
  DATE(MAX({{decided_at}})) AS last_decided_at
FROM {{decisions}}
WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
```

Weekly volume. Fills `weeks_observed` and `weekly_volume_median`.

Snowflake:

```sql
SELECT COUNT(*) AS weeks_observed, MEDIAN(decisions) AS weekly_volume_median
FROM (
  SELECT DATE_TRUNC('week', {{decided_at}}) AS week_start, COUNT(*) AS decisions
  FROM {{decisions}}
  WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
  GROUP BY 1
) AS weekly
```

BigQuery:

```sql
SELECT COUNT(*) AS weeks_observed, APPROX_QUANTILES(decisions, 2)[OFFSET(1)] AS weekly_volume_median
FROM (
  SELECT TIMESTAMP_TRUNC({{decided_at}}, WEEK(MONDAY)) AS week_start, COUNT(*) AS decisions
  FROM {{decisions}}
  WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
  GROUP BY 1
) AS weekly
```

## 6. Human-action probes

Coverage. Fills the `actions` section; `action_rows` is `rows`. Actions whose
decision falls just before `{{since}}` count as orphans, so a small
`orphan_rate` at the window edge is expected.

Snowflake:

```sql
SELECT
  COUNT(*) AS action_rows,
  COUNT(DISTINCT d.decision_id) / NULLIF(MAX(t.decisions), 0) AS decisions_with_action_rate,
  COUNT_IF(d.decision_id IS NULL) / NULLIF(COUNT(*), 0) AS orphan_rate,
  COUNT(a.{{a_event_at}}) / NULLIF(COUNT(*), 0) AS event_at_coverage,
  COUNT(a.{{a_observed_at}}) / NULLIF(COUNT(*), 0) AS observed_at_coverage,
  COUNT(a.{{a_actor}}) / NULLIF(COUNT(*), 0) AS actor_coverage,
  COUNT(DISTINCT a.{{a_actor}}) AS distinct_actors
FROM {{actions}} AS a
LEFT JOIN (
  SELECT DISTINCT {{d_id}} AS decision_id
  FROM {{decisions}}
  WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
) AS d ON a.{{a_decision_id}} = d.decision_id
CROSS JOIN (
  SELECT COUNT(DISTINCT {{d_id}}) AS decisions
  FROM {{decisions}}
  WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
) AS t
WHERE a.{{a_event_at}} >= {{since}}
```

BigQuery:

```sql
SELECT
  COUNT(*) AS action_rows,
  SAFE_DIVIDE(COUNT(DISTINCT d.decision_id), MAX(t.decisions)) AS decisions_with_action_rate,
  SAFE_DIVIDE(COUNTIF(d.decision_id IS NULL), COUNT(*)) AS orphan_rate,
  SAFE_DIVIDE(COUNT(a.{{a_event_at}}), COUNT(*)) AS event_at_coverage,
  SAFE_DIVIDE(COUNT(a.{{a_observed_at}}), COUNT(*)) AS observed_at_coverage,
  SAFE_DIVIDE(COUNT(a.{{a_actor}}), COUNT(*)) AS actor_coverage,
  COUNT(DISTINCT a.{{a_actor}}) AS distinct_actors
FROM {{actions}} AS a
LEFT JOIN (
  SELECT DISTINCT {{d_id}} AS decision_id
  FROM {{decisions}}
  WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
) AS d ON a.{{a_decision_id}} = d.decision_id
CROSS JOIN (
  SELECT COUNT(DISTINCT {{d_id}}) AS decisions
  FROM {{decisions}}
  WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
) AS t
WHERE a.{{a_event_at}} >= {{since}}
```

Suggestion versus final output. The latest action per decision is the final
one. Fills the three `decisions.suggestion_*` rates and
`actions.accept_unchanged_rate` (set `accept_unchanged_basis` to `kind`). The
comparison happens inside the warehouse; only the rates come back.

Snowflake:

```sql
WITH final_action AS (
  SELECT
    {{a_decision_id}} AS decision_id,
    {{a_kind}} AS kind,
    TRIM({{a_output}}::string) AS output
  FROM {{actions}}
  WHERE {{a_event_at}} >= {{since}}
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {{a_decision_id}} ORDER BY {{a_event_at}} DESC) = 1
),
first_action AS (
  SELECT {{a_decision_id}} AS decision_id, MIN({{a_event_at}}) AS first_event_at
  FROM {{actions}}
  WHERE {{a_event_at}} >= {{since}}
  GROUP BY 1
),
decision AS (
  SELECT
    {{d_id}} AS decision_id,
    TRIM({{suggestion}}::string) AS suggestion,
    {{suggestion_updated_at}} AS suggestion_updated_at
  FROM {{decisions}}
  WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
)
SELECT
  COUNT_IF(d.suggestion = f.output)
    / NULLIF(COUNT_IF(d.suggestion IS NOT NULL AND f.output IS NOT NULL), 0)
    AS suggestion_equals_final_rate,
  COUNT_IF(d.suggestion = f.output AND f.kind IN ('edited', 'overridden'))
    / NULLIF(COUNT_IF(d.suggestion IS NOT NULL AND f.output IS NOT NULL
        AND f.kind IN ('edited', 'overridden')), 0)
    AS suggestion_equals_final_rate_when_edited,
  COUNT_IF(d.suggestion_updated_at > s.first_event_at)
    / NULLIF(COUNT_IF(d.suggestion_updated_at IS NOT NULL AND s.first_event_at IS NOT NULL), 0)
    AS suggestion_modified_after_action_rate,
  COUNT_IF(f.kind = 'accepted') / NULLIF(COUNT(f.decision_id), 0) AS accept_unchanged_rate
FROM decision AS d
JOIN final_action AS f ON f.decision_id = d.decision_id
LEFT JOIN first_action AS s ON s.decision_id = d.decision_id
```

BigQuery:

```sql
WITH final_action AS (
  SELECT
    {{a_decision_id}} AS decision_id,
    {{a_kind}} AS kind,
    TRIM(CAST({{a_output}} AS STRING)) AS output
  FROM {{actions}}
  WHERE {{a_event_at}} >= {{since}}
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {{a_decision_id}} ORDER BY {{a_event_at}} DESC) = 1
),
first_action AS (
  SELECT {{a_decision_id}} AS decision_id, MIN({{a_event_at}}) AS first_event_at
  FROM {{actions}}
  WHERE {{a_event_at}} >= {{since}}
  GROUP BY 1
),
decision AS (
  SELECT
    {{d_id}} AS decision_id,
    TRIM(CAST({{suggestion}} AS STRING)) AS suggestion,
    {{suggestion_updated_at}} AS suggestion_updated_at
  FROM {{decisions}}
  WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
)
SELECT
  SAFE_DIVIDE(
    COUNTIF(d.suggestion = f.output),
    COUNTIF(d.suggestion IS NOT NULL AND f.output IS NOT NULL)) AS suggestion_equals_final_rate,
  SAFE_DIVIDE(
    COUNTIF(d.suggestion = f.output AND f.kind IN ('edited', 'overridden')),
    COUNTIF(d.suggestion IS NOT NULL AND f.output IS NOT NULL
      AND f.kind IN ('edited', 'overridden'))) AS suggestion_equals_final_rate_when_edited,
  SAFE_DIVIDE(
    COUNTIF(d.suggestion_updated_at > s.first_event_at),
    COUNTIF(d.suggestion_updated_at IS NOT NULL AND s.first_event_at IS NOT NULL))
    AS suggestion_modified_after_action_rate,
  SAFE_DIVIDE(COUNTIF(f.kind = 'accepted'), COUNT(f.decision_id)) AS accept_unchanged_rate
FROM decision AS d
JOIN final_action AS f ON f.decision_id = d.decision_id
LEFT JOIN first_action AS s ON s.decision_id = d.decision_id
```

Decisions by final action kind. Fills `actions.kind_counts`. Both dialects:

```sql
WITH final_action AS (
  SELECT {{a_decision_id}} AS decision_id, {{a_kind}} AS kind
  FROM {{actions}}
  WHERE {{a_event_at}} >= {{since}}
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {{a_decision_id}} ORDER BY {{a_event_at}} DESC) = 1
)
SELECT kind, COUNT(*) AS decisions
FROM final_action
GROUP BY 1
ORDER BY 2 DESC
LIMIT 20
```

Reviewers. Fills `actions.reviewers`; write each row's reviewer as
`reviewer_<reviewer_rank>`. The query never returns a reviewer ID.

Snowflake:

```sql
WITH final_action AS (
  SELECT
    {{a_decision_id}} AS decision_id,
    {{a_actor}} AS actor,
    {{a_kind}} AS kind,
    {{a_event_at}} AS event_at
  FROM {{actions}}
  WHERE {{a_event_at}} >= {{since}}
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {{a_decision_id}} ORDER BY {{a_event_at}} DESC) = 1
)
SELECT
  ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) AS reviewer_rank,
  COUNT(*) AS actions,
  COUNT_IF(f.kind = 'accepted') / NULLIF(COUNT(*), 0) AS accept_unchanged_rate,
  MEDIAN(DATEDIFF('second', d.{{decided_at}}, f.event_at)) AS median_seconds_to_action
FROM final_action AS f
JOIN {{decisions}} AS d ON d.{{d_id}} = f.decision_id
WHERE f.actor IS NOT NULL
  AND d.{{decided_at}} >= {{since}} AND d.{{decided_at}} < {{until}}
GROUP BY f.actor
ORDER BY actions DESC
LIMIT 20
```

BigQuery:

```sql
WITH final_action AS (
  SELECT
    {{a_decision_id}} AS decision_id,
    {{a_actor}} AS actor,
    {{a_kind}} AS kind,
    {{a_event_at}} AS event_at
  FROM {{actions}}
  WHERE {{a_event_at}} >= {{since}}
  QUALIFY ROW_NUMBER() OVER (PARTITION BY {{a_decision_id}} ORDER BY {{a_event_at}} DESC) = 1
)
SELECT
  ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) AS reviewer_rank,
  COUNT(*) AS actions,
  SAFE_DIVIDE(COUNTIF(f.kind = 'accepted'), COUNT(*)) AS accept_unchanged_rate,
  APPROX_QUANTILES(TIMESTAMP_DIFF(f.event_at, d.{{decided_at}}, SECOND), 2)[OFFSET(1)]
    AS median_seconds_to_action
FROM final_action AS f
JOIN {{decisions}} AS d ON d.{{d_id}} = f.decision_id
WHERE f.actor IS NOT NULL
  AND d.{{decided_at}} >= {{since}} AND d.{{decided_at}} < {{until}}
GROUP BY f.actor
ORDER BY actions DESC
LIMIT 20
```

## 7. Outcome probes

Coverage. Fills `outcomes.rows` (`outcome_rows`) and the two timestamp
coverages.

Snowflake:

```sql
SELECT
  COUNT(*) AS outcome_rows,
  COUNT({{o_event_at}}) / NULLIF(COUNT(*), 0) AS event_at_coverage,
  COUNT({{o_observed_at}}) / NULLIF(COUNT(*), 0) AS observed_at_coverage
FROM {{outcomes}}
WHERE COALESCE({{o_observed_at}}, {{o_event_at}}) >= {{since}}
```

BigQuery:

```sql
SELECT
  COUNT(*) AS outcome_rows,
  SAFE_DIVIDE(COUNT({{o_event_at}}), COUNT(*)) AS event_at_coverage,
  SAFE_DIVIDE(COUNT({{o_observed_at}}), COUNT(*)) AS observed_at_coverage
FROM {{outcomes}}
WHERE COALESCE({{o_observed_at}}, {{o_event_at}}) >= {{since}}
```

Join and lag. Fills `decisions_with_outcome_rate`, `lag_days_p50` and
`lag_days_p90`. Recent decisions have not had time to produce an outcome: run
it once, then run it again with `{{until}}` moved back by the p90 lag and
report that coverage.

Snowflake:

```sql
WITH first_outcome AS (
  SELECT
    {{o_decision_id}} AS decision_id,
    MIN(COALESCE({{o_observed_at}}, {{o_event_at}})) AS seen_at
  FROM {{outcomes}}
  WHERE COALESCE({{o_observed_at}}, {{o_event_at}}) >= {{since}}
  GROUP BY 1
),
decision AS (
  SELECT {{d_id}} AS decision_id, MIN({{decided_at}}) AS decided_at
  FROM {{decisions}}
  WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
  GROUP BY 1
)
SELECT
  COUNT(o.decision_id) / NULLIF(COUNT(*), 0) AS decisions_with_outcome_rate,
  APPROX_PERCENTILE(DATEDIFF('hour', d.decided_at, o.seen_at) / 24, 0.5) AS lag_days_p50,
  APPROX_PERCENTILE(DATEDIFF('hour', d.decided_at, o.seen_at) / 24, 0.9) AS lag_days_p90
FROM decision AS d
LEFT JOIN first_outcome AS o ON o.decision_id = d.decision_id
```

BigQuery:

```sql
WITH first_outcome AS (
  SELECT
    {{o_decision_id}} AS decision_id,
    MIN(COALESCE({{o_observed_at}}, {{o_event_at}})) AS seen_at
  FROM {{outcomes}}
  WHERE COALESCE({{o_observed_at}}, {{o_event_at}}) >= {{since}}
  GROUP BY 1
),
decision AS (
  SELECT {{d_id}} AS decision_id, MIN({{decided_at}}) AS decided_at
  FROM {{decisions}}
  WHERE {{decided_at}} >= {{since}} AND {{decided_at}} < {{until}}
  GROUP BY 1
)
SELECT
  SAFE_DIVIDE(COUNT(o.decision_id), COUNT(*)) AS decisions_with_outcome_rate,
  APPROX_QUANTILES(TIMESTAMP_DIFF(o.seen_at, d.decided_at, HOUR) / 24, 100)[OFFSET(50)] AS lag_days_p50,
  APPROX_QUANTILES(TIMESTAMP_DIFF(o.seen_at, d.decided_at, HOUR) / 24, 100)[OFFSET(90)] AS lag_days_p90
FROM decision AS d
LEFT JOIN first_outcome AS o ON o.decision_id = d.decision_id
```

## 8. Fill the profile

```bash
python3 <skill-dir>/scripts/readiness.py template --workflow <workflow> \
  --out hindsight-discovery/<workflow>/profile.json
```

Copy each result into the matching field. Add one `sources` entry of type
`warehouse`, with the account or project and database as `location` and the
tables as `objects`. Rates are fractions from 0 to 1. Leave a field `null`
when its probe did not run.

## When a probe is denied

Leave the fields `null`, record the check as `deferred: privileges`, and give
the user this note to forward. Fill in the names; it asks for read access to
the listed tables and nothing else.

```text
Subject: Read access needed for a workflow-history readiness check

We are checking whether the stored history of <workflow> can be used to
evaluate its AI suggestions. The check runs aggregate SELECT queries only
(counts and rates); it does not read or export row content and writes nothing.

Please grant read-only access for <user or role> to:
  - <decisions table>
  - <human actions table>
  - <outcomes table>

Snowflake: USAGE on the database and schema, SELECT on those tables.
BigQuery: roles/bigquery.dataViewer on those tables and roles/bigquery.jobUser
on the project.
```

## Dialect notes

- Snowflake divides integers as decimals; `NULLIF(..., 0)` avoids a
  divide-by-zero error. BigQuery uses `SAFE_DIVIDE`.
- BigQuery: if the table is partitioned, filter on the partition column as
  well, or the window filter may not prune. Use a dry run to see bytes scanned
  before a probe on a very large table.
- The file path compares text after collapsing all whitespace; these probes
  compare after trimming the ends. A stored suggestion that differs from the
  final output only in internal spacing counts as different here.
- Timestamp arithmetic above assumes `TIMESTAMP` columns. Cast `DATETIME` or
  string columns first.
