# zotero-daily-papers

[中文](README.md) | English

Let an AI agent (such as Claude Code or Codex) pick papers from arXiv every day, read them, and organize the results in your local Zotero:
a daily recommendation digest, paper items with PDFs, review notes, and deep-reading notes for must-read papers, all stored in Zotero.

Just say "today's paper recommendations" in the agent chat and the rest happens automatically.

## Features

- **Daily recommendations**: fetch new arXiv papers and Hugging Face trending papers → filter and score by your research directions → deduplicate against your Zotero library → review each paper.
- **Automatic saving**: save today's digest first, then the selected papers (with PDF and review child note), and finally backfill the item links in the digest.
- **Deep-reading notes**: read the full text of must-read papers and write a note with method, formulas, main results table and key figures, saved as a child note.
- **Standalone use**: you can also just save a paper, or just deep-read a paper.
- **Safe to rerun**: reruns deduplicate first and reuse existing items and notes instead of creating new ones; your manual collections and attachments are kept.
- **Local first**: writes through Zotero 10's local API; no cloud API key needed, and `zotero.sqlite` is never read or written directly.
- **Attanger compatible**: works with the Attanger plugin and verifies whether PDFs were moved to your file directory.

## Workflow

1. **Fetch**: the latest arXiv announcement batch, plus Hugging Face Daily / Trending.
2. **Filter and score**: keyword prefilter, then the agent reads abstracts and scores by research direction.
3. **Deduplicate**: against existing Zotero items.
4. **Review**: the agent reviews each paper as must read, worth a look, or skip.
5. **Save the daily digest**: into the `Daily Papers` collection.
6. **Save papers**: into "Pending" or a matching existing collection.
7. **Deep-read**: write deep-reading notes for must-read papers as child notes.

Scoring, reviewing and deep reading are all done by the agent in the current conversation; the scripts never call any paid model API.

## Requirements

