---
name: zotero-paper-reader
description: Deep-read or quick-read a given paper, arXiv link or existing Zotero item, reading the local PDF (including Attanger attachments), and save the research note as a Zotero child note. Use for "read this", "deep read", "take a quick look"; hand save-only requests to zotero-paper-save.
---

# Read a Paper and Save a Child Note

Let `{SKILL_DIR}` be the directory of this file and `{SHARED}` the sibling `../_zotero_shared`. Relative links are based on this file.
Run `python3 "{SHARED}/zdp_config.py"` to get the configuration and temp directory.
Write in English by default; follow the user's language.

## Locate the paper and existing notes

Parse the arXiv ID (keep the requested version for reading), source link or existing item_key. Ask first when the paper's identity is unclear.
When the daily pipeline passes an item_key, reuse it directly without saving or classifying again.
For a single paper first run `find --arxiv <ID>`; if it exists, check existing notes with `notes --item <KEY>`.
Note length and formula counts are hints, not completeness criteria; when needed read the note HTML through a local API item GET
and check coverage of method, experiments, main formulas, main table, key figures and sources. A complete note that fits the request can be reused;
updates must keep the user's handwritten content, and a quick summary must never overwrite a full note. Never claim the original was kept based only on note statistics.

## Hand deep reads to a sub-agent

Always hand a full deep read (full-note) to its own sub-agent so the current conversation cannot pollute the note: after locating the paper
and checking existing notes above, the main agent dispatches one fresh sub-agent per paper following the [sub-agent brief](references/subagent-brief.md),
waits for the result and reads it back. The main agent neither reads the full text nor writes the note body itself.
Quick reads, critical analysis, formula extraction and other short tasks are done directly in the current session.
A sub-agent running from the brief starts at the two sections below.

## Get the full text and read

1. For an existing item, get real paths with `attachments --item <KEY>` and prefer a local PDF with exists=true.
2. Without a usable attachment, read the arXiv HTML and fall back to the PDF; never silently switch the version being read.
3. Write a temporary Markdown file following the [deep-reading template](references/paper-note-template.md).
4. Generate the "Relation to my research directions" section with one entry per `name` in the effective config daily_papers.directions.
5. When the full text is unavailable, state the reading scope clearly and do not present an abstract note as a full deep read; never pad words, formulas or figures.

"Take a quick look" writes 300–600 words of key points with kind=quick; the default full reading uses full-note.
When the user asks for critical analysis or formula extraction, organize the content accordingly instead of forcing a full deep-read note.

## Save and self-check

For a new paper, after reading and writing the note, run the [save skill](../zotero-paper-save/SKILL.md); skip saving for existing items.

```bash
python3 "{SHARED}/zotero_push.py" add-note --item <KEY> --markdown <temporary note path> --kind full-note
python3 "{SHARED}/zotero_push.py" notes --item <KEY>
```

Use `--replace` on an existing note of the same kind only after keeping the user's content. Confirm the child note really exists and matches its sources.
When citing figures, include a Zotero deep link (zotero://open-pdf/library/...) down to the page.
When called from the daily pipeline, return the arXiv ID, item_key, note_key and done/failed status so the entry skill can update reading progress.
The sub-agent ends with one line of JSON as the brief says; after reading it back, the main agent gives the user a short verdict, the Zotero link and anything actually left unfinished.
