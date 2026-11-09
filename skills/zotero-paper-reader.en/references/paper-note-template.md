# Deep-Reading Note Template (written as a Zotero child note)

First read the effective config from `zdp_config.py`; the research direction section expands dynamically from `daily_papers.directions`.
For example, when only "Retrieval-Augmented Generation" is configured, discuss only that direction instead of the default direction list.

Write Markdown; `zotero_push.py add-note` converts it to Zotero note HTML. Rules:

- The first line, a level-1 heading, is the note title shown in Zotero: `# [Deep Read] {MethodName}: {Paper title}`.
- Formulas: inline `$...$`, display `$$...$$` (`$$` on its own line or wrapping the whole line both work). Split very long formulas with `\begin{aligned}`.
- Cite the source next to key claims (§3.2, Table 2, Eq. 5). Distinguish the authors' claims, the paper's evidence and your own inferences.

---

```markdown
# [Deep Read] {MethodName}: {Title}

## One-sentence summary

{≤30 words}

## Problem and motivation

- **Problem addressed**: …
- **Limitations of existing methods**: … (name specific methods)
- **Motivation of this paper**: …

## Method

… (describe the paper's method in detail)

## Experimental results

### Setup

- **Benchmarks**: …  **Base models**: …  **Baselines**: …  **Compute / decoding settings**: …

### Main results

{Key numbers from the main table and the gap to the strongest baseline (Table N); a Markdown table is fine}

## Relation to my research directions

- **{configured direction name}**: {specific takeaways / unrelated}
{Repeat in the order of the effective config daily_papers.directions, one entry per configured direction, without adding unconfigured ones.}
- **Experiments to try directly**: 1. … 2. …

## Limitations and open questions

1. {Limitations stated by the authors}
2. {Issues you found + evidence}
3. {Follow-up questions worth pursuing}

## Related work at a glance

- **{Prior work A}** (arXiv/venue): relation —— baseline / improved upon / concurrent competitor
- **{Prior work B}**: …

## Glossary

- **{term}**: one-sentence definition
```
