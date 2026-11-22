---
name: workflow-discover
description: Assess whether a human-in-the-loop AI workflow's stored history (AI suggestion, human action, outcome) can be mined for Nodal Hindsight, and write a readiness report. Use when onboarding a workflow. Do not use for analytics questions, eval runs, or runtime routing.
---

# Workflow Discover

You are assessing one human-in-the-loop AI workflow: an AI makes a suggestion, a
person accepts, edits or overrides it, and later an outcome shows how it went.
Hindsight turns that history into evaluation cases. Your job is to find out how
much of the history is usable and to write a readiness report. You work from
aggregates by default; a small sample of rows is allowed only after the user
consents. You do not extract, map or load the data; that is a later step.

Resolve every resource from the directory containing this loaded `SKILL.md`. The
examples below write `<skill-dir>` for that directory.

## Hard fences

These hold for the whole session, including when the user asks otherwise.

- **Aggregates by default.** Do not select, print, sample or quote a free-text
  column: the suggestion, the final output, request content, input context,
  reasons, notes. No `SELECT *`. Do not open an export with a file reader,
  `head` or `cat`. These fields can hold protected health information, and
  anything you read is sent to the AI provider behind this session. Column
  names and types are fine.
- **Sample rows need consent.** The one way to view row content is the
  consent gate in `references/content-samples.md`: the user confirms this
  agent is approved to read row content for this workflow, and then you may
  view a few truncated values of named columns, in the session only. Without
  a clear yes, stay on aggregates. With or without consent, no row content is
  ever written to the profile, the report or any other file.
- **Status values are the one exception.** You may list the values of a column
  only when the user has named it as a category, action-kind or exposure field,
  and it passes the shape test in the source reference (at most 50 distinct
  values, at most 64 characters, values repeat). Never list reviewer IDs;
  reviewers appear as `reviewer_1`, `reviewer_2`.
- **Read-only.** Every warehouse statement is a single `SELECT` or `WITH`
  query. Never DDL, DML, grants or procedure calls. Never write to the source.
  In the workflow's repository, read code and configuration only: no edits, no
  commits, no running the workflow, no opening data files or secret files.
- **Bounded.** Every probe has a date window (default: the last 180 days) and
  returns one row, or a grouped result with a `LIMIT`.
- **No credentials.** Use the connection the session already has. Never ask
  for, read, print or store a password, key or token. With no connection, say
  so and offer the file-export path.
- **Accept-history is not correctness.** A reviewer accepting a suggestion does
  not show the suggestion was right. Never describe an accept rate as accuracy,
  quality or evidence that a class is safe to automate.

## Flow

### 1. Which workflow, and where is its code?

Ask one question, then the next. First: which workflow, in the user's words.
Turn the name into a lowercase slug such as `prior-auth-review`; this is
`<workflow>` below.

Then ask for the repository that runs the workflow, as a choice:

- **It is on this machine**: ask for the path.
- **It needs cloning**: ask for the URL and where to put it.
- **No repository is available**: go to step 2 and ask where the history
  lives.

With a repository, read `references/source-repo.md` and trace one run through
the code before asking anything else. The code shows where the suggestion,
the human action and the outcome are written, so you can tell the user where
the history lives instead of asking.

### 2. Measure the stored history

