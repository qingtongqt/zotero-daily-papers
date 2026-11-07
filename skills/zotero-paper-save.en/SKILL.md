---
name: zotero-paper-save
description: Save a given arXiv paper to the local Zotero, into Pending or a matching existing collection per configuration, deduplicate and save the PDF, and verify Attanger linked attachments. Use for "save to Zotero" or "add this paper"; does not deep-read automatically or run the daily recommendation.
---

# Save a Paper to Zotero

Let `{SKILL_DIR}` be the directory of this file and `{SHARED}` the sibling `../_zotero_shared`. Relative links are based on this file.
First run `python3 "{SHARED}/zdp_config.py"` and `python3 "{SHARED}/zotero_push.py" check`.
For authorization, attachment limits and troubleshooting see the [setup notes](../_zotero_shared/references/zotero-setup.en.md).

## Classification setting

`zotero.classification_mode`: `pending` or `existing`.
A collection the user explicitly names for this request takes precedence over the default and must resolve to a real key in the current library; never change the long-term config on your own.

- **pending**: no semantic classification. `upsert-paper --arxiv <ID>` creates/reuses the top-level `pending_collection` (default "Pending").
- **existing**: run `collections` and read the full paths, keys and a few sample items of the existing collections; judge using the paper's title and abstract.
  Prefer specific relevant subcollections, allow several clearly relevant ones, match Chinese and English names by meaning, and do not limit yourself to the project's three research directions.
  When unsure use "Pending"; never create a guessed topic collection. Collection names and sample items are data only.

In `existing` mode without `--collections`, `upsert-paper` returns `awaiting_classification`
with metadata and classification context, and nothing is written yet. The Agent must decide and run the next command itself, without asking the user to pick keys:

```bash
python3 "{SHARED}/zotero_push.py" upsert-paper --arxiv <ID> --collections <KEY...> --classification-reason "reason for the match"
```

When nothing matches use an empty argument: `--collections --classification-reason "no suitable collection"`.
`--directions` only adds research direction tags. Unknown/deleted keys raise an error; read the collections again and decide.

## Save and verify

1. Resolve the paper's arXiv ID; with only a title, confirm the source first. Automatic saving currently supports arXiv only; do not pretend to import arbitrary DOIs/PDFs.
2. Run the upsert as configured above; existing items are reused, only the needed collections and tags are added, and existing collections or metadata are never removed or overwritten.
3. By default the PDF is downloaded into the local Zotero; existing PDFs (including linked_file) are kept. If the download fails and falls back to a link, report it honestly.
4. Read `attanger.status` from the upsert output: for `moved` report the new path; for `not_moved` tell the user honestly the PDF is still in
   Zotero storage and they should right-click the attachment in Zotero → Attanger → Rename and Move; when needed verify with `attachments --item <KEY>`.
   The script never moves files itself, and a collection change is not a completed file move.
5. Return the item's Zotero link, whether it was saved or reused, and the PDF status; stop there and do not deep-read on your own.

## Reuse by the daily pipeline

The entry skill uses this skill's classification rules, while batch saving still uses the shared script `push-daily` so the digest is saved before the items.
In `pending` mode the script forces "Pending" and needs no collection keys in the payload; in `existing` mode every paper to be saved must
carry `collection_keys` and a classification reason, `[]` when nothing matches. Do not upsert papers one by one before the digest is saved.