| Dependency | Notes |
|---|---|
| Python 3.10+ | standard library only, no `pip install` |
| `curl` | needed by the enrichment script for arXiv HTML, metadata pages and PDFs |
| Zotero 10+ | with the local API enabled (see below) |
| Claude Code or Codex | or any AI agent that can run local commands and supports skills |
| [Attanger](https://github.com/MuiseDestiny/zotero-attanger) (optional) | renames and moves PDFs automatically |
| `pdftotext` (optional) | extracts affiliations from the first PDF page; on macOS `brew install poppler` |

## Quick start

### 1. Get the project

```bash
git clone https://github.com/qingtongqt/zotero-daily-papers.git
cd zotero-daily-papers
```

Keep the project directory: installed skills link here, and the shared scripts run from here.

### 2. Enable the Zotero local API

Open Zotero → **Settings → Advanced** and check "Allow other applications on this computer to communicate with Zotero".

Then check the connection and request write authorization:

```bash
python3 skills/_zotero_shared/zotero_push.py check       # can we reach the local Zotero?
python3 skills/_zotero_shared/zotero_push.py authorize   # Zotero shows an authorization dialog
```

Choose **"Always Allow"** in the dialog so later runs do not ask again.

### 3. Install the skills

The installer symlinks the three skills and the shared directory into the agent's skill directory without copying code; rerunning is safe.

Choose the language with `--lang`: `zh` (Chinese, default) or `en` (English). The language decides the skill instructions, the reviews and notes the agent writes,
the note title markers (`[Deep Read]` or `【精读】`) and the default pending collection name (`Pending` or `待定`), and is saved to your personal config.

**Claude Code**

```bash
python3 install.py --target ~/.claude/skills --lang en
```

The skills are available in the current session. Trigger them in natural language, or call `/zotero-daily-papers` manually.

**Codex**

```bash
python3 install.py --target ~/.agents/skills --lang en
```

Restart Codex if the skills do not show up. Trigger them in natural language, call `$zotero-daily-papers`,
or pick it via `/skills`. Older Codex versions use the `~/.codex/skills` directory.

> If you use both, run both commands. They link to the same project directory, so updating the code happens in one place.
> To install by copying, copy the three skills and `_zotero_shared/` from `skills-en/` with `cp -rL` (to resolve the links inside) into the host skill directory, keeping the four directories side by side, then run `python3 skills/_zotero_shared/zdp_config.py --set-language en`.
>
> To switch an existing install to another language: `python3 install.py --target <dir> --lang zh --update`.

If your agent does not support skill directories, ask it in the chat to read `skills-en/zotero-daily-papers/SKILL.md`.

### 4. Set your research directions

The default config targets three directions: MLLM hallucination, LLM post-training and LLM architecture. To use your own, create a personal config:

```bash
python3 skills/_zotero_shared/zdp_config.py --init
```

Then edit `~/.config/zotero-daily-papers/config.json`; see [Research directions](#research-directions) below.

### 5. Use it

Just say in the agent chat:

| You say | What happens |
|---|---|
| "Today's paper recommendations" | full pipeline: fetch, filter, review, save, deep-read must-read papers |
| "Recommend papers from the past three days" | same, for the last 3 days (also "past week") |
| "Continue the last recommendation" | check the actual Zotero state and retry only the unfinished parts |
| "Save 2311.16922 to Zotero" | save the paper and PDF only, no deep reading |
| "Deep-read 2311.16922" / "Take a quick look at this paper" | read the paper and save a note under its item |

## Results in Zotero

| Content | Location |
|---|---|
| Daily digest | top-level collection `Daily Papers` (created if missing) |
| Selected papers | top-level collection `Pending` by default; can match existing collections instead |
| PDF | attachment of the paper item; whether Attanger moves it depends on the plugin settings and event handling, and the script checks the result |
| Daily reviews, deep-reading notes | child notes of the paper item |

Note types are identified by markers in their titles, such as `[Daily Digest]`, `[Daily Review]`, `[Deep Read]`, `[Quick Read]`; daily notes also carry the date.
Do not remove these markers, or reruns cannot recognize existing notes. Chinese markers (`【每日推荐】` etc.) are recognized too, so switching language keeps deduplication working.

## Configuration

The defaults live in [`config.default.json`](skills/_zotero_shared/config.default.json); do not edit it directly.
Personal settings go in `~/.config/zotero-daily-papers/config.json`, containing only the keys you override:

```json
{
  "language": "en",
  "zotero": {
    "classification_mode": "pending",
    "attach_pdf": "upload"
  },
  "daily_papers": {
    "top_n": 10
  }
}
```

Merge rule: objects merge recursively; lists (such as `directions`) are replaced as a whole.
Show the effective config:

```bash
python3 skills/_zotero_shared/zdp_config.py
```

### Research directions

Research directions decide which papers are selected and are written to items as tags, but they do **not** decide which collection a paper goes to.

```json
{
  "daily_papers": {
    "directions": [
      {
        "id": "rag",
        "name": "Retrieval-Augmented Generation",
        "keywords": [["retrieval-augmented", "rag"], "dense retrieval", ["reranker", "reranking"]]
      },
      {
        "id": "llm-agent",
        "name": "LLM Agents",
        "keywords": [["llm agent", "llm agents", "language agent"], ["tool use", "tool learning", "function calling"], "agentic search"]
      }
    ],
    "negative_keywords": ["medical imaging", "protein"],
    "quality_boost": {
      "venues": ["NeurIPS", "ICLR", "ACL", "EMNLP"],
      "venue_multiplier": 1.5,
      "accepted_multiplier": 1.2
    },
    "arxiv_categories": ["cs.CL", "cs.IR"],
    "top_n": 10
  }
}
```

- `keywords`: direction terms. A paper must hit at least one direction term or focus term to enter prefilter scoring. Each entry is a single term or a group of synonyms (e.g. `["rlvr", "verifiable reward"]`), and a group scores at most once: 3 points for a title hit, 2 for an abstract-only hit. Each direction is scored separately and a paper takes its best direction; scores of different directions do not add up. The agent then scores by reading the abstract.
- `focus_keywords`: topics of special interest, written like `keywords` (synonym groups allowed). Each group hit adds `focus_bonus` (default 3) to the total; a paper hitting only focus terms can also enter the prefilter.
- `quality_boost`: reads the arXiv comment field. A venue from `venues` (e.g. `Accepted to NeurIPS 2026`) multiplies the total by `venue_multiplier` (default 1.5); a bare Accepted / To appear or a workshop multiplies by `accepted_multiplier` (default 1.2); Under review, Submitted to, etc. get no boost.
- `negative_keywords`: a hit in title or abstract excludes the paper, to filter out application areas you do not care about.
- `arxiv_categories`: which arXiv categories to fetch.

### Classification mode

There are two modes for which Zotero collection a paper goes to:

| Mode | Behavior |
|---|---|
| `pending` (default) | new papers all go to "Pending" for you to organize later |
| `existing` | the agent reads your collection structure and matches the best collection by content; only papers without a match go to "Pending" |

```bash
python3 skills/_zotero_shared/zdp_config.py --set-classification existing
```

In both modes, papers already in some collection keep it and are not added to "Pending".

### Common settings

| Key (top level) | Default | Description |
|---|---|---|
| `language` | `zh` | `zh` or `en`; set by `install.py --lang`, or change with `zdp_config.py --set-language` |

| Key (`zotero` section) | Default | Description |
|---|---|---|
| `root_collection` | `Daily Papers` | top-level collection holding the daily digests |
| `pending_collection` | `Pending` (`待定` in Chinese) | where unclassified papers go |
| `classification_mode` | `pending` | `pending` or `existing` |
| `attach_pdf` | `upload` | `upload` downloads the PDF; `link` only adds a link; `none` adds nothing |
| `attanger_wait_seconds` | `10` | seconds to wait for Attanger after uploading a PDF; `0` disables the check |
| `write_tiers` | `["must", "worth"]` | which recommendation tiers are saved |

| Key (`daily_papers` section) | Default | Description |
|---|---|---|
| `top_n` | `10` | maximum number of papers selected per day |
| `candidate_pool_size` | `30` | candidates handed to the agent after keyword prefiltering |
| `recall_min_score` | `3` | minimum keyword prefilter score |
| `ranking.min_score` | `2.0` | selection threshold of the agent's relevance score (0–4) |
| `focus_bonus` | `3` | points added per focus group hit |

All settings, Web API mode and troubleshooting are in [Zotero setup](skills-en/_zotero_shared/references/zotero-setup.md).

## About Attanger

With Attanger installed, the script waits a few seconds after uploading a PDF and reports the PDF status:

- `moved`: Attanger moved it to your file directory.
- `not_moved`: still in the Zotero storage directory. Right-click the attachment in Zotero → Attanger → Rename and Move.

If you later change a paper's collection in Zotero, run Attanger's "Rename and Move" on the PDF again afterwards.

## Command-line reference

Daily use only needs the agent chat. These commands are for debugging or manual operation:

```bash
# Check the connection, request authorization
python3 skills/_zotero_shared/zotero_push.py check
python3 skills/_zotero_shared/zotero_push.py authorize

# Save a single paper (pending mode)
python3 skills/_zotero_shared/zotero_push.py upsert-paper --arxiv 2311.16922

# Save into specific collections (list collections and their keys first)
python3 skills/_zotero_shared/zotero_push.py collections
python3 skills/_zotero_shared/zotero_push.py upsert-paper --arxiv 2311.16922 --collections <KEY> --classification-reason "reason"

# Find items, show real attachment paths and existing notes
python3 skills/_zotero_shared/zotero_push.py find --arxiv 2311.16922
python3 skills/_zotero_shared/zotero_push.py attachments --arxiv 2311.16922
python3 skills/_zotero_shared/zotero_push.py notes --arxiv 2311.16922

# Preview only, without changing the library
python3 skills/_zotero_shared/zotero_push.py --dry-run upsert-paper --arxiv 2311.16922
```

## Project structure

```text
zotero-daily-papers/
├── skills/                            # Chinese skills and all code
│   ├── zotero-daily-papers/           # entry point, recommendation scripts and review guide
│   ├── zotero-paper-save/             # single-paper saving and classification
│   ├── zotero-paper-reader/           # reading workflow and note template
│   └── _zotero_shared/                # Zotero scripts, default config and setup notes
├── skills-en/                         # English skill instructions (scripts link to skills/, one shared codebase)
├── tests/
└── install.py                         # skill installer (--lang zh|en)
```

## Data and privacy

- Personal config, local authorization and caches are stored in `~/.config/zotero-daily-papers/` (authorization files have permission 600).
- Intermediate files go to the system temp directory; set `ZDP_TEMP_DIR` to choose another.
- Fetching accesses arXiv and the Hugging Face Daily/Trending APIs; enrichment accesses arXiv pages and PDFs; local saving accesses localhost.
- Web API mode also accesses the Zotero cloud and the file upload URLs it returns; embedding images in that mode also accesses the image URLs in notes.
- When the agent fetches full texts, project pages or related material, it may access those sites as well.
