# Source: the workflow's code repository

Start here whenever the user can give you the repository that runs the AI
workflow. The code says where each record is written, which is faster and
more reliable than guessing from table or file names. It cannot say how
complete the stored history is; that still needs the measurements in
`references/source-warehouse.md` or `references/source-files.md`.

## 1. Get the repository

Ask whether it is already on this machine or needs cloning:

- **Local**: ask for the path. Read it in place.
- **Needs cloning**: ask for the URL and where to put it, then clone with the
  Git credentials the machine already has. If the clone is refused, do not ask
  for a token; ask the user to clone it and give you the path.
- **Not available**: skip this reference and go straight to the data source.

Rules while reading:

- Read only. Do not edit, commit, install dependencies or run the workflow.
- Do not open secret files: `.env`, `*.pem`, `*.key`, credential or token
  files, or a secrets directory. A connection string's host and database name
  are useful; a password in the same line is not to be repeated anywhere.
- Fixtures and sample data in a repository can hold real records. Do not open
  data files; read code, configuration and schema definitions.

## 2. Trace one run from start to finish

Find the entry point (the scheduled job, the request handler, the queue
consumer) and follow one item through. Search for the model call first: the
client library import, the agent or prompt definition, the scheduler entry.
Then answer these, noting the file for each:

| Question | What to look for | Check |
|---|---|---|
| Where is the AI output written, and to which field? | The write after the model call: an insert, an API call, a file append | `suggestion_separate` |
| Is that same field written again later? | An update to the same column or key when a person edits; a regenerate path | `suggestion_not_overwritten` |
| Where does a person see the output, and can they act without seeing it? | The review screen, message or approval step; any blind-review or sampling branch | `exposure_known` |
| Where is the person's action written, and with which values? | The accept, edit, reject handlers and the status values they store | `reviewer_id`, `volume_accept_rate` |
| Which timestamps are set, and by what? | Explicit event times versus an auto-updated `updated_at` | `timestamps` |
| Are the model name, prompt version and code version stored with each run? | Fields set at write time; prompts kept in files with versions; a release tag | `policy_identity` |
| Is the input stored with the run, or looked up again later? | A saved copy of the input versus a join to live tables | `frozen_context` |
| Where does the later result come from, and what key ties it back? | A callback, a sync job, a status change; the ID it carries | `outcome_join` |
| Is there a queue, category, tag or intent on each request? | A field set on intake or by a classifier | `request_type_signal` |

Check the history as well as today's code. `git log` on the files that write
these records shows when a field was added or a behaviour changed. Code that
has logged exposure for two months says nothing about the four months before.

## 3. What to do with what you find

- **Name the stores.** The code tells you the tables, files or service where
  each record lives. Use that to choose the data source for the next step, and
  tell the user what you found instead of asking them where things are.
- **Add the repository to `sources`** in the profile: type `repository`, the
  repository name or path as `location`, and the files you relied on as
  `objects`.
- **Let the code settle a check only when it can.** A code finding goes into
  the profile's `answers` list with `"source": "code"`, citing the file, when
  both of these hold: the code is unambiguous, and Git history shows it has
  behaved that way for the whole window being assessed. Example: the review
  screen renders the draft unconditionally and that file is unchanged for a
  year, so `exposure_known` is `present`.
- **Otherwise turn it into a question.** When the code changed during the
  window, or two paths write the same record, put the evidence in a choice
  question to the user: "`review.py` has logged `shown` since March; before
  that nothing recorded it. For the earlier months, was the draft (a) always
  on screen, (b) sometimes hidden, or (c) unknown?"
- **Never use the code as a measurement.** Coverage, volume, accept rate, lag
  and join rate come from the data. A field the code writes can still be empty
  in half the rows.

## 4. When the history is only in an agent's own store

Some workflows keep nothing outside the agent framework's session or log
store. The code or the framework's configuration will show where that store
is and what format it uses. If it is a set of JSONL or CSV files, or a table,
treat it as a file or warehouse source. If it is something else, tell the user
which export would be needed (one line per run with an ID, a timestamp and the
output, as JSONL) and record the source as `unsupported` with that note.
