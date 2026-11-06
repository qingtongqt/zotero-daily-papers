#!/usr/bin/env python3
"""Batch-enrich arXiv papers with metadata from HTML/abs pages.

Usage:
    # Linux/Mac
    cat /tmp/daily_papers_selected.json | python3 enrich_papers.py > /tmp/daily_papers_enriched.json

    # Windows (powershell cmd compatible)
    python3 enrich_papers.py input.json output.json

    # Cross-platform (auto-detect paths)
    python3 enrich_papers.py

Input:  JSON array via stdin or auto-detected file
Output: JSON array via stdout or file with enriched fields added

Architecture:
    - asyncio + subprocess curl for concurrent HTTP requests
    - Semaphore(10) to avoid hammering arXiv
    - Pure regex HTML parsing (no WebFetch / no external deps)
    - Per-request timeout via curl --max-time (no Python-level per-paper timeout)
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_zotero_shared"))

from zdp_config import temp_file_path

SEMAPHORE_LIMIT = 10
CURL_TIMEOUT = 30

# ── Stop words for method_names extraction ──────────────────────────────────
METHOD_STOP = {
    # Section headings
    "Abstract", "Introduction", "Method", "Methods", "Methodology",
    "Results", "Conclusion", "Conclusions", "Discussion", "Experiments",
    "Experiment", "Evaluation", "Background", "Appendix", "Supplementary",
    "References", "Related", "Overview", "Preliminaries", "Framework",
    "Acknowledgements", "Acknowledgments",
    # Conferences / venues
    "CVPR", "ICCV", "ECCV", "NeurIPS", "ICML", "ICLR", "IEEE", "AAAI",
    "IJCAI", "SIGCHI", "SIGGRAPH", "ICRA", "IROS", "CoRL", "RSS",
    "WACV", "BMVC", "ACCV", "MICCAI", "ACL", "EMNLP", "NAACL",
    # Common abbreviations (not method names)
    "RGB", "GPU", "CPU", "TPU", "CNN", "MLP", "SGD", "ADAM", "GAN",
    "RNN", "LSTM", "GRU", "API", "URL", "HTML", "PDF", "JSON", "XML",
    "FPS", "IoU", "MAP", "FID", "PSNR", "SSIM", "LPIPS", "MSE", "MAE",
    "BCE", "CE", "KL", "GNN", "VAE", "ELBO", "EM",
    "SoTA", "SOTA", "TODO", "NOTE", "TBD",
    # Generic terms
    "Table", "Figure", "Section", "Eq", "Equation", "Algorithm",
    "Step", "Phase", "Stage", "Layer", "Block", "Module", "Head",
    "Loss", "Input", "Output", "Data", "Model", "Network",
    "Training", "Testing", "Inference", "Baseline", "Ablation",
    # Roman numerals
    "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",
    # Common LaTeX / HTML artifacts
    "LaTeX", "BibTeX", "ArXiv",
}

# ── Institution keywords for HTML affiliation extraction ────────────────────
INST_KEYWORDS = [
    "university", "universite", "università", "universität",
    "institute", "laboratory", "college", "school of",
    "center for", "centre for", "academy", "polytechnic",
    "department of", "faculty of", "research center", "research centre",
    "national lab",
    "google", "nvidia", "meta ai", "meta platforms", "microsoft",
    "deepmind", "openai", "alibaba", "tencent", "baidu", "bytedance",
    "amazon", "apple", "samsung", "huawei", "intel", "qualcomm",
    "adobe", "salesforce", "ibm research", "uber", "waymo", "toyota",
    "sony", "bosch", "damo academy",
    "mit ", "csail", "stanford", "berkeley", "cmu", "caltech",
    "eth zurich", "eth zürich", "epfl", "kaist", "inria", "mpi ",
    "fair ", "max planck", "cnrs",
    "tsinghua", "peking", "westlake", "hkust", "hku ", "fudan",
    "sjtu", "zju", "nju", "ustc", "cuhk", "shanghaitech",
    "chinese academy", "shanghai ai", "nanjing university",
    "nankai", "south china",
]


# ══════════════════════════════════════════════════════════════════════════════
# HTTP helpers
# ══════════════════════════════════════════════════════════════════════════════

async def curl_fetch(url: str, sem: asyncio.Semaphore, timeout: int = CURL_TIMEOUT,
                     retries: int = 3) -> str:
    """Fetch URL content using curl subprocess with retry. Returns empty string on failure."""
    for attempt in range(1, retries + 1):
        async with sem:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "curl", "-sL", "--max-time", str(timeout), url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout + 5)
                content = stdout.decode("utf-8", errors="replace") if stdout else ""
                if content:
                    return content
            except (asyncio.TimeoutError, Exception) as e:
                print(f"  [curl] attempt {attempt}/{retries} failed {url}: {e}", file=sys.stderr)
        if attempt < retries:
            await asyncio.sleep(3 * attempt)  # 3s, 6s
    return ""



# ══════════════════════════════════════════════════════════════════════════════
# HTML regex extractors
# ══════════════════════════════════════════════════════════════════════════════

def strip_tags(html: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", html)


def extract_figure_url(html: str, arxiv_id: str) -> str:
    """Extract the first non-icon figure image URL from HTML."""
    figures = re.findall(r"<figure[^>]*>.*?<img[^>]+src=[\"']([^\"'>]+)[\"']", html, re.DOTALL)
    skip_words = ["icon", "logo", "badge", "inline", "orcid", "creative"]
    for fig in figures:
        if any(skip in fig.lower() for skip in skip_words):
            continue
        url = fig
        if url.startswith("/"):
            url = "https://arxiv.org" + url
        elif not url.startswith("http"):
            if re.match(r"\d{4}\.\d{4,5}v\d+/", url):
                url = "https://arxiv.org/html/" + url
            else:
                url = f"https://arxiv.org/html/{arxiv_id}/" + url
        return url
    return ""


def extract_authors_html(html: str) -> list[str]:
    """Extract authors from ltx_personname spans."""
    matches = re.findall(r'class="ltx_personname"[^>]*>(.*?)</span>', html, re.DOTALL)
    authors = []
    for m in matches:
        name = strip_tags(m).strip()
        # Skip if it looks like an affiliation or footnote
        if name and len(name) < 80 and not any(kw in name.lower() for kw in ["university", "institute", "department"]):
            authors.append(name)
    return authors


def extract_affiliations_html(html: str) -> list[str]:
    """Extract affiliations from HTML paper using multiple strategies."""
    affils = set()

    # Strategy 1: structured class elements (ltx_role_affil, ltx_contact)
    # Search up to abstract or first 80k chars (some pages have long headers)
    abstract_pos = html.find("ltx_abstract")
    search_end = abstract_pos if abstract_pos > 0 else min(len(html), 80000)
    search_region = html[:search_end]
    for cls in ("ltx_role_affil", "ltx_contact"):
        for m in re.finditer(
            rf'class="[^"]*{cls}[^"]*"[^>]*>(.*?)</(?:span|div|p|td)',
            search_region, re.DOTALL
        ):
            text = strip_tags(m.group(1)).strip(" ,;.")
            if text and 3 < len(text) < 500:
                affils.add(text)

    # Strategy 2: header region plain text (between <article> and ltx_abstract)
    article_start = html.find("<article")
    abstract_start = html.find("ltx_abstract")
    if article_start >= 0 and abstract_start > article_start:
        header_text = strip_tags(html[article_start:abstract_start])
        for line in header_text.split("\n"):
            line = line.strip()
            if not line or len(line) < 5 or len(line) > 500:
                continue
            if any(kw in line.lower() for kw in INST_KEYWORDS):
                affils.add(line.strip(" ,;."))

    return _clean_affiliations(affils)


def _clean_affiliations(raw) -> list[str]:
    out: list[str] = []
    for text in raw:
        text = re.sub(r"(?i)\baffiliations?\s*:\s*", " ", text)
        text = re.sub(r"[†‡∗*§¶]+|\bthanks:?", " ", text)
        text = re.sub(r"(?i)(corresponding|equal contribution|project lead)[^,;]*", " ", text)
        text = re.sub(r"\S+@\S+", " ", text)
        text = re.sub(r"(?i)^(e-?mail|emails|contact)\b:?", " ", text.strip())
        text = " ".join(text.split()).strip(" ,;.:")
        if len(text) < 4 or not re.search(r"[A-Za-z\u4e00-\u9fff]{3}", text):
            continue
        if text not in out and not any(text in o for o in out):
            out = [o for o in out if o not in text] + [text]
    return out


def extract_section_headers(html: str) -> list[str]:
    """Extract h2/h3 section headers."""
    headers = []
    for m in re.finditer(r"<h[23][^>]*>(.*?)</h[23]>", html, re.DOTALL):
        text = strip_tags(m.group(1)).strip()
        text = re.sub(r"^\d+(\.\d+)*\.?\s*", "", text)  # remove "1.2.3 " prefix
        if text and len(text) < 200:
            headers.append(text)
    return headers[:25]


def extract_captions(html: str) -> list[str]:
    """Extract figure/table captions of reasonable length."""
    captions = []
    for m in re.finditer(r"<(?:figcaption|caption)[^>]*>(.*?)</(?:figcaption|caption)>", html, re.DOTALL):
        text = strip_tags(m.group(1)).strip()
        text = re.sub(r"\s+", " ", text)
        if 10 <= len(text) <= 200:
            captions.append(text)
    return captions[:8]


def extract_method_names(html: str, paper_title: str) -> list[str]:
    """Extract method/model names from HTML text using CamelCase + ALLCAPS patterns."""
    text = strip_tags(html)

    # CamelCase: DreamerV3, OpenVLA, ControlNet, MuJoCo
    camel = re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]*)+(?:V?\d+)?)\b", text)
    # ALLCAPS with optional version: DDPM, SAM-2, GPT-4, RT-2
    allcaps = re.findall(r"\b([A-Z]{2,}(?:[-_]\d+)?)\b", text)
    # CamelCase with numbers: GPT4o, Llama3
    camel_num = re.findall(r"\b([A-Z][a-z]+[A-Z][a-z]*\d+[a-z]?)\b", text)
    # Hyphenated: Diffusion-Policy, Stable-Diffusion
    hyphenated = re.findall(r"\b([A-Z][a-z]+-[A-Z][a-z]+(?:-[A-Z][a-z]+)?)\b", text)

    all_names = camel + allcaps + camel_num + hyphenated
    cnt = Counter(all_names)

    # Build stop set including title words
    title_words = set(re.findall(r"\b[A-Za-z]+\b", paper_title))
    stop = METHOD_STOP | {w for w in title_words if len(w) >= 3}

    method_names = []
    seen = set()
    for name, count in cnt.most_common(40):
        if count < 2:
            continue
        if name in stop:
            continue
        if len(name) < 2:
            continue
        name_lower = name.lower()
        if name_lower in seen:
            continue
        seen.add(name_lower)
        method_names.append(name)
        if len(method_names) >= 20:
            break

    return method_names


def _sections(html: str) -> list[tuple[str, str]]:
    """Split arXiv HTML into (h2 title, body html) pairs."""
    parts = re.split(r"(<h2[^>]*>.*?</h2>)", html, flags=re.DOTALL)
    out = []
    for i in range(1, len(parts) - 1, 2):
        title = re.sub(r"^\s*[\dA-Z]+(\.\d+)*\s+", "", strip_tags(parts[i])).strip()
        out.append((" ".join(title.split()), parts[i + 1]))
    return out


def _paragraph_text(body: str) -> str:
    paras = re.findall(r'<p class="ltx_p"[^>]*>(.*?)</p>', body, re.DOTALL)
    text = " ".join(strip_tags(p) for p in paras) if paras else strip_tags(body)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s*\[\d+(?:[,–-]\s*\d+)*\]", "", text)


def extract_method_summary(html: str, paper_title: str = "") -> str:
    """Method description (~300-600 chars) from the main method section."""
    sections = _sections(html)
    skip = re.compile(r"(?i)^(introduction|related|background|preliminar|experiment|evaluation|result|"
                      r"discussion|limitation|conclusion|reference|acknowledg|appendix|ethic|broader)")
    method_kw = re.compile(r"(?i)method|approach|framework|proposed|our model|architecture|design")
    short_name = paper_title.split(":")[0].strip().casefold() if ":" in paper_title else ""

    chosen = ""
    for title, body in sections:
        if method_kw.search(title) or (short_name and short_name in title.casefold()):
            chosen = _paragraph_text(body)
            if len(chosen) >= 150:
                break
    if len(chosen) < 150:
        for title, body in sections:
            if title and not skip.match(title):
                chosen = _paragraph_text(body)
                if len(chosen) >= 150:
                    break
    if len(chosen) < 150:
        for title, body in sections:
            if title.lower().startswith("introduction"):
                paras = re.findall(r'<p class="ltx_p"[^>]*>(.*?)</p>', body, re.DOTALL)
                chosen = re.sub(r"\s+", " ", " ".join(strip_tags(x) for x in paras[-2:])).strip()
                break
    if len(chosen) > 600:
        end = chosen.rfind(". ", 300, 650)
        chosen = chosen[:end + 1] if end > 0 else chosen[:600].rsplit(" ", 1)[0] + "..."
    return chosen if len(chosen) >= 100 else ""


# ══════════════════════════════════════════════════════════════════════════════
# Abs page fallback extractor
# ══════════════════════════════════════════════════════════════════════════════

def extract_from_abs(html: str) -> dict:
    """Extract authors and affiliations from arxiv abs page meta tags."""
    authors = re.findall(r'<meta\s+name="citation_author"\s+content="([^"]+)"', html)
    authors = [a.strip() for a in authors if a.strip()]
    affils = set()
    for m in re.findall(r'<meta\s+name="citation_author_institution"\s+content="([^"]+)"', html):
        if m.strip():
            affils.add(m.strip())
    return {"authors": authors, "affiliations": list(affils)}



# ══════════════════════════════════════════════════════════════════════════════
# PDF affiliation extraction
# ══════════════════════════════════════════════════════════════════════════════

EXTRACT_AFFILIATIONS_SCRIPT = str(
    __import__("pathlib").Path(__file__).parent / "extract_affiliations.py"
)

ARXIV_ID_PATTERN = re.compile(r"(?:[0-9]{4}\.[0-9]{4,5}|[a-z][a-z.-]*/[0-9]{7})(?:v[1-9][0-9]*)?")


def valid_arxiv_id(value: object) -> bool:
    return isinstance(value, str) and ARXIV_ID_PATTERN.fullmatch(value) is not None


async def run_process(*args: str, input_data: bytes | None = None, timeout: float = 45) -> bytes:
    """Pass bytes between subprocesses without shell parsing; reap timed-out children."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE if input_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(input_data), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.communicate()
        raise
    if proc.returncode:
        raise RuntimeError(f"{Path(args[0]).name} exited with status {proc.returncode}")
    return stdout


