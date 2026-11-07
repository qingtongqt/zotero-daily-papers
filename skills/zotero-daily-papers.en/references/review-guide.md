# Review Guide

## Persona

Review to the standard of a senior researcher; the directions of interest are those in the effective config `daily_papers.directions`.
Before starting, read each direction's id, name and keywords from the `zdp_config.py` output; never substitute the template's default directions for the user's config.
Zero tolerance for filler papers, but every criticism needs evidence. Praise specifically (which number, which design), criticize even more specifically (which assumption,
which control, which claim).

## Scoring rubric

The score only measures **relevance to the directions**, not paper quality.

| Score | Meaning | Example |
|---|------|------|
| 4 | Core problem/method/benchmark of a direction | Directly solves a core research question of a configured direction |
| 3 | Directly relevant, but a broader or more analytical contribution | Goal directly related to a configured direction, but a wider scope |
| 2 | Reusable component / background | A component, benchmark or background method usable for a configured direction |
| 1 | Only shares terminology | Uses a related model as a tool without studying the configured direction itself |
| 0 | Unrelated | — |

Cross-direction weighting: a paper that substantially hits two directions and would score 3 or more should preferably get 4.

## Triage rules

- 🔥 **Must read (must)**: scores 4 with solid-looking method/evidence, or hits an intersection of directions. Usually 1–3 per day; fewer is better than padding.
- 👀 **Worth a look (worth)**: scores 3–4 with limited or incremental evidence; or scores 2 but one design is worth borrowing.
- 💤 **Skip (skip)**: passed scoring but on closer look is weakly related, purely incremental or obvious filler. Give a one-sentence reason.

## Iron rule: stick to facts

Evidence: title, abstract, enriched fields (`method_summary`, `section_headers`, `captions`, `method_names`,
`affiliations`), and the arXiv `comment` (venues recognized by the script are in the `quality` field).
Acceptance information can inform triage on whether a paper is solid, but never replaces judging the method and evidence themselves;
if the comment does not state an acceptance, do not guess.

Forbidden:
- Inventing flaws the paper does not have ("no ablation", "no baseline") — unless section/table titles really show it is missing, and say what shows it;
- Calling something a "reskin/copy" without concrete method-level evidence;
- Stating uncertain facts with certainty. When unsure, write "not mentioned in the abstract" or "needs the full text to confirm".

Do: name the prior work compared against or borrowed from using `method_names`; infer experimental coverage from section and table titles;
question whether the title overclaims and whether the contribution is incremental; even for a strong paper, find at least one point worth probing.

Recommendation levels only use the triage 🔥 Must read / 👀 Worth a look / 💤 Skip; do not add separate verdict labels. To flag an issue, you may add one issue label after the level:
⚠️ flawed but right direction · 🫠 incremental · 💀 filler · 🤡 clickbait. Add none when there is no clear issue.

## Review card (`review_markdown`, one per paper)

The Zotero note title is the first line, so the first line is always a level-1 heading. If the paper title already starts with the method name (e.g. `VIGOR: ...`),
write `# [Daily Review] {paper title}` directly without another method-name prefix.

```markdown
# [Daily Review] {MethodName}: {Paper title}

**{🔥 Must read | 👀 Worth a look}{ · issue label, optional}** · {direction names, may be several} · {YYYY-MM-DD}

- **Authors**: {authors; when long keep the first 6 + et al.}
- **Affiliations**: {affiliations; "unknown" if none}
- **Source**: {📰 HF Daily ⬆️N | 📈 HF Trending ⬆️N | 📄 arXiv search}
- **Accepted**: {acceptance info from the comment, e.g. "NeurIPS 2026"}
- **Links**: [arXiv](https://arxiv.org/abs/ID) · [PDF](https://arxiv.org/pdf/ID)

> ⏪ Re-recommended: recommended on {last_recommend_date}

![Figure 1](figure_url)

## Core method
{3–5 sentences: input/output; key components (architecture, loss, training/decoding strategy); the essential difference from existing methods. Based on method_summary, not a restatement of the abstract.}

## Comparisons and influences
- **Baselines**: {specific method names}
- **Builds on**: {specific prior work}

## Verdict
{One clear judgment: worth a deep read or not, what is most worth learning, whether it is worth reproducing and why.}
```

The following three lines are conditional; omit the whole line when the condition does not hold:

- **Accepted**: only when `quality` is non-empty.
- **⏪ Re-recommended**: only when `is_re_recommend=true`.
- **Figure 1**: only when `figure_url` is non-empty; never invent an image URL.

## Digest (`digest_markdown`)

```markdown
# 🔪 Daily Review {YYYY-MM-DD}

{2–3 sentences: overall quality today; which direction is booming and where the filler is; call out direct collisions with recent hot topics.}

## Triage

| Level | Papers |
|------|------|
| 🔥 Must read | [MethodName](zotero:ID) (one-line reason) · [MethodName](zotero:ID) (…) |
| 👀 Worth a look | … |
| 💤 Skip | … |

## By direction

### {configured direction name}
- [MethodName](zotero:ID): one-line core idea + one-line judgment {level emoji}{issue label, optional}

{Repeat the section for each direction in config order; for a direction without papers write "Nothing worth recommending today".}

## Excluded

- {title}: {reason}

## Trend in one line
{An opinionated judgment}
```

Omit the "Excluded" section when no paper was excluded.

Link text uses the method abbreviation (the name before the colon in the title, or the first entry of `method_names`), not the full title.
