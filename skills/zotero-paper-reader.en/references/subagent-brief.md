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
- When the host has no sub-agents, run the papers one by one in the current session, re-read this brief
  before each paper, and ignore the content of earlier papers.

## Brief

````text
Explain this paper.

- Paper: arXiv {ARXIV_ID} (version: {version or "latest"})
- Zotero item_key: {ITEM_KEY; "none" for a new paper}
- Existing note of the same kind: {none / note_key and the user's handwritten content to keep}
- Language: {zh or en}
- Extra user requests: {none / the user's own words}
- Temporary note path: {absolute TEMP_DIR}/notes/{ARXIV_ID}.md
- Shared scripts directory: {absolute SHARED}
- The full-text retrieval and saving workflow is in the "Get the full text and read" and "Save and self-check" sections of {absolute SKILL_DIR}/SKILL.md.
  For a new paper without an item_key, save it after reading by following {absolute SKILL_DIR}/../zotero-paper-save/SKILL.md.

Read the full text, then explain it in natural, accessible language.
Stay faithful to the paper, and state the actual reading scope if any full text was unavailable.

Write the explanation to the temporary Markdown file above, save it as a Zotero child note with add-note --kind full-note, and read it back with notes --item.
End with one line of JSON so the main agent can record progress:
{"arxiv_id": "...", "item_key": "...", "note_key": "...", "status": "done|failed", "scope": "full-text|partial|abstract-only", "reason": "why it failed or was partial, empty otherwise", "verdict": "one-sentence verdict"}
````