async def extract_affiliations_pdf(arxiv_id: str, sem: asyncio.Semaphore,
                                   retries: int = 3) -> list[str]:
    """Download -> extract text -> parse affiliations using explicit byte streams."""
    if not valid_arxiv_id(arxiv_id):
        raise ValueError("Invalid arXiv ID")
    for attempt in range(1, retries + 1):
        async with sem:
            try:
                pdf = await run_process("curl", "-fsSL", "--max-time", str(CURL_TIMEOUT),
                                        f"https://arxiv.org/pdf/{arxiv_id}", timeout=CURL_TIMEOUT + 5)
                text = await run_process("pdftotext", "-l", "2", "-", "-", input_data=pdf)
                output = await run_process(sys.executable, EXTRACT_AFFILIATIONS_SCRIPT, input_data=text)
                data = json.loads(output.decode("utf-8", errors="replace"))
                affils = data.get("affiliations", [])
                if affils:
                    return affils
            except (OSError, RuntimeError, asyncio.TimeoutError, json.JSONDecodeError) as e:
                print(f"  [pdf] attempt {attempt}/{retries} failed {arxiv_id}: {e}", file=sys.stderr)
        if attempt < retries:
            await asyncio.sleep(3 * attempt)
    return []


# ══════════════════════════════════════════════════════════════════════════════
# Per-paper enrichment
# ══════════════════════════════════════════════════════════════════════════════

