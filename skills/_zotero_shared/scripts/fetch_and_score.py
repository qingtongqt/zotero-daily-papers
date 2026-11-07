#!/usr/bin/env python3
"""
fetch_and_score.py — Fetch HF Daily/Trending + arXiv papers, keyword-recall, dedup against history.

Default agent mode prepares a request; the host agent scores it in its current session.
Keyword mode selects by recall score and makes no model call.

Usage:
    python3 fetch_and_score.py --output candidates.json [--days N]
    python3 fetch_and_score.py --ranker keyword --output selected.json

Stdout or --output: agent request object, or selected JSON array for keyword mode.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zdp_config import daily_papers_config, load_history, temp_file_path
from agent_ranker import prepare_request

# ── Configuration ──────────────────────────────────────────────────────────

_CONFIG = daily_papers_config()

DIRECTION_GROUPS = _CONFIG["direction_groups"]
NEGATIVE_KEYWORDS = _CONFIG["negative_keywords"]
FOCUS_GROUPS = _CONFIG["focus_groups"]
FOCUS_BONUS = _CONFIG.get("focus_bonus", 3)
QUALITY = _CONFIG.get("quality_boost", {})
ARXIV_CATEGORIES = _CONFIG["arxiv_categories"]
RECALL_MIN_SCORE = _CONFIG["recall_min_score"]
TOP_N = _CONFIG["top_n"]


ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

# ── Scoring ────────────────────────────────────────────────────────────────


def keyword_matches(keyword: str, text: str) -> bool:
    keyword = keyword.casefold()
    if keyword.isascii() and keyword.isalnum():
        return re.search(r"\b" + re.escape(keyword) + r"\b", text) is not None
    return keyword in text


def score_paper(paper: dict, is_trending: bool = False) -> int:
    text = (paper["title"] + " " + paper["abstract"]).lower()
    title_lower = paper["title"].lower()

    # 1. Negative keywords → instant reject
    for neg in NEGATIVE_KEYWORDS:
        if keyword_matches(neg, text):
            return -999

    # 2. Directions are scored separately; the paper takes its best direction.
    #    Within a direction each synonym group scores once (title 3, else abstract 2).
    direction_scores = {}
    for direction, groups in DIRECTION_GROUPS.items():
        s = 0
        for group in groups:
            if any(keyword_matches(kw, title_lower) for kw in group):
                s += 3
            elif any(keyword_matches(kw, text) for kw in group):
                s += 2
        if s:
            direction_scores[direction] = s
    paper["direction_scores"] = direction_scores
    score = max(direction_scores.values(), default=0)

    # 3. Focus topics: each group adds a flat bonus once, and also qualifies a paper on its own
    for group in FOCUS_GROUPS:
        if any(keyword_matches(kw, text) for kw in group):
            score += FOCUS_BONUS

    if not score:
        return -1  # Upvotes alone do not qualify a paper.

    if is_trending:
        upvotes = paper.get("hf_upvotes", 0) or 0
        if upvotes >= 10:
            score += 3
        elif upvotes >= 5:
            score += 2
        elif upvotes >= 2:
            score += 1

    return score


# ── Fetchers ───────────────────────────────────────────────────────────────


def fetch_url(url: str, timeout: int = 30) -> str:
    try:
        req = Request(url, headers={"User-Agent": "zotero-daily-papers/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        print(f"  [WARN] fetch failed {url}: {e}", file=sys.stderr)
        return ""


def _parse_hf_item(item: dict, source: str) -> tuple[str, dict] | None:
    """Parse a single HF API item into (arxiv_id, paper_dict). Returns None on skip."""
    p = item.get("paper", {})
    arxiv_id = p.get("id", "")
    if not arxiv_id:
        return None

    upvotes = p.get("upvotes", 0)

    # Authors
    authors_raw = p.get("authors", [])
    if isinstance(authors_raw, list):
        names = []
        for a in authors_raw:
            if isinstance(a, dict):
                names.append(a.get("name", ""))
            elif isinstance(a, str):
                names.append(a)
        authors = ", ".join(n for n in names if n)
    else:
        authors = str(authors_raw)

    paper = {
        "title": " ".join((p.get("title") or "").split()),
        "authors": authors,
        "affiliations": "",
        "abstract": " ".join((p.get("summary") or "").split()),
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf": f"https://arxiv.org/pdf/{arxiv_id}",
        "date": (p.get("publishedAt") or "")[:10],
        "score": 0,
        "category": "",
        "source": source,
        "hf_upvotes": upvotes,
        "arxiv_id": arxiv_id,
        "comment": None,  # HF has no arXiv comment; filled by attach_comments()
    }

    is_trending = source == "hf-trending"
    paper["score"] = score_paper(paper, is_trending=is_trending)

    if paper["score"] < 0:
        return None

    return arxiv_id, paper


def fetch_hf_papers(start_date=None, end_date=None) -> list[dict]:
    papers = {}  # arxiv_id → paper

    # ── hf-daily: loop each day in range ──
    if start_date and end_date:
        d = start_date
        while d <= end_date:
            date_str = d.isoformat()
            endpoint = f"https://huggingface.co/api/daily_papers?date={date_str}&limit=100"
            print(f"  Fetching hf-daily {date_str}...", file=sys.stderr)
            raw = fetch_url(endpoint)
            if raw:
                try:
                    items = json.loads(raw)
                except json.JSONDecodeError:
                    items = []
                    print(f"  [WARN] bad JSON from hf-daily {date_str}", file=sys.stderr)
                for item in items:
                    result = _parse_hf_item(item, "hf-daily")
                    if result:
                        arxiv_id, paper = result
                        if arxiv_id not in papers or paper["score"] > papers[arxiv_id]["score"]:
                            papers[arxiv_id] = paper
            d += timedelta(days=1)
    else:
        # Legacy single-call (days=1 default)
        endpoint = "https://huggingface.co/api/daily_papers?limit=50"
        print(f"  Fetching hf-daily...", file=sys.stderr)
        raw = fetch_url(endpoint)
        if raw:
            try:
                items = json.loads(raw)
            except json.JSONDecodeError:
                items = []
                print(f"  [WARN] bad JSON from hf-daily", file=sys.stderr)
            for item in items:
                result = _parse_hf_item(item, "hf-daily")
                if result:
                    arxiv_id, paper = result
                    if arxiv_id not in papers or paper["score"] > papers[arxiv_id]["score"]:
                        papers[arxiv_id] = paper

    # ── hf-trending: always single call (not date-dependent) ──
    endpoint = "https://huggingface.co/api/daily_papers?sort=trending&limit=50"
    print(f"  Fetching hf-trending...", file=sys.stderr)
    raw = fetch_url(endpoint)
    if raw:
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            items = []
            print(f"  [WARN] bad JSON from hf-trending", file=sys.stderr)
        for item in items:
            result = _parse_hf_item(item, "hf-trending")
            if result:
                arxiv_id, paper = result
                if arxiv_id not in papers or paper["score"] > papers[arxiv_id]["score"]:
                    papers[arxiv_id] = paper

    result = list(papers.values())
    print(f"  HF: {len(result)} papers after scoring", file=sys.stderr)
    return result


ARXIV_TZ = ZoneInfo("America/New_York")
ARXIV_CUTOFF = dtime(14, 0)  # arXiv closes each announcement batch at 14:00 US Eastern
ARXIV_PAGE_SIZE = 500
ARXIV_MAX_ENTRIES = 10000  # safety stop, far above several days of these categories


def _published(entry) -> datetime | None:
    text = entry.findtext("atom:published", "", ATOM_NS)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def arxiv_window_start(newest: datetime, batches: int) -> datetime:
    """Start of the last `batches` announcement batches ending at the newest visible submission.

    A batch spans submissions between two weekday 14:00 ET cutoffs, so Monday's batch
    reaches back to Friday 14:00. Holidays are ignored (they only cause a small overlap).
    """
    local = newest.astimezone(ARXIV_TZ)
    boundary = datetime.combine(local.date(), ARXIV_CUTOFF, ARXIV_TZ)
    if boundary >= local:
        boundary -= timedelta(days=1)
    found = 0
    while True:
        if boundary.weekday() < 5:
            found += 1
            if found == batches:
                return boundary.astimezone(timezone.utc)
        boundary -= timedelta(days=1)


def fetch_arxiv_entries(days: int) -> list:
    """Page through the newest submissions until the requested batches are fully covered."""
    cats = "+OR+".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
    entries, window_start = [], None
    for start in range(0, ARXIV_MAX_ENTRIES, ARXIV_PAGE_SIZE):
        url = (f"https://export.arxiv.org/api/query?search_query=({cats})"
               f"&sortBy=submittedDate&sortOrder=descending&start={start}&max_results={ARXIV_PAGE_SIZE}")
        page = []
        for attempt in range(2):  # the API occasionally returns an empty page transiently
            xml_text = fetch_url(url, timeout=120)
            try:
                page = ET.fromstring(xml_text).findall("atom:entry", ATOM_NS) if xml_text else []
            except ET.ParseError as e:
                print(f"  [WARN] arXiv XML parse error: {e}", file=sys.stderr)
            if page:
                break
            time.sleep(3)
        if not page:
            if not entries:
                return []
            print("  [WARN] arXiv paging stopped early; the oldest part of the window may be missing",
                  file=sys.stderr)
            break
        if window_start is None:
            newest = _published(page[0])
            if newest is None:
                print("  [WARN] arXiv entry without a date; cannot determine the window", file=sys.stderr)
                return []
            window_start = arxiv_window_start(newest, days)
            print(f"  arXiv window: submissions since {window_start:%Y-%m-%d %H:%M} UTC ({days} batch(es))",
                  file=sys.stderr)
        entries.extend(e for e in page if (_published(e) or window_start) >= window_start)
        oldest = _published(page[-1])
        if len(page) < ARXIV_PAGE_SIZE or (oldest and oldest < window_start):
            break
        time.sleep(3)  # arXiv API etiquette
    else:
        print(f"  [WARN] arXiv safety stop at {ARXIV_MAX_ENTRIES} entries", file=sys.stderr)
    return entries


def fetch_arxiv_papers(days: int = 1) -> list[dict]:
    print(f"  Fetching arXiv (latest {days} announcement batch(es))...", file=sys.stderr)
    papers = []
    for entry in fetch_arxiv_entries(days):
        title_el = entry.find("atom:title", ATOM_NS)
        summary_el = entry.find("atom:summary", ATOM_NS)
        published_el = entry.find("atom:published", ATOM_NS)
        id_el = entry.find("atom:id", ATOM_NS)

        if title_el is None or summary_el is None:
            continue

        title = " ".join(title_el.text.split())
        abstract = " ".join(summary_el.text.split())
        entry_url = id_el.text.strip() if id_el is not None else ""
        date = published_el.text[:10] if published_el is not None else ""
        arxiv_id = entry_url.split("/abs/")[-1] if "/abs/" in entry_url else ""

        author_els = entry.findall("atom:author", ATOM_NS)
        names = []
        affiliations = set()
        for a in author_els:
            name_el = a.find("atom:name", ATOM_NS)
            if name_el is not None and name_el.text:
                names.append(name_el.text.strip())
            for aff_el in a.findall("arxiv:affiliation", ATOM_NS):
                if aff_el.text and aff_el.text.strip():
                    affiliations.add(aff_el.text.strip())

        cat_el = entry.find("arxiv:primary_category", ATOM_NS)
        category = cat_el.get("term", "") if cat_el is not None else ""
        comment_el = entry.find("arxiv:comment", ATOM_NS)
        comment = " ".join(comment_el.text.split()) if comment_el is not None and comment_el.text else ""

        papers.append({
            "title": title,
            "authors": ", ".join(names),
            "affiliations": ", ".join(sorted(affiliations)) if affiliations else "",
            "abstract": abstract,
            "url": entry_url,
            "pdf": f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else "",
            "date": date,
            "score": 0,
            "category": category,
            "source": "arxiv",
            "arxiv_id": re.sub(r"v\d+$", "", arxiv_id),
            "comment": comment,
        })

    scored = []
    for p in papers:
        p["score"] = score_paper(p)
        if p["score"] >= 0:
            scored.append(p)

    print(
        f"  arXiv: {len(scored)} papers after scoring (from {len(papers)} in window)",
        file=sys.stderr,
    )
    return scored


# ── Quality signal ─────────────────────────────────────────────────────────

_NOT_ACCEPTED = re.compile(r"under review|submitted to|in submission|under submission|rejected", re.I)
_ACCEPTED = re.compile(r"\baccepted\b|to appear", re.I)


def quality_multiplier(comment: str | None) -> tuple[float, str]:
    """Venue named in the arXiv comment -> venue multiplier; otherwise 'accepted' -> smaller one."""
    if not comment or _NOT_ACCEPTED.search(comment):
        return 1.0, ""
    for venue in QUALITY.get("venues", []):
        if re.search(r"\b" + re.escape(venue) + r"\b", comment, re.I):
            if re.search(r"\bworkshops?\b", comment, re.I):
                return QUALITY.get("accepted_multiplier", 1.0), f"{venue} workshop"
            return QUALITY.get("venue_multiplier", 1.0), venue
    if _ACCEPTED.search(comment):
        return QUALITY.get("accepted_multiplier", 1.0), "accepted"
    return 1.0, ""


def attach_comments(hf_papers: list[dict], arxiv_papers: list[dict]) -> None:
    """HF papers carry no arXiv comment: reuse the arXiv listing, batch-fetch the rest."""
    known = {p["arxiv_id"]: p.get("comment", "") for p in arxiv_papers}
    missing = list(dict.fromkeys(p["arxiv_id"] for p in hf_papers if p["arxiv_id"] not in known))
    for start in range(0, len(missing), 100):
        chunk = missing[start:start + 100]
        xml_text = fetch_url("https://export.arxiv.org/api/query?id_list="
                             + ",".join(chunk) + f"&max_results={len(chunk)}", timeout=60)
        try:
            entries = ET.fromstring(xml_text).findall("atom:entry", ATOM_NS) if xml_text else []
        except ET.ParseError:
            entries = []
        for entry in entries:
            aid = re.sub(r"v\d+$", "", (entry.findtext("atom:id", "", ATOM_NS)).split("/abs/")[-1])
            known[aid] = " ".join((entry.findtext("arxiv:comment", "", ATOM_NS)).split())
        if start + 100 < len(missing):
            time.sleep(3)  # arXiv API etiquette
    for p in hf_papers:
        p["comment"] = known.get(p["arxiv_id"], "")


# ── Merge & Dedup ──────────────────────────────────────────────────────────


def extract_arxiv_id(url: str) -> str:
    m = re.search(r"(\d{4}\.\d{4,5})", url)
    return m.group(1) if m else ""


def merge_and_dedup(
    hf_papers: list[dict],
    arxiv_papers: list[dict],
    target_date,
    days: int = 1,
    top_n: int = TOP_N,
) -> list[dict]:
    is_weekend = target_date.weekday() >= 5

    # ── merge by arXiv ID, keep higher score ──
    by_id: dict[str, dict] = {}
    for p in hf_papers + arxiv_papers:
        aid = extract_arxiv_id(p["url"])
        if not aid:
            continue
        if aid not in by_id or p["score"] > by_id[aid]["score"]:
            by_id[aid] = p

    print(f"  Merged: {len(by_id)} unique papers", file=sys.stderr)

    # ── quality multiplier from the arXiv comment ──
    boosted = 0
    for p in by_id.values():
        multiplier, label = quality_multiplier(p.get("comment"))
        if multiplier != 1.0:
            p["keyword_base_score"] = p["score"]
            p["score"] = round(p["score"] * multiplier, 2)
            p["quality"] = label
            boosted += 1
    if boosted:
        print(f"  Quality boost applied to {boosted} papers", file=sys.stderr)

    if days > 1:
        # ── multi-day mode: skip history dedup ──
        # User explicitly wants to see all N days, don't filter out previously recommended
        print(f"  Multi-day mode (days={days}): skipping history dedup", file=sys.stderr)
        candidates = [p for p in by_id.values() if p["score"] >= RECALL_MIN_SCORE]
        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[:top_n]
        print(f"  Final: {len(top)} papers (top_n={top_n})", file=sys.stderr)
        return top

    # ── single-day mode: history dedup as before ──
    history = load_history()
    history_ids: dict[str, str] = {}  # id → earliest date
    for h in history:
        hid, hdate = h.get("id", ""), h.get("date", "")
        if hid and hdate:
            if hid not in history_ids or hdate < history_ids[hid]:
                history_ids[hid] = hdate

    # ── cross-day dedup ──
    deduped: dict[str, dict] = {}
    removed = 0
    for aid, p in by_id.items():
        if aid in history_ids:
            # Weekend: keep trending with upvotes >= 5
            if is_weekend and p.get("source") == "hf-trending" and (p.get("hf_upvotes") or 0) >= 5:
                p["is_re_recommend"] = True
                p["last_recommend_date"] = history_ids[aid]
                deduped[aid] = p
            else:
                removed += 1
        else:
            deduped[aid] = p

    # Mark any remaining that appear in history
    for aid, p in deduped.items():
        if aid in history_ids and not p.get("is_re_recommend"):
            p["is_re_recommend"] = True
            p["last_recommend_date"] = history_ids[aid]

    print(f"  After history dedup: {len(deduped)} (removed {removed})", file=sys.stderr)

    # ── filter + sort ──
    candidates = [p for p in deduped.values() if p["score"] >= RECALL_MIN_SCORE]
    candidates.sort(key=lambda x: x["score"], reverse=True)

    top = candidates[:top_n]
    print(f"  Final: {len(top)} papers", file=sys.stderr)
    return top


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=1, help="Number of days to fetch (default: 1)")
    parser.add_argument("--output", type=Path, help="Write UTF-8 JSON directly, avoiding shell redirection encoding")
    parser.add_argument("--ranker", choices=("agent", "keyword"), help="Override the configured ranking backend")
    parser.add_argument("--candidates-output", type=Path, help="Save the raw shortlist for reproducible comparison")
    parser.add_argument("--usage-output", type=Path, help="Save ranking calls, scores and actual API token usage")
    args = parser.parse_args()

    target_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now().date()
    )
    days = max(1, args.days)
    start_date = target_date - timedelta(days=days - 1)
    if type(TOP_N) is not int or TOP_N < 1:
        parser.error("daily_papers.top_n must be a positive integer")
    top_n = TOP_N * days
    backend = args.ranker or _CONFIG["ranking"]["backend"]
    if backend not in ("agent", "keyword"):
        parser.error("daily_papers.ranking.backend must be agent or keyword")
    pool_size = _CONFIG["candidate_pool_size"]
    if type(pool_size) is not int or pool_size < TOP_N:
        parser.error("candidate_pool_size must be an integer greater than or equal to top_n")

    usage_path = args.usage_output or temp_file_path("daily_papers_ranking.json")
    output_paths = [path.resolve() for path in (args.output, args.candidates_output, usage_path) if path is not None]
    if len(set(output_paths)) != len(output_paths):
        parser.error("Output, raw candidates, and ranking report must be different files")

    is_weekend = target_date.weekday() >= 5
    print(
        f"[fetch_and_score] {target_date} ({'weekend' if is_weekend else 'weekday'})"
        + (f", days={days} [{start_date} ~ {target_date}], top_n={top_n}" if days > 1 else ""),
        file=sys.stderr,
    )

    hf_papers = fetch_hf_papers(start_date, target_date)
    arxiv_papers = fetch_arxiv_papers(days)
    attach_comments(hf_papers, arxiv_papers)
    candidates = merge_and_dedup(
        hf_papers, arxiv_papers, target_date, days=days, top_n=pool_size * days,
    )
    if args.candidates_output:
        args.candidates_output.write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if backend == "agent":
        try:
            result = prepare_request(candidates, _CONFIG, top_n)
        except ValueError as error:
            parser.error(str(error))
        result.update(date=target_date.isoformat(), days=days)
        report = {"backend": "agent", "status": "awaiting_agent", "run_id": result["run_id"],
                  "candidate_count": len(candidates), "selected_count": None,
                  "requested_top_n": top_n, "usage": None, "usage_complete": False}
        print(f"  Prepared {len(candidates)} candidates for the host agent; selection is pending.", file=sys.stderr)
    else:
        result = candidates[:top_n]
        report = {"backend": "keyword", "status": "complete", "candidate_count": len(candidates),
                  "selected_count": len(result), "usage": {"input_tokens": 0, "output_tokens": 0}}
    report["date"] = target_date.isoformat()
    report["days"] = days
    usage_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if backend != "agent":
        print(f"  Selected {len(result)}/{top_n} papers with {backend}; usage: {usage_path}", file=sys.stderr)

    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
