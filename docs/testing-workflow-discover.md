# Testing `workflow-discover` on another machine (Codex, Hermes daily job)

A short walkthrough for running discovery against a daily job that uses Hermes
as its agent, from a machine that runs Codex.

Two things are not verified yet, so treat them as the points most likely to
need adjusting:

- This repo ships a Claude Code plugin only. There is no Codex plugin manifest,
  so on Codex the skill is installed by copying its folder (step 2).
- Nobody has looked at where that Hermes job keeps its history. Step 3 finds
  out. The skill reads a warehouse or CSV/JSONL exports; anything else is
  recorded as "not yet supported".

## 1. Get the branch

```bash
git clone https://github.com/nodal-data/nodal-hindsight.git
cd nodal-hindsight
git switch workflow-discover
```

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

## 3. Find where the job's history lives

Before starting the skill, answer three questions about the daily job. The
skill needs all three kinds of record, and the answers decide which path you
take.

| Record | Question for this job |
|---|---|
| AI suggestion | Where is what Hermes produced on each run stored? |
| Human action | Does a person accept, edit or reject that output? Where is that recorded? |
| Outcome | Is there a later signal that the output was right or wrong? Where? |

Then pick the path:

- **The records are in tables** in Snowflake or BigQuery, and Codex on that
  machine can already query them: use the warehouse path.
- **The records are in files**, or can be exported: use the file path. Put
  CSV or JSONL files in one folder, ideally one file each for decisions,
  human actions and outcomes. One wide file also works. Each row needs a run
  or decision ID so the three can be joined.
- **The records exist only in Hermes's own session or log store**: export
  them to JSONL first, one line per run, with at least an ID, a timestamp and
  the output. Without an export the skill records the source as unsupported
  and measures nothing.

If no person reviews the job's output, say so when asked. The report will then
show the human-action checks as missing. That is a correct result, and it
tells you what to start logging.

## 4. Run the discovery

Start Codex in the folder where you want the report written (an empty working
folder is fine; no Git repo is needed), and ask:

> Use the workflow-discover skill to check whether the history of our daily
> Hermes job can be used with Hindsight.

What to expect:

1. It asks which workflow this is and where the history lives. Answer with the
   path from step 3.
2. It inspects the source. For files it runs `list-columns` and
   `profile-files`; for a warehouse it runs aggregate `SELECT` queries.
3. It asks a few choice questions where the evidence is ambiguous, for example
   which column is the suggestion, or whether the stored output is what the
   reviewer saw.
4. It writes three files and summarises the verdict:

```text
hindsight-discovery/<workflow>/profile.json
hindsight-discovery/<workflow>/readiness.json
hindsight-discovery/<workflow>/readiness.md
```

## 5. What to check

- **No row content appeared.** Codex should never print message bodies, sample
  rows or reviewer IDs, and should not open the export files directly. Search
  the three output files for a phrase you know is in the data; it should not
  be there.
- **The verdict follows from the checks.** Read `readiness.md`: the verdict is
  one of `mine-history`, `partial`, `log-forward-only`, with all ten checks
  listed and a status for Decision, HumanAction and Outcome.
- **The file validates:**

  ```bash
  python3 ~/.codex/skills/workflow-discover/scripts/readiness.py check \
    hindsight-discovery/<workflow>/readiness.json
  ```

- **The questions were real choices**, with the evidence in the question, not
  "is that right?".
- **Accept rates are not described as accuracy** anywhere in the summary.

## 6. Send back

The three files in `hindsight-discovery/<workflow>/` hold aggregates only, so
they are safe to share. Useful notes to add: how long the run took, any
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
