# Zotero Setup

Below, `{SHARED}` is the parent directory of the `references/` folder that holds this file.

## Default: local Zotero 10+

1. Open Zotero and under Settings → Advanced enable "Allow other applications on this computer to communicate with Zotero".
2. `python3 "{SHARED}/zotero_push.py" check` checks the connection and the local library.
3. `python3 "{SHARED}/zotero_push.py" authorize` requests write authorization. Choose "Always Allow" in the Zotero dialog
   to reuse the authorization on later runs; "Allow" is valid for a single write request only. Denying stops the operation.
4. Saving a single paper needs no setup. The daily digest is saved directly into the Daily Papers collection (created if missing); papers go to "Pending" by default, or can be configured to match existing collections.

Local mode needs no cloud API key or user ID; the personal library uses `/api/users/0`.
A group library needs `library_type: "group"` and the group `library_id`, with edit permission.
The program never reads or writes `zotero.sqlite`, never changes Attanger settings and never moves attachments directly.

Local authorization is scoped by Zotero Server ID and saved with permission 600 as `~/.config/zotero-daily-papers/local-auth-*.json`, shared by all installs.
Write authorization can be cleared in Zotero's advanced settings. The cache is kept separate from the cloud one so local/cloud versions are never mixed.
`--dry-run` does not connect to Zotero, show the authorization dialog, download PDFs or change the library; a single-paper preview still fetches arXiv metadata.

## Attachments and Attanger

`attach_pdf: "upload"` (default) downloads the PDF and saves it into the local Zotero. In local mode this upload
only sends the file to localhost; whether Zotero then syncs the file depends on the user's sync settings.
Existing PDFs are not imported again, including linked files managed by Attanger.
Run `attachments --arxiv <ID>` to get the current attachment paths and whether the files exist; prefer an existing local PDF when reading.
Attanger only processes attachments whose file exists "about 1 second after the add event"; the local API creates the attachment item first and uploads the file afterwards,
so it may miss them. The script therefore polls for up to `attanger_wait_seconds` seconds after uploading and reports `attanger.status` in the result:
`moved` (now a linked file, with the new path) / `not_moved` (still in `~/Zotero/storage`; right-click the attachment
in Zotero and use Attanger's "Rename and Move") / `unknown` / `skipped`. Never report `not_moved` as organized.
When Attanger moves a stored attachment it creates a new linked-file attachment and deletes the original, so the attachment key changes; with `moved` the
`pdf_key` in the output is already the new attachment. Attachment keys are not stable identifiers: when you need the PDF later, query live with `attachments --item <KEY>` using the item key.
A failed download is reported and falls back to a PDF link; never treat the link as a downloaded file. In upload mode the next run retries the download,
saves the PDF once it succeeds, and does not stack duplicate link attachments.

New item metadata matches Zotero's "Add Item by Identifier" (arXiv translator): the date is the latest version, `extra` is
`arXiv:ID [primary category]`, `libraryCatalog: arXiv.org`, arXiv subject auto-tags, and the PDF title is `Preprint PDF`,
so the file names Attanger generates (Author - Year - Title) match manual additions.

Pending mode only puts **unfiled** papers into "Pending"; existing items already in any collection keep their collections and are not added to "Pending".

## Configurable settings

Settings of this install: `{SHARED}/config.json` (one per install location, kept on reinstall; `ZDP_CONFIG` can layer one more file on top).

| Key | Default | Description |
|---|---|---|
| language | en | `en` / `zh`: language of generated note titles and localized defaults; env ZDP_LANGUAGE overrides |

| Key (zotero section) | Default | Description |
|---|---|---|
| backend | local | local / web; env ZOTERO_BACKEND overrides |
| local_url | http://127.0.0.1:23119 | only local loopback HTTP addresses are allowed |
| attach_pdf | upload | upload saves the PDF; link only links; none adds nothing |
| attanger_wait_seconds | 10 | seconds to wait after upload and verify Attanger's move; 0 disables the check |
| root_collection | Daily Papers | top-level collection that holds the daily digests (created if missing) |
| classification_mode | pending | pending: everything to Pending; existing: read existing collections and let the Agent decide by meaning |
| pending_collection | Pending (en) / 待定 (zh) | default target in pending mode, and the top-level fallback when existing mode finds no match |
| write_tiers | must, worth | tiers of daily recommendations that are saved |
| max_embedded_images_per_note | 4 | Web mode only; local mode inserts figures as links |

## Optional: Web API mode

Set `ZOTERO_BACKEND=web` together with `ZOTERO_API_KEY` and `ZOTERO_LIBRARY_ID`.
Create a key at https://www.zotero.org/settings/keys with library, notes and write access; uploading attachments needs file access.
Web mode uploads files to Zotero cloud storage and cannot read local linked-file paths.

## Troubleshooting

- Connection failed: keep Zotero open and check the port and the local API setting.
- Missing Server ID: upgrade to Zotero 10+ to use this local write backend.
- 401: the script requests authorization again; 403: check for a denied authorization or library permissions.
- 412: the Zotero instance changed or there is a version conflict; rerun the command to read the new version.
- File not found: fix the linked file's path in Zotero and retry.
- Write requests with an unknown outcome are never retried automatically; rerunning deduplicates first.

Official protocol: https://www.zotero.org/support/dev/web_api/v3/local_api

## Tags and note identification

New items only use plain research direction names and topic tags; `dp` or `dp/...` tags are no longer generated.
Note types are identified by the first level-1 heading of the note: `[Deep Read]`, `[Quick Read]`, `[Daily Review]`, `[Daily Digest]`
(Chinese installs write `【精读】`, `【速读】`, `【每日锐评】`, `【每日推荐】`; both forms are always recognized).
Daily review and digest titles also contain the date, which separates records of different days.

Notes of the same type under the same parent item are deduplicated; daily digests are only looked up in the digest collection and updated by type and date.
Keep the type and date in titles; removing these markers manually breaks later identification.
Legacy tags are only read for compatibility: updating an old note converts it to the title above and removes its old dp tags, keeping other tags.
Historical tags of items or notes in the library are never cleaned up in bulk.

## Switching language or classification mode

`python3 "{SHARED}/zdp_config.py" --set-language en` (or `zh`) sets the language of new notes and localized defaults;
`python3 "{SHARED}/zdp_config.py" --set-classification pending` or `--set-classification existing` sets the classification mode.
These commands only change that key in `{SHARED}/config.json` and keep the rest. Confirm the effective value with `zdp_config.py`;
when using ZDP_CONFIG, also check the value it overrides. Invalid values raise an error instead of silently falling back to the default.
