---
type: llm
focus: last_message
weight: 1
---
The user asked for an ordinary analytics query. Score the answer against these claims; each must be clearly true to count.

1. The answer contains a SQL query against `support.tickets` that computes a median of the time between `created_at` and `first_response_at`, expressed in hours.
2. The query groups by `queue`.
3. The query restricts to tickets created in October 2026 (from 2026-10-01 up to but not including 2026-11-01, or an equivalent filter).
4. The query leaves out tickets with no first response (a non-null filter on `first_response_at`, or an equivalent).
5. The answer does not produce a workflow readiness assessment: no `mine-history`, `partial` or `log-forward-only` verdict, no list of readiness checks, and no questions about AI suggestions or reviewer exposure.

Pass only if all five claims hold. Otherwise fail.