async def enrich_one(paper: dict, sem: asyncio.Semaphore) -> dict:
    """Enrich a single paper with metadata from HTML and abs pages."""
    arxiv_id = paper.get("arxiv_id", "")
    if not arxiv_id:
        # Try to extract from URL
        url = paper.get("url", "")
        m = re.search(r"(\d{4}\.\d{4,5})", url)
        arxiv_id = m.group(1) if m else ""
    if not valid_arxiv_id(arxiv_id):
        return paper

    title = paper.get("title", "")
    result = dict(paper)  # copy

    try:
        # Fetch HTML page
        html_url = f"https://arxiv.org/html/{arxiv_id}"
        html = await curl_fetch(html_url, sem)

        # Parse HTML if we got content
        html_authors = []
        html_affiliations = []
        figure_url = ""
        section_headers = []
        captions = []
        method_names = []
        method_summary = ""

        if html and len(html) > 1000:
            figure_url = extract_figure_url(html, arxiv_id)
            html_authors = extract_authors_html(html)
            html_affiliations = extract_affiliations_html(html)
            section_headers = extract_section_headers(html)
            captions = extract_captions(html)
            method_names = extract_method_names(html, title)
            method_summary = extract_method_summary(html, title)

        # Abs fallback if HTML authors OR affiliations are empty
        abs_authors = []
        abs_affiliations = []
        if not html_authors or not html_affiliations:
            abs_url = f"https://arxiv.org/abs/{arxiv_id}"
            abs_html = await curl_fetch(abs_url, sem)
            if abs_html:
                abs_data = extract_from_abs(abs_html)
                abs_authors = abs_data["authors"]
                abs_affiliations = abs_data["affiliations"]

        # PDF fallback for affiliations if still empty
        pdf_affiliations = []
        if not html_affiliations and not abs_affiliations:
            pdf_affiliations = await extract_affiliations_pdf(arxiv_id, sem)

        # ── Merge with priority rules ──
        # Principle: new extraction > existing input, but never overwrite non-empty with empty

        # figure_url: HTML curl > keep existing
        result["figure_url"] = figure_url or paper.get("figure_url", "")

        # affiliations: HTML > abs fallback > PDF fallback > keep existing input
        if html_affiliations:
            result["affiliations"] = ", ".join(html_affiliations)
        elif abs_affiliations:
            result["affiliations"] = ", ".join(abs_affiliations)
        elif pdf_affiliations:
            result["affiliations"] = ", ".join(pdf_affiliations)
        # else: keep whatever was in the input (supports re-enriching enriched data)

        # authors: HTML > abs fallback > keep existing input
        if html_authors:
            result["authors"] = ", ".join(html_authors)
        elif abs_authors:
            result["authors"] = ", ".join(abs_authors)
        # else: keep original

        # Other enriched fields
        result["section_headers"] = section_headers
        result["captions"] = captions
        result["method_names"] = method_names
        result["method_summary"] = method_summary

    except Exception as e:
        print(f"  [error] {arxiv_id}: {e}", file=sys.stderr)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

