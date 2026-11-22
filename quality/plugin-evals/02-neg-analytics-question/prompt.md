---
max_turns: 8
timeout_seconds: 180
allowed_tools: [Skill, Read]
runs: 3
---
I need a number for tomorrow's ops review. Our Snowflake table `support.tickets` has one row per ticket with `ticket_id`, `queue`, `created_at`, `first_response_at` and `closed_at`. Can you write me the SQL for median time to first response in hours, by queue, for tickets created in October 2026? Tickets with no response yet should be left out.
