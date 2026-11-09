# Deep-Reading Sub-agent Brief

Run every full deep read (full-note) in its own sub-agent: one fresh context per paper,
unaffected by the day's candidate list, scores, reviews, other papers or the earlier conversation.

## How the main agent dispatches

- Start one **fresh** sub-agent per paper (in Claude Code, a general-purpose sub-agent from the Agent tool, not a fork;
  on Codex and other hosts, their own sub-agent feature). Do not inherit or retell the current conversation.
- The prompt contains only the filled-in brief below; never attach the candidate list, scoring reasons, reviews or other papers' notes.
  Write every path as an absolute path, because the sub-agent does not know `{SKILL_DIR}`, `{SHARED}` or `TEMP_DIR`.
- Run at most 3 sub-agents at a time; each writes only its own item and temporary files, so they can run in parallel.
- While a sub-agent runs, the main agent does not read that paper's full text or rewrite its note.
- When a sub-agent finishes, the main agent confirms the note_key exists with `notes --item <KEY>` before recording progress;
  never mark a reading done on the sub-agent's reply alone. A failed item can be dispatched again with the same brief.
- When the host has no sub-agents, run the papers one by one in the current session, re-read this brief and the note template
  before each paper, and ignore the content of earlier papers.

## Brief

````text
Please deep-read arXiv {ARXIV_ID} (version: {version or "latest"}) and write it up as a Zotero child note.

- Zotero item_key: {ITEM_KEY; "none" for a new paper}
- Existing note of the same kind: {none / note_key and the user's handwritten content to keep}
- Language: {zh or en}
- Extra user requests: {none / the user's own words}
- Temporary note path: {absolute TEMP_DIR}/notes/{ARXIV_ID}.md
- Shared scripts directory: {absolute SHARED}
- The workflow is in the "Get the full text and read" and "Save and self-check" sections of {absolute SKILL_DIR}/SKILL.md,
  and the note structure is in {absolute SKILL_DIR}/references/paper-note-template.md.
  For a new paper without an item_key, save it after reading by following {absolute SKILL_DIR}/../zotero-paper-save/SKILL.md.

What to focus on:
- Read the whole paper before writing. The note centres on the problem and motivation, and the method.
- The problem and motivation come mainly from the Introduction: what problem the authors solve, where existing methods
  concretely fall short, and why this idea works.
- Describe the method well enough to reproduce it: inputs and outputs, what each module does, the training objective or loss,
  key formulas (with Eq. numbers), the algorithm flow and important hyperparameters.
- Keep experiments brief: the setup, plus the main table's key numbers and the gap to the strongest baseline.
- Cite the source next to key claims (§, Table, Figure, Eq.) and separate the authors' claims, the paper's evidence and your inferences.
- For any part of the full text you could not read, state the actual reading scope.

When done, save with add-note and read it back with notes --item.
End with one line of JSON so the main agent can record progress:
{"arxiv_id": "...", "item_key": "...", "note_key": "...", "status": "done|failed", "scope": "full-text|partial|abstract-only", "reason": "why it failed or was partial, empty otherwise", "verdict": "one-sentence verdict"}
````
