---
max_turns: 12
timeout_seconds: 300
allowed_tools: [Skill, Read]
runs: 3
---
We want to onboard our prior-authorization review workflow to Hindsight. An AI drafts a reply to each request, a nurse reviewer sends it as is or rewrites it, and later we learn whether the authorization was approved. I can't give you warehouse access today, but our data engineer ran aggregate probes over the last 180 days and sent the results below. Is our history usable?

## Probe results (aggregates only, Snowflake, last 180 days)

| Measurement | Result |
|---|---|
| Rows in `ai_drafts` (one per request) | 48,210 |
| `ai_drafts.body` populated | 99% |
| `ai_drafts.body` equals `messages.body` (the reply that was sent) | 81% of reviewed requests |
| Last-modified time on `ai_drafts` | no such column |
| Column recording the reviewer's action (accepted, edited, ...) | no such column |
| Column recording whether the reviewer saw the draft | no such column |
| `ai_drafts.created_at` populated | 100% |
| `messages.sent_at` populated | 100% |
| `messages.sent_by` populated | 100%, 212 distinct values |
| `ai_drafts.model` populated | 100% |
| Prompt version, code version | not stored |
| `ai_drafts.input_json` populated | 97%; no capture time stored |
| Requests joined to a row in `auth_results` | 64% |
| Days from draft to `auth_results.recorded_at` | p50 6, p90 21 |
| Median requests per week | 1,850 |
| `requests.queue` populated | 100%, 14 distinct values |
| `requests.body` populated | 100% |
