---
name: zotero-daily-papers
description: Full daily paper recommendation pipeline - fetch, filter, deduplicate and review new papers, save the daily digest to the local Zotero first, then save papers per configuration, and finally deep-read the must-read papers as child notes. Use for today's paper recommendations, recommendations for the past few days, or resuming the last run; hand off save-only or read-only requests for a specific paper to the matching skill.
---

# Zotero Daily Papers Entry Point

This is the orchestration entry for the full pipeline. The user only needs to say "today's paper recommendations" once.
Order: **fetch → filter → deduplicate → review → save digest → save papers → deep-read must-read papers**.
All papers, the digest and reading notes are saved to Zotero; Markdown/JSON files are only temporary intermediates.

## Routing and shared resources

Let `{SKILL_DIR}` be the directory of this file and `{SHARED}` the sibling `../_zotero_shared`.

- Save a paper or PDF only, or manage collections: run [zotero-paper-save](../zotero-paper-save/SKILL.md); do not deep-read automatically.
- Deep-read or quick-read a given paper: run [zotero-paper-reader](../zotero-paper-reader/SKILL.md); do not run the daily recommendation.
- Daily / past-N-days recommendation: complete every stage below.

## 1. Configuration and checks

Run `python3 "{SHARED}/zdp_config.py"` and take `runtime.temp_dir` as `TEMP_DIR`,
then run `python3 "{SHARED}/zotero_push.py" check`. Local mode needs Zotero 10+ running with the local API enabled.
For authorization and troubleshooting see [Zotero setup](../_zotero_shared/references/zotero-setup.en.md). Never print credentials or touch the database directly.
Use a different `ZDP_TEMP_DIR` for each pipeline when running several at once.
Write all reviews and notes in English (the effective config has `language: "en"`); if the user explicitly asks for Chinese, follow the user.

The saving setting `zotero.classification_mode` has exactly two values:

- `pending`: recommended papers go to the top-level "Pending" collection (`pending_collection`), created if missing. No classification by the Agent.
- `existing`: following the save skill, read the existing collections, their paths and sample items, and match by title and abstract; only papers without a match go to "Pending".

Research directions are only used for recommendation filtering and tags; they do not decide the real collection. Existing items are reused, keeping the user's collections and attachments.

## 2. Fetch, filter, deduplicate, review

Follow the [daily preparation stage](references/daily-workflow.md) to produce the checked candidates, enriched metadata and `zotero_payload.json`.

## 3. Save the digest first, then the papers

```bash
python3 "{SHARED}/zotero_push.py" push-daily --payload "{TEMP_DIR}/zotero_payload.json" --enriched "{TEMP_DIR}/daily_papers_enriched.json"
```

The script first saves today's digest (and stops automatically if that fails), then saves papers, PDFs and review child notes
for the `write_tiers` (must/worth by default), and records this run in the recommendation history. Read requests that hit 429/5xx
or a dropped connection are retried with backoff; writes are never retried automatically.
A rerun on the same day **resumes**: it reads the existing `zotero_push_result.json`, skips stages and papers that already finished
with an unchanged payload, processes only failed/unfinished ones, and merges everything into one cumulative record.
Pass `--fresh` only to redo the whole batch.
Note: multi-day `--days N` mode does not deduplicate against history, so previously recommended papers may reappear.

Read `zotero_push_result.json`: it contains `stage`, the digest key, per-paper results, the unresolved errors, `reading_queue`,
`summary` (saved/created/existing/failed counts) and `runs` (papers processed and skipped, and errors, per run).
A paper's `created` says whether this task created it (cumulative across runs; a rerun never resets it to false); report new papers from `summary.created`.
Each paper result has `attanger.status`; when reporting, list the papers with `not_moved` and ask the user to rename and move them manually in Zotero.
Exit code 2 means partial failure; check the cause and rerun the same command to resume. Do not redo the whole batch.
Never ignore errors silently; reading can continue for the successful part, and failures are recorded and reported separately.

## 4. Deep reading

After the digest and paper saving stages are complete, run
[zotero-paper-reader](../zotero-paper-reader/SKILL.md) for each entry of `reading_queue`, passing the arXiv ID and the existing item_key so saving is skipped.
Deep-read each paper in its own fresh sub-agent following the [sub-agent brief](../zotero-paper-reader/references/subagent-brief.md);
the brief carries no candidates, scores or reviews, so earlier stages cannot pollute the note. The main agent only dispatches, reads back and records progress.
By default deep-read all must-read papers; a short review is not a deep read. When the user asks to deep-read every selected paper, extend to all successfully saved items.
After each save, record the arXiv ID, item_key, note_key, status and failure reason in `{TEMP_DIR}/reading_progress.json`.
Progress must be based on actual writes and read-backs; never mark a reading complete just because the item exists or is queued.

## 5. Resume and report

When the user says "continue", first read the push result and reading progress of the current task and confirm against the actual notes in Zotero,
then retry only unfinished items. If temporary files are lost, recover from today's digest and item notes; if the task cannot be determined, ask for the date.
Report the digest link, the numbers recommended / saved / deep-read, and any failed or unfinished items. Keep existing results and do not lower reading quality because of context length.
