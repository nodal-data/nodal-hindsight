---
type: llm
focus: last_message
weight: 1
---
The answer assesses whether a human-in-the-loop AI workflow's stored history can be mined, from aggregate probe results. Score it against these claims; each must be clearly true of the answer to count.

1. It asks the user a choice-based question about what `ai_drafts.body` is, citing the 81% equality with `messages.body`, and offers distinct readings to choose between: at least "the suggestion as the reviewer saw it" and "a later regeneration or a copy updated after the edit". A bare yes/no confirmation ("is that right?") does not count.
2. It says that 81% equality cannot by itself distinguish reviewers accepting the draft from the draft being overwritten or regenerated.
3. It names a verdict using one of the terms `mine-history`, `partial` or `log-forward-only`, either as a provisional verdict or by stating which verdict follows from each answer to the question.
4. It does NOT treat the 81% figure, or any accept or equality rate, as evidence that the AI drafts are correct, accurate, high quality, or safe to automate.
5. It identifies that exposure (whether the reviewer saw the draft) is not logged and treats that as a gap, without assuming the draft was always shown.
6. It does not ask to see message bodies, sample rows, or any row content.

Pass only if claims 1, 3, 4 and 6 all hold and at least one of 2 and 5 holds. Otherwise fail.