async def enrich_all(papers: list[dict]) -> list[dict]:
    """Enrich all papers concurrently with a semaphore limit."""
    sem = asyncio.Semaphore(SEMAPHORE_LIMIT)
    tasks = [asyncio.create_task(enrich_one(paper, sem)) for paper in papers]

    # gather preserves order and handles exceptions inline
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    ordered = []
    for i, result in enumerate(raw_results):
        if isinstance(result, Exception):
            print(f"  [error] paper #{i} ({papers[i].get('arxiv_id','')}): {result}", file=sys.stderr)
            ordered.append(papers[i])
        else:
            ordered.append(result)

    return ordered


def main():
    """Main entry point with cross-platform path support.

    Usage:
        # Linux/Mac - pipe from stdin
        cat /tmp/daily_papers_selected.json | python3 enrich_papers.py

        # Windows - file arguments
        python3 enrich_papers.py input.json output.json

        # Cross-platform - auto-detect default paths
        python3 enrich_papers.py
    """
    output_path = None
    input_path = None

    # Parse arguments: [input.json] [output.json]
    if len(sys.argv) >= 2:
        if sys.argv[1].endswith('.json'):
            input_path = sys.argv[1]
        else:
            output_path = sys.argv[1]
    if len(sys.argv) >= 3:
        if sys.argv[2].endswith('.json'):
            output_path = sys.argv[2]

    # Auto-detect input path if not provided (Windows/Linux compatible)
    if not input_path:
        auto_input_path = temp_file_path('daily_papers_selected.json')
        if auto_input_path.exists():
            input_path = str(auto_input_path)
            print(f"[enrich_papers] Auto-detected input: {input_path}", file=sys.stderr)

    # Read input from file or stdin
    if input_path:
        try:
            with open(input_path, 'r', encoding='utf-8', errors='replace') as f:
                input_data = f.read()
        except FileNotFoundError:
            print(f"Error: Input file not found: {input_path}", file=sys.stderr)
            _write_output("[]", output_path)
            sys.exit(1)
    else:
        input_data = sys.stdin.read()

    if not input_data.strip():
        _write_output("[]", output_path)
        return

    try:
        papers = json.loads(input_data)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}", file=sys.stderr)
        _write_output("[]", output_path)
        sys.exit(1)

    if not papers:
        _write_output("[]", output_path)
        return

    print(f"Enriching {len(papers)} papers...", file=sys.stderr)
    enriched = asyncio.run(enrich_all(papers))
    print(f"Done. Enriched {len(enriched)} papers.", file=sys.stderr)

    output = json.dumps(enriched, ensure_ascii=False, indent=2) + "\n"

    # Auto-detect output path if not provided (Windows/Linux compatible)
    if not output_path:
        output_path = str(temp_file_path('daily_papers_enriched.json'))
        print(f"[enrich_papers] Auto-detected output: {output_path}", file=sys.stderr)

    _write_output(output, output_path)


def _write_output(data: str, output_path: str | None):
    """Write output to file (if path given) or stdout with explicit flush."""
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(data)
    else:
        sys.stdout.write(data)
        sys.stdout.flush()


if __name__ == "__main__":
    main()
