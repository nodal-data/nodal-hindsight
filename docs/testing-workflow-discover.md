# Testing `workflow-discover` on another machine (Codex, Hermes daily job)

A short walkthrough for running discovery against a daily job that uses Hermes
as its agent, from a machine that runs Codex.

Two things are not verified yet, so treat them as the points most likely to
need adjusting:

- This repo ships a Claude Code plugin only. There is no Codex plugin manifest,
  so on Codex the skill is installed by copying its folder (step 2).
- Nobody has looked at where that Hermes job keeps its history. The skill
  finds out by reading the job's repository (step 3). It then measures a
  warehouse or CSV/JSONL exports; anything else is recorded as "not yet
  supported".

## 1. Get the code

```bash
git clone https://github.com/nodal-data/nodal-hindsight.git
cd nodal-hindsight
```

This uses `main`. If you already have a clone, run `git switch main` and
`git pull`.

Python 3.11 or later is the only requirement. The skill's script uses the
standard library, so there is nothing to install.

Check the script runs:

```bash
python3 skills/workflow-discover/scripts/readiness.py --help
```

## 2. Make the skill available to Codex

Copy the skill folder into your Codex skills directory, then restart Codex:

```bash
mkdir -p ~/.codex/skills
cp -R skills/workflow-discover ~/.codex/skills/
```

If Codex does not list `workflow-discover` afterwards, skip installation and
point it at the file instead. Start Codex anywhere and say:

> Read `<path to clone>/skills/workflow-discover/SKILL.md` and follow it.

That works on any agent that can read files and run `python3`.

## 3. Have the job's repository ready

The skill starts from the repository that runs the daily job, because the code
shows where each record is written. Have one of these to hand:

- the path to the repository, if it is already on that machine, or
- its Git URL, so the skill can clone it with the credentials the machine
  already has.

The skill reads code and configuration only. It does not run the job, change
the repository, or open secret files.

From the code it works out where three kinds of record live:

| Record | What it looks for |
|---|---|
| AI suggestion | Where what Hermes produced on each run is stored |
| Human action | Whether a person accepts, edits or rejects that output, and where that is recorded |
| Outcome | Any later signal that the output was right or wrong |

It then measures the stored history:

- **In tables** in Snowflake or BigQuery, if Codex on that machine can already
  query them.
- **In files** (CSV or JSONL), if that is where the job writes or if you
  export them. Each row needs a run or decision ID so the three kinds of
  record can be joined.
- **Only in Hermes's own session or log store**: the skill will tell you which
  export it needs (JSONL, one line per run with an ID, a timestamp and the
  output). Without one it records the store as unsupported and reports from
  the code alone.

If no person reviews the job's output, the report shows the human-action
checks as missing. That is a correct result, and it tells you what to start
logging.

## 4. Run the discovery

Start Codex in the folder where you want the report written (an empty working
folder is fine; no Git repo is needed), and ask:

> Use the workflow-discover skill to check whether the history of our daily
> Hermes job can be used with Hindsight.

What to expect:

1. It asks which workflow this is, then for the repository: give the path or
   the URL from step 3.
2. It reads the code and tells you where it thinks the records live.
3. It explains why a reviewer's approval alone is weak evidence, then asks
   what later shows that a run's output was right or wrong (a later reply, a
   status in another system, an audit), where that is stored and how long it
   takes. "Nothing like this exists yet" is a valid answer.
4. It asks you to confirm where to measure the records, then measures. For files it runs `list-columns` and `profile-files`; for a
   warehouse it runs aggregate `SELECT` queries.
5. It asks a few choice questions where the evidence is ambiguous, for example
   which column is the suggestion, or whether the stored output is what the
   reviewer saw. If seeing a few example rows would help, it asks once whether
   the agent is approved to read row content. Answer yes only if your company
   allows that data to go to the AI provider behind Codex.
6. It writes three files and summarises the verdict:

```text
hindsight-discovery/<workflow>/profile.json
hindsight-discovery/<workflow>/readiness.json
hindsight-discovery/<workflow>/readiness.md
```

## 5. What to check

- **No row content appeared without your consent.** Codex should not print
  message bodies or sample rows, and should not open the export files
  directly, unless it first asked whether the agent is approved to read row
  content and you said yes. Try answering "not sure": it should stay on
  aggregates. After a yes it may view at most 5 rows per look and 20 in the
  session, of named columns only, and the report states how many it viewed.
- **Nothing from the rows reached the files.** Search the three output files
  for a phrase you know is in the data; it should not be there, whether or not
  you consented. Reviewer IDs should never appear anywhere.
- **The verdict follows from the checks.** Read `readiness.md`: the verdict is
  one of `mine-history`, `partial`, `log-forward-only`, with all ten checks
  listed and a status for Decision, HumanAction and Outcome.
- **The file validates:**

  ```bash
  python3 ~/.codex/skills/workflow-discover/scripts/readiness.py check \
    hindsight-discovery/<workflow>/readiness.json
  ```

- **The outcome step happened.** It explained why approvals are weak evidence
  before asking, and `readiness.md` has an Outcome section listing the signal
  you named, or saying none exists yet.
- **The questions were real choices**, with the evidence in the question, not
  "is that right?".
- **Code findings are attributed.** A check settled from the repository shows
  `code` in the "Answered by" column and names the file.
- **The repository is unchanged**: `git status` in it shows nothing new.
- **Accept rates are not described as accuracy** anywhere in the summary.

## 6. Send back

The three files in `hindsight-discovery/<workflow>/` hold aggregates only,
even if you approved sample rows, so they are safe to share. Useful notes to add: how long the run took, any
question that was confusing, and any point where Codex tried to read row
content or could not run a step.

## Trying it without real data

The repo has small synthetic exports. From the clone:

```bash
python3 skills/workflow-discover/scripts/readiness.py profile-files \
  --decisions tests/fixtures/workflow_discover/decisions.csv \
  --actions tests/fixtures/workflow_discover/actions.jsonl \
  --outcomes tests/fixtures/workflow_discover/outcomes.jsonl \
  --columns tests/fixtures/workflow_discover/columns.json \
  --workflow fixture-workflow --out /tmp/discover/profile.json
python3 skills/workflow-discover/scripts/readiness.py build \
  --profile /tmp/discover/profile.json --out /tmp/discover
```

Then open `/tmp/discover/readiness.md`. You can also point Codex at that
fixture folder in step 4 to rehearse the conversation.