The code says where records are written; only the data says how complete they
are. Confirm the store with the user as a choice, naming what the code showed
("the code writes drafts to `ai_drafts` in Snowflake; measure there, or from
an export?"):

- **A data warehouse** (Snowflake, BigQuery) this session can already query.
- **File exports** (CSV or JSONL) on this machine.
- **Somewhere else** (a trace platform, a CRM, an agent platform, only in the
  application database).

"Somewhere else" is not supported yet. Record it as a source of type
`unsupported` with a note saying what would be needed (for example "an export
of traces to JSONL with the suggestion, the reviewer action and a timestamp"),
and ask whether a warehouse copy or a file export exists. If neither does,
still write the report from what the code showed; it says that no stored
history was measured.

Read the one reference that matches, when you reach this step:

- Warehouse: `references/source-warehouse.md`. Probe the connection, find the
  tables and columns from metadata, run the aggregate probes, and fill a
  profile started with `python3 <skill-dir>/scripts/readiness.py template`.
- Files: `references/source-files.md`. Describe the columns with
  `list-columns`, write a column map, and run `profile-files`. The script
  computes the aggregates locally, so row content never enters this
  conversation.

Either way the result is one profile file,
`hindsight-discovery/<workflow>/profile.json`, in the current directory. It
holds aggregates only. `references/readiness-format.md` defines every field;
`null` means not measured and `0` means measured and absent.

When a probe is denied, do not stop. Leave its fields `null`, give the user the
forwardable grant note from `references/source-warehouse.md` straight away,
since access is the slowest dependency, and carry on with the other probes.

### 3. Ask only where the evidence is ambiguous

Read `references/checks.md`. It lists, for each of the ten checks, which
results are ambiguous and which answers resolve them. Do not ask about a check
the probes settled.

**Selection beats confirmation.** Before asking anything, run this ladder:

1. **Two or more readings fit the evidence.** Present them as a choice, with
   the evidence (a measurement, or the file in the repository) in the question: "`ai_drafts.body` equals `messages.body` in
   81% of rows. Is `ai_drafts` (a) the suggestion as the reviewer saw it, (b) a
   later regeneration, or (c) unknown?" Each option is a distinct reading that
   the evidence supports. Always include "unknown" when it is an honest answer.
   Never invent an option that has no evidence behind it.
2. **One credible reading with an uncertain part.** Do not ask the user to
   confirm a paragraph. Ask the single fact the reading depends on: "Is
   `reviewed_by` the person who made the decision, or the queue it was
   assigned to?"
3. **Settled by a probe, or by code that has not changed during the
   window.** Do not ask. Record a code finding in `answers` with
   `"source": "code"` and the file it came from.

When a few example rows would resolve an ambiguity better than a question
(which of two columns is the suggestion, whether a category field holds real
request types), read `references/content-samples.md` and ask its consent
question once. On no, or not sure, carry on with choice questions.

A bare "is that right?" means you skipped the ladder. Ask one question at a
time. Record each answer in the profile's `answers` list with the check ID, the
question, the option chosen (the label, never pasted row content) and the
status that `references/checks.md` assigns to that option.

### 4. Write the report and summarise

```bash
python3 <skill-dir>/scripts/readiness.py build \
  --profile hindsight-discovery/<workflow>/profile.json \
  --out hindsight-discovery/<workflow>
python3 <skill-dir>/scripts/readiness.py check \
  hindsight-discovery/<workflow>/readiness.json
```

`build` applies the verdict rules and writes `readiness.json` and
`readiness.md`. The report states whether sample rows were viewed, from
`content_access` in the profile. Do not edit those two files by hand; change the profile and
rebuild. If you cannot run the script in this session, say so, apply the
rules in `references/checks.md` yourself, and mark the verdict provisional.
While a question from step 3 is open, say which verdict each answer leads to. `--blind-sample-rate` (default `0.10`)
sets the share of weekly volume assumed to be reviewed blind in the
labels-needed table; ask the user only if they already have a rate in mind.

Then tell the user, in this order:

1. The verdict (`mine-history`, `partial` or `log-forward-only`) and what it
   means for them in one sentence.
2. The status of each record type: Decision, HumanAction, Outcome.
3. One disposition line per check, all ten, in order:
   `<check>: present | partial | missing | unresolved | deferred: <why>`.
   Use `deferred: privileges (grant note handed over)` or `deferred: auth` for
   a probe that could not run. A check you cannot write a line for is a check
   you skipped; go back and run it.
4. What to start logging now, most valuable first, taken from the missing and
   partial checks.
5. Where the files are, and that the labels-needed table in `readiness.md` is
   a reference table, not a sample plan.

## What discovery cannot do

- Recover a suggestion that was overwritten, exposure that was never logged,
  or reviewer IDs that do not exist. It reports these; it does not fix them.
- Make outcomes arrive sooner or correct outcomes that are missing for
  non-random reasons.
- Certify anything. Mined accept-history is descriptive. Certification needs a
  blind-reviewed or outcome-supported cohort.
- Map the history to Hindsight's record format, load it, or build a request
  taxonomy. Those belong to a later step. Consent to view a few sample rows
  here does not cover reading content in bulk for taxonomy work.
