#!/usr/bin/env python3
"""Write papers and notes into Zotero Desktop (default) or the Web API.

Never touches zotero.sqlite. Local writes require Zotero 10+ authorization.
Use authorize to grant access and attachments to resolve linked files (Attanger).

Subcommands
  check                         verify key, permissions and library id
  setup                         create daily digest containers (idempotent)
  push-daily --payload P        create/update recommended items, review notes, PDF links,
             --enriched E       the day's digest note; write result JSON + history
  upsert-paper --arxiv ID       save to pending by default; existing mode requests classification
  add-note (--arxiv ID|--item KEY) --markdown F [--kind full-note] [--replace]
                                attach a Markdown note (converted to Zotero HTML; LaTeX kept)
  notes (--arxiv ID|--item KEY) list child notes with kind tags and length
  find --arxiv ID               print the item key of an existing paper

Global flag: --dry-run  print planned requests, write nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, unquote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))

from md2zotero import to_note_html  # noqa: E402
from zdp_config import (  # noqa: E402
    classification_mode, config_home, directions, language, load_history, save_history, temp_file_path,
    zotero_config,
)

API = "https://api.zotero.org"
UA = "zotero-daily-papers/1.0"
# Note titles start with a localized kind marker. Both languages are always recognized so a
# library keeps deduplicating after the language is switched; new titles use the configured one.
NOTE_LABELS = {
    "zh": {"full-note": "精读", "quick": "速读", "review": "每日锐评", "digest": "每日推荐"},
    "en": {"full-note": "Deep Read", "quick": "Quick Read", "review": "Daily Review", "digest": "Daily Digest"},
}
NOTE_MARKERS = {"zh": ("【", "】", "笔记-"), "en": ("[", "]", "Note-")}
NOTE_KINDS = tuple(NOTE_LABELS["zh"])
TIERS = ("must", "worth", "skip")
IMAGE_SOURCE_LABEL = {"zh": "原图", "en": "source"}


def note_prefix(kind: str, lang: str) -> str:
    left, right, custom = NOTE_MARKERS[lang]
    return f"{left}{NOTE_LABELS[lang].get(kind, custom + kind)}{right}"


def parse_note_prefix(title: str) -> tuple[str | None, str]:
    """Return (kind, rest of title) for a title that starts with any known marker."""
    for lang, labels in NOTE_LABELS.items():
        for kind in labels:
            prefix = note_prefix(kind, lang)
            if title.startswith(prefix):
                return kind, title[len(prefix):]
        left, right, custom = NOTE_MARKERS[lang]
        match = re.match(re.escape(left + custom) + r"([a-z][a-z0-9-]*)" + re.escape(right), title)
        if match:
            return match.group(1), title[match.end():]
    return None, title
ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


class ZoteroError(RuntimeError):
    pass


def die(message: str, code: int = 1) -> None:
    print(f"[zotero_push] ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def log(message: str) -> None:
    print(f"[zotero_push] {message}", file=sys.stderr)


def norm_arxiv(value: str) -> str:
    m = ARXIV_ID_RE.search(value or "")
    return m.group(1) if m else ""


# ══════════════════════════════════════════════════════════════════════════
# HTTP client
# ══════════════════════════════════════════════════════════════════════════

class Zotero:
    def __init__(self, cfg: dict, dry_run: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.key = (cfg.get("api_key") or "").strip()
        self.lib_type = (cfg.get("library_type") or "user").strip()
        self.lib_id = str(cfg.get("library_id") or "").strip()
        if self.lib_type not in ("user", "group"):
            die("zotero.library_type must be 'user' or 'group'")
        if not dry_run and (not self.key or not self.lib_id):
            die("Zotero is not configured: set ZOTERO_API_KEY and ZOTERO_LIBRARY_ID "
                "(or zotero.api_key / zotero.library_id in ~/.config/zotero-daily-papers/config.json). "
                "See references/zotero-setup.md")
        self.base = f"{API}/{self.lib_type}s/{self.lib_id or '0'}"
        self.cache_identity = self.base
        self._dry_counter = 0

    # -- low level --------------------------------------------------------
    def _raw(self, method: str, url: str, body: bytes | None = None,
             headers: dict | None = None, auth: bool = True) -> tuple[int, dict, bytes]:
        hdrs = {"User-Agent": UA}
        if auth:
            hdrs.update({"Zotero-API-Key": self.key, "Zotero-API-Version": "3"})
        hdrs.update(headers or {})
        for attempt in range(5):
            req = Request(url, data=body, method=method, headers=hdrs)
            try:
                with urlopen(req, timeout=60) as resp:
                    data = resp.read()
                    backoff = resp.headers.get("Backoff")
                    if backoff:
                        time.sleep(min(int(backoff), 30))
                    return resp.status, dict(resp.headers), data
            except HTTPError as err:
                payload = err.read()
                if err.code in (429, 503) and attempt < 4:
                    wait = int(err.headers.get("Retry-After") or 2 ** (attempt + 1))
                    log(f"rate limited ({err.code}); retrying in {wait}s")
                    time.sleep(min(wait, 60))
                    continue
                raise ZoteroError(f"{method} {url} -> HTTP {err.code}: "
                                  f"{payload.decode('utf-8', 'replace')[:500]}") from None
            except URLError as err:
                if attempt < 4:
                    time.sleep(2 ** attempt)
                    continue
                raise ZoteroError(f"{method} {url} failed: {err}") from None
        raise ZoteroError(f"{method} {url} failed after retries")

    def _fake_key(self) -> str:
        self._dry_counter += 1
        return f"DRY{self._dry_counter:05d}"

    def get(self, path: str, params: dict | None = None) -> list | dict:
        url = f"{self.base}{path}"
        if params:
            url += "?" + urlencode(params, doseq=True)
        if self.dry_run and not self.key:
            log(f"[dry-run] GET {url}")
            return []
        _, _, data = self._raw("GET", url)
        return json.loads(data or b"null")

    def get_all(self, path: str, params: dict | None = None) -> list:
        params = dict(params or {})
        params.setdefault("limit", 100)
        start, out = 0, []
        while True:
            params["start"] = start
            page = self.get(path, params)
            if not isinstance(page, list) or not page:
                return out
            out.extend(page)
            if len(page) < params["limit"]:
                return out
            start += len(page)

    def create(self, objects: list[dict], path: str = "/items") -> list[str]:
        """POST objects; returns created keys in order. Raises on any failure."""
        if self.dry_run:
            keys = [self._fake_key() for _ in objects]
            for key, obj in zip(keys, objects):
                summary = {k: (v if k not in ("note", "abstractNote") else f"<{len(v)} chars>")
                           for k, v in obj.items()}
                log(f"[dry-run] POST {path} -> {key}: {json.dumps(summary, ensure_ascii=False)[:400]}")
            return keys
        _, _, data = self._raw("POST", f"{self.base}{path}", json.dumps(objects).encode(),
                               {"Content-Type": "application/json"})
        result = json.loads(data)
        if result.get("failed"):
            raise ZoteroError(f"create failed: {json.dumps(result['failed'], ensure_ascii=False)[:800]}")
        success = result.get("success", {})
        return [success[str(i)] for i in range(len(objects))]

    def patch(self, key: str, version: int, fields: dict) -> None:
        if self.dry_run:
            log(f"[dry-run] PATCH /items/{key} fields={list(fields)}")
            return
        self._raw("PATCH", f"{self.base}/items/{key}", json.dumps(fields).encode(),
                  {"Content-Type": "application/json", "If-Unmodified-Since-Version": str(version)})

    def item(self, key: str) -> dict | None:
        if self.dry_run and (key.startswith("DRY") or not self.key):
            return None
        try:
            return self.get(f"/items/{key}")
        except ZoteroError as err:
            if "HTTP 404" in str(err):
                return None
            raise

    def upload_file(self, att_key: str, data: bytes, filename: str, content_type: str) -> None:
        """Zotero file-upload protocol: authorize -> upload -> register."""
        if self.dry_run:
            log(f"[dry-run] upload {filename} ({len(data)} bytes) to attachment {att_key}")
            return
        form = {"Content-Type": "application/x-www-form-urlencoded", "If-None-Match": "*"}
        params = urlencode({"md5": hashlib.md5(data).hexdigest(), "filename": filename,
                            "filesize": len(data), "mtime": int(time.time() * 1000)}).encode()
        _, _, raw = self._raw("POST", f"{self.base}/items/{att_key}/file", params, form)
        auth = json.loads(raw)
        if auth.get("exists"):
            return
        body = auth["prefix"].encode() + data + auth["suffix"].encode()
        self._raw("POST", auth["url"], body, {"Content-Type": auth["contentType"]}, auth=False)
        self._raw("POST", f"{self.base}/items/{att_key}/file",
                  urlencode({"upload": auth["uploadKey"]}).encode(), form)

    def select_link(self, key: str) -> str:
        if self.lib_type == "group":
            return f"zotero://select/groups/{self.lib_id}/items/{key}"
        return f"zotero://select/library/items/{key}"


class LocalZotero(Zotero):
    """Zotero 10 local API; local credentials/versions never mix with Web API."""

    def __init__(self, cfg: dict, dry_run: bool = False):
        self.cfg, self.dry_run = cfg, dry_run
        self.key = ""
        self.lib_type = cfg.get("library_type", "user")
        self.lib_id = "0" if self.lib_type == "user" else str(cfg.get("library_id") or "")
        if self.lib_type not in ("user", "group") or not self.lib_id:
            raise ZoteroError("Local group libraries require a library_id")
        self.origin = cfg.get("local_url", "http://127.0.0.1:23119").rstrip("/")
        parsed = urlsplit(self.origin)
        if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise ZoteroError("local_url must be a loopback HTTP origin")
        self.base = f"{self.origin}/api/{self.lib_type}s/{self.lib_id}"
        self._dry_counter = 0
        self.server_id = "dry-run"
        self.version = ""
        if not dry_run:
            _, headers, _ = self._request("GET", f"{self.origin}/api/")
            self.server_id = headers.get("Zotero-Server-ID", "")
            self.version = headers.get("X-Zotero-Version") or headers.get("Zotero-Version", "")
            if not self.server_id:
                raise ZoteroError("Local writes require Zotero 10+. Update Zotero and enable the local API.")
        self.cache_identity = f"{self.base}#{self.server_id}"
        token_id = hashlib.sha256(self.cache_identity.encode()).hexdigest()[:24]
        self.token_path = config_home() / f"local-auth-{token_id}.json"
        if not dry_run:
            try:
                self.key = json.loads(self.token_path.read_text())["key"]
            except (OSError, ValueError, KeyError):
                pass

    def _request(self, method, url, body=None, headers=None):
        # Never retry ambiguous writes: the server may already have committed them.
        req = Request(url, data=body, method=method,
                      headers={"User-Agent": UA, "Zotero-API-Version": "3", **(headers or {})})
        with urlopen(req, timeout=120) as resp:
            return resp.status, resp.headers, resp.read()

    def authorize(self):
        if self.dry_run:
            log("[dry-run] would request local write authorization")
            return
        log("Zotero will request write permission. 'Always Allow' remembers it for later runs.")
        try:
            _, _, raw = self._request("POST", f"{self.origin}/api/local/authorize",
                json.dumps({"appName": "zotero-daily-papers"}).encode(),
                {"Content-Type": "application/json", "Zotero-Server-ID": self.server_id})
        except HTTPError as err:
            raise ZoteroError(f"Local authorization failed (HTTP {err.code}); allow access in Zotero.") from None
        result = json.loads(raw)
        self.key = result["key"]
        if result.get("remember"):
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as stream:
                os.chmod(self.token_path, 0o600)
                json.dump({"key": self.key}, stream)

    def _raw(self, method, url, body=None, headers=None, auth=True):
        writing = method != "GET" and auth
        if writing and not self.key:
            self.authorize()
        for attempt in range(2):
            hdrs = dict(headers or {})
            if auth:
                hdrs["Zotero-Server-ID"] = self.server_id
                if writing:
                    hdrs["Zotero-API-Key"] = self.key
            try:
                return self._request(method, url, body, hdrs)
            except HTTPError as err:
                if err.code == 401 and writing and attempt == 0:
                    self.key = ""
                    self.authorize()
                    continue
                # Do not include arbitrary server payloads containing credentials.
                raise ZoteroError(f"{method} {url} -> HTTP {err.code}. "
                                  "For 412, rerun against the current Zotero library.") from None
            except (URLError, OSError) as err:
                raise ZoteroError(f"Local Zotero request failed: {err}. Keep Zotero open.") from None

    def file_info(self, attachment: dict) -> dict:
        key, data = attachment["key"], attachment.get("data", {})
        result = {"key": key, "title": data.get("title"), "link_mode": data.get("linkMode")}
        if data.get("linkMode") == "linked_url":
            return {**result, "url": data.get("url"), "exists": False}
        try:
            _, _, raw = self._raw("GET", f"{self.base}/items/{key}/file/view/url")
            uri = raw.decode().strip()
            parsed = urlsplit(uri)
            if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
                raise ZoteroError("Zotero returned a non-local attachment URL")
            path = Path(unquote(parsed.path))
            result.update({"path": str(path), "exists": path.is_file()})
        except ZoteroError as err:
            result["error"] = str(err)
        return result


# ══════════════════════════════════════════════════════════════════════════
# Local cache (collection keys, arXiv -> item key)
# ══════════════════════════════════════════════════════════════════════════

def cache_path() -> Path:
    return config_home() / "zotero-cache.json"


def load_cache(zot: Zotero | None = None) -> dict:
    try:
        data = json.loads(cache_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and (zot is None or data.get("identity") == zot.cache_identity) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict, dry_run: bool) -> None:
    if dry_run:
        return
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as err:
        log(f"WARNING: could not update local lookup cache at {path}: {err}")


# ══════════════════════════════════════════════════════════════════════════
# Collections
# ══════════════════════════════════════════════════════════════════════════

def collection_catalog(zot: Zotero, samples: bool = False) -> list[dict]:
    """Read the current library, preserving paths so duplicate names are unambiguous."""
    rows = zot.get_all("/collections")
    data = {r["key"]: r.get("data", r) for r in rows if not r.get("data", r).get("deleted")}
    def path(key, seen=None):
        seen = set(seen or ())
        if key in seen or key not in data:
            return []
        seen.add(key)
        col = data[key]
        return path(col.get("parentCollection"), seen) + [col["name"]]
    result = []
    for key, col in data.items():
        row = {"key": key, "name": col["name"], "parent": col.get("parentCollection") or None,
               "path": " / ".join(path(key))}
        if samples:
            items = zot.get(f"/collections/{key}/items/top", {"limit": 3})
            row["example_titles"] = [i.get("data", {}).get("title", "") for i in items
                                     if i.get("data", {}).get("title")]
        result.append(row)
    return result


def select_collections(zot: Zotero, cfg: dict, selected: list[str], catalog: list[dict]) -> list[str]:
    if not isinstance(selected, list) or any(not isinstance(k, str) for k in selected):
        raise ZoteroError("collection_keys must be an array of existing collection keys")
    known = {c["key"] for c in catalog}
    unknown = set(selected) - known
    if unknown and getattr(zot, "dry_run", False) is not True:
        raise ZoteroError(f"Unknown/deleted collection keys: {', '.join(sorted(unknown))}; read collections again")
    if selected:
        return list(dict.fromkeys(selected))
    name = cfg.get("pending_collection", "待定")
    pending = next((c for c in catalog if c["name"] == name and not c["parent"]), None)
    if pending:
        return [pending["key"]]
    key = zot.create([{"name": name}], path="/collections")[0]
    catalog.append({"key": key, "name": name, "parent": None, "path": name})
    return [key]


def ensure_collections(zot: Zotero, cfg: dict) -> dict:
    """Only create the daily digest collection; paper categories come from the library.

    Daily overview notes live directly in the top-level `root_collection` (Daily Papers).
    """
    name = cfg.get("root_collection") or "Daily Papers"
    existing = next((c for c in collection_catalog(zot) if c["name"] == name and not c["parent"]), None)
    key = existing["key"] if existing else zot.create([{"name": name}], path="/collections")[0]
    return {"digest": key}


def collection_keys(zot: Zotero, cfg: dict, cache: dict) -> dict:
    # Re-read live collections, including deletions and renames, on every run.
    return ensure_collections(zot, cfg)


# ══════════════════════════════════════════════════════════════════════════
# Paper metadata
# ══════════════════════════════════════════════════════════════════════════

# Subject names as Zotero's arXiv translator writes them (automatic tags, type 1).
ARXIV_SUBJECTS = {
    "cs.AI": "Computer Science - Artificial Intelligence",
    "cs.CL": "Computer Science - Computation and Language",
    "cs.CV": "Computer Science - Computer Vision and Pattern Recognition",
    "cs.LG": "Computer Science - Machine Learning",
    "cs.MM": "Computer Science - Multimedia",
    "cs.IR": "Computer Science - Information Retrieval",
    "cs.NE": "Computer Science - Neural and Evolutionary Computing",
    "cs.RO": "Computer Science - Robotics",
    "cs.DC": "Computer Science - Distributed, Parallel, and Cluster Computing",
    "cs.CR": "Computer Science - Cryptography and Security",
    "cs.HC": "Computer Science - Human-Computer Interaction",
    "cs.SE": "Computer Science - Software Engineering",
    "cs.SD": "Computer Science - Sound",
    "cs.GR": "Computer Science - Graphics",
    "cs.MA": "Computer Science - Multiagent Systems",
    "cs.PL": "Computer Science - Programming Languages",
    "cs.IT": "Computer Science - Information Theory",
    "stat.ML": "Statistics - Machine Learning",
    "eess.IV": "Electrical Engineering and Systems Science - Image and Video Processing",
    "eess.AS": "Electrical Engineering and Systems Science - Audio and Speech Processing",
    "eess.SP": "Electrical Engineering and Systems Science - Signal Processing",
    "math.OC": "Mathematics - Optimization and Control",
}


def _parse_arxiv_entry(entry, ns) -> dict:
    text = lambda tag: " ".join((entry.findtext(tag, "", ns) or "").split())  # noqa: E731
    aid = norm_arxiv(text("a:id"))
    cat = entry.find("x:primary_category", ns)
    categories = [c.get("term", "") for c in entry.findall("a:category", ns) if c.get("term")]
    return {
        "arxiv_id": aid,
        "title": text("a:title"),
        "abstract": text("a:summary"),
        "authors": ", ".join(" ".join((a.findtext("a:name", "", ns)).split())
                             for a in entry.findall("a:author", ns)),
        # The translator dates an arXiv preprint by its latest version ("updated").
        "date": (text("a:updated") or text("a:published"))[:10],
        "published": text("a:published")[:10],
        "category": cat.get("term", "") if cat is not None else "",
        "categories": categories,
        "url": f"https://arxiv.org/abs/{aid}",
        "pdf": f"https://arxiv.org/pdf/{aid}",
    }


def fetch_arxiv_meta_batch(arxiv_ids: list[str]) -> dict[str, dict]:
    """One arXiv API call for many ids; returns {arxiv_id: meta} for ids arXiv knows."""
    ids = [a for a in dict.fromkeys(arxiv_ids) if a]
    out: dict[str, dict] = {}
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        url = (f"https://export.arxiv.org/api/query?id_list={','.join(chunk)}"
               f"&max_results={len(chunk)}")
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=60) as resp:
            root = ET.fromstring(resp.read())
        for entry in root.findall("a:entry", ns):
            if entry.find("a:title", ns) is None:
                continue
            meta = _parse_arxiv_entry(entry, ns)
            if meta["arxiv_id"] in chunk and meta["title"] and meta["title"].lower() != "error":
                out[meta["arxiv_id"]] = meta
        if start + 50 < len(ids):
            time.sleep(3)  # arXiv API etiquette
    return out


def fetch_arxiv_meta(arxiv_id: str) -> dict:
    meta = fetch_arxiv_meta_batch([arxiv_id]).get(arxiv_id)
    if not meta:
        raise ZoteroError(f"arXiv has no entry for {arxiv_id}")
    return meta


def split_name(name: str) -> dict:
    name = " ".join(name.split())
    if " " not in name:
        return {"creatorType": "author", "name": name}
    first, last = name.rsplit(" ", 1)
    return {"creatorType": "author", "firstName": first, "lastName": last}


def item_payload(meta: dict, collections: list[str], tags: list[str], item_type: str = "preprint") -> dict:
    """Mirror what Zotero's arXiv translator saves for "Add Item by Identifier"."""
    aid = meta["arxiv_id"]
    authors = [a for a in re.split(r",\s*", meta.get("authors") or "") if a.strip()]
    primary = meta.get("category") or ""
    extra = f"arXiv:{aid}" + (f" [{primary}]" if primary else "")
    subject_tags = [{"tag": ARXIV_SUBJECTS[c], "type": 1}
                    for c in dict.fromkeys(meta.get("categories") or ([primary] if primary else []))
                    if c in ARXIV_SUBJECTS]
    obj = {
        "itemType": item_type,
        "title": meta.get("title") or aid,
        "creators": [split_name(a) for a in authors[:100]],
        "abstractNote": meta.get("abstract", ""),
        "date": meta.get("date", ""),
        "libraryCatalog": "arXiv.org",
        "url": f"http://arxiv.org/abs/{aid}",
        "accessDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "extra": extra,
        "tags": subject_tags + [{"tag": t} for t in dict.fromkeys(tags) if t],
        "collections": list(dict.fromkeys(collections)),
    }
    if item_type == "preprint":
        obj.update({"repository": "arXiv", "archiveID": f"arXiv:{aid}",
                    "DOI": f"10.48550/arXiv.{aid}"})
    else:
        obj.update({"publicationTitle": "arXiv", "DOI": f"10.48550/arXiv.{aid}"})
    return obj


_EXTRA_ARXIV_RE = re.compile(r"^\s*arxiv\s*:\s*(\S+)", re.I | re.M)


def item_arxiv_ids(data: dict) -> set[str]:
    """arXiv IDs the item itself declares, read only from its identifier fields.

    The item's own archiveID / DOI / arXiv URL win. Only when none of them names an
    arXiv paper (e.g. a journal version) is an "arXiv: ID" line in Extra trusted; free
    text elsewhere (notes, citations in Extra) never counts.
    """
    ids = set()
    for field in ("archiveID", "DOI"):
        value = str(data.get(field) or "")
        if "arxiv" in value.lower() and norm_arxiv(value):
            ids.add(norm_arxiv(value))
    url = str(data.get("url") or "")
    if re.search(r"arxiv\.org/", url, re.I) and norm_arxiv(url):
        ids.add(norm_arxiv(url))
    if not ids:
        ids = {norm_arxiv(m) for m in _EXTRA_ARXIV_RE.findall(str(data.get("extra") or ""))} - {""}
    return ids


def item_matches(data: dict, aid: str) -> bool:
    if data.get("deleted") or data.get("itemType") in ("note", "attachment", "annotation"):
        return False
    return aid in item_arxiv_ids(data)


def find_item(zot: Zotero, aid: str, cache: dict) -> dict | None:
    key = cache.get("items", {}).get(aid)
    if key:
        found = zot.item(key)
        if found and item_matches(found.get("data", {}), aid):
            return found
    # qmode=everything also hits PDFs that merely cite this paper, so page through all hits.
    start = 0
    while True:
        page = zot.get("/items/top", {"q": aid, "qmode": "everything", "limit": 100, "start": start}) or []
        for row in page:
            if item_matches(row.get("data", {}), aid):
                cache.setdefault("items", {})[aid] = row["key"]
                return row
        if len(page) < 100:
            return None
        start += len(page)


def ensure_paper(zot: Zotero, meta: dict, collections: list[str], tags: list[str],
                 cache: dict, fallback_collection: str | None = None) -> tuple[str, bool, list[str]]:
    """Merge classifications/tags into an existing item, or create a new one.

    `fallback_collection` is the pending ("待定") key used when nothing was chosen. An
    existing item that already lives in some collection is not added to it, so papers
    you filed by hand stay where they are.

    Returns (item key, created, the item's collections after the write).
    """
    aid = meta["arxiv_id"]
    existing = find_item(zot, aid, cache)
    if existing:
        data = existing["data"]
        current = data.get("collections", [])
        wanted = list(collections)
        if fallback_collection and current:
            wanted = [c for c in wanted if c != fallback_collection]
        cols = list(dict.fromkeys(current + wanted))
        have = {t["tag"] for t in data.get("tags", [])}
        new_tags = data.get("tags", []) + [{"tag": t} for t in dict.fromkeys(tags) if t and t not in have]
        if cols != data.get("collections", []) or len(new_tags) != len(data.get("tags", [])):
            zot.patch(existing["key"], existing["version"], {"collections": cols, "tags": new_tags})
        return existing["key"], False, cols
    try:
        key = zot.create([item_payload(meta, collections, tags)])[0]
    except ZoteroError as err:
        if "itemType" not in str(err) and "field" not in str(err).lower():
            raise
        log(f"preprint item rejected ({err}); retrying as journalArticle")
        key = zot.create([item_payload(meta, collections, tags, "journalArticle")])[0]
    cache.setdefault("items", {})[aid] = key
    return key, True, list(dict.fromkeys(collections))


def children(zot: Zotero, item_key: str) -> list[dict]:
    if item_key.startswith("DRY"):
        return []
    return zot.get_all(f"/items/{item_key}/children")


def ensure_pdf(zot: Zotero, item_key: str, aid: str, mode: str, kids: list[dict]) -> str | None:
    if mode == "none":
        return None
    pdf_url = f"https://arxiv.org/pdf/{aid}"
    pending_key = link_key = None
    for kid in kids:
        d = kid.get("data", {})
        if d.get("itemType") == "attachment" and (
                d.get("contentType") == "application/pdf" or d.get("url", "").startswith(pdf_url)):
            if mode == "upload" and d.get("linkMode") == "linked_url":
                link_key = kid["key"]
                continue  # an earlier failed download left only a link; try downloading again
            if isinstance(zot, LocalZotero) and not zot.dry_run and d.get("linkMode") == "linked_file":
                # Attanger-style external file: the attachment survives a moved/deleted PDF.
                info = zot.file_info(kid)
                if not info.get("exists"):
                    log(f"WARNING: linked PDF attachment {kid['key']} of {item_key} is broken "
                        f"(missing: {info.get('path') or info.get('error') or 'unknown path'}); "
                        "attaching a fresh copy")
                    continue
            if (isinstance(zot, LocalZotero) and mode == "upload"
                    and d.get("linkMode") == "imported_file"):
                info = zot.file_info(kid)
                if not info.get("exists"):
                    pending_key = kid["key"]
                    continue
            return kid["key"]
    if mode == "upload":
        if zot.dry_run:
            return zot.create([{"itemType": "attachment", "parentItem": item_key,
                                "linkMode": "imported_file", "title": "Preprint PDF",
                                "contentType": "application/pdf", "filename": f"{aid}.pdf",
                                "url": pdf_url}])[0]
        try:
            with urlopen(Request(pdf_url, headers={"User-Agent": UA}), timeout=120) as resp:
                pdf = resp.read()
            if not pdf.lstrip().startswith(b"%PDF-"):
                raise OSError("arXiv returned a non-PDF response")
            key = pending_key or zot.create([{"itemType": "attachment", "parentItem": item_key,
                               "linkMode": "imported_file", "title": "Preprint PDF",
                               "contentType": "application/pdf", "filename": f"{aid}.pdf",
                               "url": pdf_url}])[0]
            zot.upload_file(key, pdf, f"{aid}.pdf", "application/pdf")
            return key
        except (ZoteroError, URLError, OSError) as err:
            if isinstance(zot, LocalZotero) and isinstance(err, ZoteroError):
                # Authorization/version failures must stop; retry can reuse the pending attachment.
                raise
            log(f"PDF upload failed for {aid} ({err}); falling back to a linked URL")
    if link_key:
        return link_key
    return zot.create([{"itemType": "attachment", "parentItem": item_key, "linkMode": "linked_url",
                        "title": "Preprint PDF", "url": pdf_url, "contentType": "application/pdf"}])[0]


def _in_zotero_storage(path: str) -> bool:
    return "/storage/" in path.replace("\\", "/") and Path(path).parent.parent.name == "storage"


def wait_for_attanger(zot: Zotero, item_key: str, cfg: dict) -> dict:
    """Report where the item's PDF ended up after Attanger had a chance to process it.

    Attanger only handles attachments whose file already exists ~1 s after the item's
    "add" event. A PDF uploaded later is never picked up, so poll and report honestly.
    """
    timeout = float(cfg.get("attanger_wait_seconds", 10))
    if not isinstance(zot, LocalZotero) or zot.dry_run or cfg.get("attach_pdf") != "upload" or timeout <= 0:
        return {"status": "skipped"}
    deadline = time.monotonic() + timeout
    last: dict = {}
    while True:
        pdfs = [zot.file_info(k) for k in children(zot, item_key)
                if k.get("data", {}).get("itemType") == "attachment"
                and k.get("data", {}).get("contentType") == "application/pdf"
                and k.get("data", {}).get("linkMode") != "linked_url"]
        moved = [f for f in pdfs if f.get("exists") and f.get("link_mode") == "linked_file"]
        if moved:
            # Attanger replaces a stored attachment with a new linked one, so the key changes.
            return {"status": "moved", "path": moved[0]["path"], "key": moved[0]["key"]}
        last = next((f for f in pdfs if f.get("exists")), last)
        if time.monotonic() >= deadline:
            break
        time.sleep(1)
    if last.get("path") and _in_zotero_storage(last["path"]):
        log(f"Attanger did not move the PDF of {item_key}; it is still at {last['path']}. "
            "In Zotero: right-click the attachment -> Attanger -> Rename and Move.")
        return {"status": "not_moved", "path": last["path"]}
    return {"status": "unknown", "path": last.get("path")}


# ══════════════════════════════════════════════════════════════════════════
# Notes
# ══════════════════════════════════════════════════════════════════════════

_IMG_RE = re.compile(r'<img src="([^"]+)" alt="([^"]*)">')


def _guess_type(url: str, header: str | None) -> str:
    if header and header.startswith("image/"):
        return header.split(";")[0]
    ext = url.rsplit(".", 1)[-1].lower()
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
            "svg": "image/svg+xml", "webp": "image/webp"}.get(ext, "image/png")


def finalize_images(zot: Zotero, html: str, note_key: str | None, limit: int) -> tuple[str, int]:
    """Embed the first `limit` images as Zotero embedded images; the rest become links.

    Embedding needs the note key, so with note_key=None every image becomes a link.
    """
    embedded = 0

    def link(url: str, alt: str) -> str:
        return f'🖼 <a href="{url}">{alt or "Figure"}</a>'

    def repl(m: re.Match) -> str:
        nonlocal embedded
        url, alt = m.group(1).replace("&amp;", "&"), m.group(2)
        if note_key is None or embedded >= limit or not url.startswith("http"):
            return link(m.group(1), alt)
        try:
            with urlopen(Request(url, headers={"User-Agent": UA}), timeout=60) as resp:
                data, ctype = resp.read(), _guess_type(url, resp.headers.get("Content-Type"))
            if len(data) < 2048 or len(data) > 8 * 1024 * 1024:
                return link(m.group(1), alt)
            key = zot.create([{"itemType": "attachment", "parentItem": note_key,
                               "linkMode": "embedded_image", "contentType": ctype}])[0]
            ext = ctype.split("/")[-1].replace("jpeg", "jpg").replace("svg+xml", "svg")
            zot.upload_file(key, data, f"image.{ext}", ctype)
            embedded += 1
            caption = (f'<br><em>{alt}</em> (<a href="{m.group(1)}">{IMAGE_SOURCE_LABEL[language()]}</a>)'
                       if alt else "")
            return f'<img data-attachment-key="{key}" alt="{alt}">{caption}'
        except (ZoteroError, URLError, OSError) as err:
            log(f"image embed failed ({url}): {err}; using a link")
            return link(m.group(1), alt)

    return _IMG_RE.sub(repl, html), embedded


def note_identity(data: dict) -> tuple[list[str], str | None]:
    """Identify notes by their visible heading; read legacy tags without generating them."""
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", data.get("note", ""), re.S | re.I)
    title = html_lib.unescape(re.sub(r"<[^>]+>", "", heading.group(1))).strip() if heading else ""
    kinds, day = [], None
    kind, rest = parse_note_prefix(title)
    if kind:
        kinds = [kind]
        if kind in ("review", "digest"):
            match = re.match(r"(\d{4}-\d{2}-\d{2})(?:\s|$)", rest.strip())
            day = match.group(1) if match else None
    legacy = [t["tag"] for t in data.get("tags", [])]
    if not kinds:
        kinds = [t[3:] for t in legacy if t.startswith("dp/") and t[3:] in NOTE_KINDS]
    if not day:
        day = next((t[3:] for t in legacy if re.fullmatch(r"dp/\d{4}-\d{2}-\d{2}", t)), None)
    return kinds, day


def note_matches(data: dict, kind: str, day: str | None = None) -> bool:
    kinds, note_day = note_identity(data)
    return not data.get("deleted") and kind in kinds and (day is None or day == note_day)


def identify_note(markdown: str, kind: str | None, day: str | None) -> str:
    if not kind:
        return markdown
    if not re.fullmatch(r"[a-z][a-z0-9-]*", kind):
        raise ZoteroError("Note kind must use lowercase letters, digits and hyphens")
    lang = language()
    prefix = note_prefix(kind, lang) + ("" if lang == "zh" else " ") + (f"{day} " if day else "")
    lines = markdown.splitlines()
    if lines and re.match(r"^#\s+", lines[0]):
        title = re.sub(r"^#\s+", "", lines[0])
        existing, rest = parse_note_prefix(title)
        if existing == kind:
            title = rest.lstrip()
        if day:
            title = re.sub(r"^" + re.escape(day) + r"\s*", "", title)
        lines[0] = "# " + prefix + title
    else:
        lines = ["# " + prefix.rstrip(), "", *lines]
    return "\n".join(lines)


def clean_tags(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(t for t in tags if t and t != "dp" and not t.startswith("dp/")))


def note_summary(kid: dict) -> dict:
    d = kid.get("data", {})
    tags = [t["tag"] for t in d.get("tags", [])]
    text = re.sub(r"<[^>]+>", "", d.get("note", ""))
    kinds, day = note_identity(d)
    return {"key": kid["key"], "version": kid.get("version"), "title": text.strip().split("\n")[0][:120],
            "tags": tags, "kinds": kinds, "date": day, "text_chars": len(text),
            "math_blocks": d.get("note", "").count('class="math"'),
            "images": d.get("note", "").count("<img")}


def write_note(zot: Zotero, cfg: dict, parent: str | None, markdown: str, tags: list[str],
               collections: list[str] | None, replace: dict | None,
               kind: str | None = None, day: str | None = None) -> tuple[str, int]:
    """Create (or replace) a note. Images are embedded after the note exists."""
    html = to_note_html(identify_note(markdown, kind, day))
    has_images = bool(_IMG_RE.search(html))
    limit = 0 if isinstance(zot, LocalZotero) else int(cfg.get("max_embedded_images_per_note", 4))
    # First write: images as links, so the note is never left with broken <img>.
    first_html, _ = finalize_images(zot, html, None, 0)
    if replace and not zot.dry_run:
        current = zot.item(replace["key"])
        if current:
            tags = [t["tag"] for t in current.get("data", {}).get("tags", [])] + tags
    tag_objs = [{"tag": t} for t in clean_tags(tags)]
    if replace:
        zot.patch(replace["key"], replace["version"], {"note": first_html, "tags": tag_objs})
        key = replace["key"]
    else:
        obj = {"itemType": "note", "note": first_html, "tags": tag_objs}
        if parent:
            obj["parentItem"] = parent
        if collections:
            obj["collections"] = collections
        key = zot.create([obj])[0]
    embedded = 0
    if has_images and limit > 0 and not zot.dry_run:
        final_html, embedded = finalize_images(zot, html, key, limit)
        if embedded:
            current = zot.item(key)
            zot.patch(key, current["version"], {"note": final_html})
    elif has_images and zot.dry_run:
        log(f"[dry-run] would embed up to {limit} images into note {key}")
    return key, embedded


# ══════════════════════════════════════════════════════════════════════════
# Commands
# ══════════════════════════════════════════════════════════════════════════

def cmd_check(zot: Zotero, cfg: dict, args) -> None:
    if isinstance(zot, LocalZotero):
        zot.get("/items", {"limit": 1})
        print(json.dumps({"ok": True, "backend": "local", "library": zot.base,
                          "server_id": zot.server_id, "version": zot.version,
                          "authorization_cached": bool(zot.key)}, indent=2))
        return
    if not zot.key:
        die("No API key. Create one at https://www.zotero.org/settings/keys (allow library + notes + write).")
    _, _, raw = zot._raw("GET", f"{API}/keys/current")
    info = json.loads(raw)
    access = info.get("access", {})
    report = {"key_user_id": info.get("userID"), "username": info.get("username"),
              "configured_library": zot.base.replace(API, ""), "access": access}
    lib = access.get("user", {}) if zot.lib_type == "user" else \
        access.get("groups", {}).get(zot.lib_id) or access.get("groups", {}).get("all", {})
    problems = []
    if zot.lib_type == "user" and str(info.get("userID")) != zot.lib_id:
        problems.append(f"library_id should be your userID {info.get('userID')}")
    if not lib.get("library"):
        problems.append("key cannot read the library")
    if not lib.get("write"):
        problems.append("key lacks write access")
    if zot.lib_type == "user" and not lib.get("notes"):
        problems.append("key lacks notes access")
    if cfg.get("attach_pdf") == "upload" or int(cfg.get("max_embedded_images_per_note", 0)) > 0:
        if zot.lib_type == "user" and not lib.get("files"):
            problems.append("key lacks file access (needed for PDF upload / embedded images)")
    report["ok"] = not problems
    report["problems"] = problems
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if problems:
        raise SystemExit(1)


def cmd_setup(zot: Zotero, cfg: dict, args) -> None:
    cache = load_cache(zot)
    cache["identity"] = zot.cache_identity
    cache.pop("collections", None)
    keys = collection_keys(zot, cfg, cache)
    save_cache(cache, zot.dry_run)
    print(json.dumps(keys, ensure_ascii=False, indent=2))


def _direction_tags(dirs: list[str]) -> list[str]:
    names = {d["id"]: d["name"] for d in directions()}
    return [names[d] for d in dirs if d in names]


def cmd_collections(zot: Zotero, cfg: dict, args) -> None:
    print(json.dumps({"collections": collection_catalog(zot, samples=True)}, ensure_ascii=False, indent=2))


def cmd_check_papers(zot: Zotero, cfg: dict, args) -> None:
    """Read-only library deduplication before the Agent writes recommendations."""
    papers = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if not isinstance(papers, list):
        raise ZoteroError("check-papers expects a JSON array")
    cache, seen, result = load_cache(zot), set(), []
    for paper in papers:
        aid = norm_arxiv(paper.get("arxiv_id") or paper.get("url", ""))
        if not aid:
            raise ZoteroError("Candidate is missing a valid arXiv ID")
        if aid in seen:
            continue
        seen.add(aid)
        found = find_item(zot, aid, cache)
        notes = [note_summary(k) for k in children(zot, found["key"])
                 if k.get("data", {}).get("itemType") == "note"] if found else []
        result.append({**paper, "arxiv_id": aid, "zotero": {
            "item_key": found["key"] if found else None, "notes": notes}})
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"papers": len(result), "existing": sum(bool(p["zotero"]["item_key"]) for p in result),
                      "output": args.output}))


def cmd_push_daily(zot: Zotero, cfg: dict, args) -> None:
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    enriched = json.loads(Path(args.enriched).read_text(encoding="utf-8")) if args.enriched else []
    meta_by_id = {}
    for p in enriched:
        aid = norm_arxiv(p.get("arxiv_id") or p.get("url", ""))
        if aid:
            meta_by_id[aid] = {**p, "arxiv_id": aid}

    day = payload.get("date") or date.today().isoformat()
    write_tiers = set(cfg.get("write_tiers", ["must", "worth"]))
    papers = payload.get("papers", [])
    for p in papers:
        p["arxiv_id"] = norm_arxiv(p.get("arxiv_id", ""))
        if not p["arxiv_id"]:
            die(f"payload paper without a valid arxiv_id: {p}")
        if p.get("tier") not in TIERS:
            die(f"tier must be must/worth/skip for {p['arxiv_id']}")
        if p["tier"] in write_tiers and not (p.get("review_markdown") or "").strip():
            die(f"review_markdown is empty for {p['arxiv_id']}")
    if not (payload.get("digest_markdown") or "").strip():
        die("digest_markdown is empty")

    mode = classification_mode(cfg)
    catalog = collection_catalog(zot)
    known = {c["key"] for c in catalog}
    for paper in papers:
        if paper["tier"] not in write_tiers:
            continue
        selected = [] if mode == "pending" else paper.get("collection_keys")
        paper["collection_keys"] = selected
        if mode == "pending":
            paper["classification_reason"] = "Configured to save in pending collection"
        if not isinstance(selected, list) or any(
                not isinstance(k, str) or (k not in known and zot.dry_run is not True) for k in selected):
            raise ZoteroError("Read collections and supply collection_keys for every saved paper; [] means the pending collection")
    cache = load_cache(zot)
    cache["identity"] = zot.cache_identity
    result = {"date": day, "library": zot.base.replace(API, ""), "classification_mode": mode,
              "papers": {}, "errors": [], "stage": "deduplicated"}
    seen = set()
    for paper in papers:
        aid = paper["arxiv_id"]
        if aid in seen:
            raise ZoteroError(f"Duplicate arXiv ID in payload: {aid}")
        seen.add(aid)
        found = find_item(zot, aid, cache)
        result["papers"][aid] = {"tier": paper["tier"], "title": meta_by_id.get(aid, {}).get("title", ""),
                                  "existing_item_key": found["key"] if found else None}
    out = Path(args.output) if args.output else temp_file_path("zotero_push_result.json")
    def checkpoint():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(out)
        save_cache(cache, zot.dry_run)

    def digest_markdown():
        def link(m):
            aid = norm_arxiv(m.group(1))
            entry = result["papers"].get(aid, {})
            key = entry.get("item_key") or entry.get("existing_item_key")
            return f"]({zot.select_link(key) if key else f'https://arxiv.org/abs/{aid}'})"
        return re.sub(r"\]\(zotero:([^)\s]+)\)", link, payload["digest_markdown"])

    cols = collection_keys(zot, cfg, cache)
    digest_tags = []
    existing = [e for e in zot.get_all(f"/collections/{cols['digest']}/items", {"itemType": "note"})
                if not e.get("data", {}).get("parentItem")
                and note_matches(e.get("data", {}), "digest", day)]
    try:
        digest_key, _ = write_note(zot, cfg, None, digest_markdown(), digest_tags, [cols["digest"]],
                                   {"key": existing[0]["key"], "version": existing[0]["version"]}
                                   if existing else None, kind="digest", day=day)
        result.update(digest_note_key=digest_key, digest_link=zot.select_link(digest_key), stage="digest_saved")
        checkpoint()
    except (ZoteroError, URLError, OSError) as err:
        result["errors"].append(f"digest: {err}")
        result["stage"] = "digest_failed"
        checkpoint()
        raise ZoteroError(f"Digest failed; no papers were saved: {err}") from None

    to_write = [p["arxiv_id"] for p in papers if p["tier"] in write_tiers]
    try:
        arxiv_meta = fetch_arxiv_meta_batch(to_write) if to_write else {}
    except (URLError, OSError, ET.ParseError) as err:
        log(f"arXiv metadata lookup failed ({err}); using fetched candidate metadata")
        arxiv_meta = {}
    for p in papers:
        aid, tier = p["arxiv_id"], p["tier"]
        entry = result["papers"][aid]
        if tier not in write_tiers:
            continue
        try:
            meta = ({**meta_by_id.get(aid, {}), **arxiv_meta[aid]} if aid in arxiv_meta
                    else meta_by_id.get(aid) or fetch_arxiv_meta(aid))
            entry["title"] = meta.get("title", "")
            dirs = p.get("directions") or []
            selected = select_collections(zot, cfg, p["collection_keys"], catalog)
            tags = clean_tags([*_direction_tags(dirs), *p.get("tags", [])])
            fallback = selected[0] if not p["collection_keys"] else None
            key, created, final_cols = ensure_paper(zot, meta, selected, tags, cache, fallback_collection=fallback)
            entry.update(item_key=key, created=created, zotero_link=zot.select_link(key),
                         collection_keys=final_cols, requested_collection_keys=selected,
                         classification_reason=p.get("classification_reason", ""))
            kids = children(zot, key)
            entry["pdf_key"] = ensure_pdf(zot, key, aid, cfg.get("attach_pdf", "link"), kids)
            review_tags = []
            same_day = next((k for k in kids if k.get("data", {}).get("itemType") == "note"
                             and note_matches(k.get("data", {}), "review", day)), None)
            note_key, _ = write_note(zot, cfg, key, p["review_markdown"], review_tags, None,
                                     {"key": same_day["key"], "version": same_day["version"]}
                                     if same_day else None, kind="review", day=day)
            entry["review_note_key"] = note_key
            # Checked after the review note: its "add" event also re-arms Attanger's timer.
            entry["attanger"] = wait_for_attanger(zot, key, cfg)
            if entry["attanger"].get("key"):
                entry["pdf_key"] = entry["attanger"]["key"]
            log(f"{aid} [{tier}] -> {key} ({'new' if created else 'existing'})")
        except (ZoteroError, URLError, OSError, ET.ParseError) as err:
            entry["error"] = str(err)
            result["errors"].append(f"{aid}: {err}")
            log(f"FAILED {aid}: {err}")
        checkpoint()

    # The overview already exists; only refresh its links after paper insertion.
    digest_md = digest_markdown()
    if zot.dry_run:
        temp_file_path("zotero_digest_preview.html").write_text(to_note_html(digest_md), encoding="utf-8")
    else:
        try:
            current = zot.item(digest_key)
            if not current:
                raise ZoteroError("Daily digest disappeared before link refresh")
            write_note(zot, cfg, None, digest_md, digest_tags, [cols["digest"]],
                       {"key": digest_key, "version": current["version"]}, kind="digest", day=day)
        except (ZoteroError, URLError, OSError) as err:
            result["errors"].append(f"digest links: {err}")

    # History (used by fetch_and_score.py for cross-day dedup), keep 60 days.
    if not zot.dry_run:
        history = load_history()
        known = {h.get("id") for h in history}
        for aid, entry in result["papers"].items():
            if aid not in known and not entry.get("error"):
                history.append({"id": aid, "date": day, "title": entry.get("title", ""),
                                "tier": entry["tier"], "item_key": entry.get("item_key")})
        cutoff = (datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=60)).isoformat()
        save_history([h for h in history if h.get("date", "") >= cutoff])
    save_cache(cache, zot.dry_run)

    result["stage"] = "papers_partial" if result["errors"] else "papers_saved"
    result["reading_queue"] = [{"arxiv_id": aid, "item_key": e["item_key"]}
                               for aid, e in result["papers"].items()
                               if e["tier"] == "must" and e.get("item_key") and not e.get("error")]
    checkpoint()
    written = sum(1 for e in result["papers"].values() if e.get("item_key"))
    print(f"Wrote {written} papers + digest to Zotero; result: {out}"
          + (f"; {len(result['errors'])} errors" if result["errors"] else ""))
    if result["errors"]:
        raise SystemExit(2)


def _resolve_item(zot: Zotero, args, cache: dict) -> str:
    if getattr(args, "item", None):
        return args.item
    aid = norm_arxiv(args.arxiv or "")
    if not aid:
        die("give --item KEY or a valid --arxiv ID")
    found = find_item(zot, aid, cache)
    if not found:
        die(f"no Zotero item for arXiv {aid}; run upsert-paper first", 3)
    return found["key"]


def cmd_upsert(zot: Zotero, cfg: dict, args) -> None:
    aid = norm_arxiv(args.arxiv)
    if not aid:
        die("invalid arXiv id")
    cache = load_cache(zot)
    cache["identity"] = zot.cache_identity
    meta = fetch_arxiv_meta(aid)
    mode = classification_mode(cfg)
    requested = args.collections
    if requested is None and mode == "pending":
        requested = []
    catalog = collection_catalog(zot, samples=requested is None)
    if requested is None:
        print(json.dumps({"status": "awaiting_classification", "paper": meta,
                          "collections": catalog,
                          "next": "Agent: choose existing collection keys by topic, then rerun with --collections KEY...; empty --collections means no suitable category (待定)."},
                         ensure_ascii=False, indent=2))
        return
    selected = select_collections(zot, cfg, requested, catalog)
    tags = clean_tags([*_direction_tags(args.directions or []), *(args.tags or [])])
    fallback = selected[0] if not requested else None
    key, created, final_cols = ensure_paper(zot, meta, selected, tags, cache, fallback_collection=fallback)
    pdf = ensure_pdf(zot, key, aid, cfg.get("attach_pdf", "link"), children(zot, key))
    attanger = wait_for_attanger(zot, key, cfg)
    if attanger.get("key"):
        pdf = attanger["key"]
    save_cache(cache, zot.dry_run)
    print(json.dumps({"arxiv_id": aid, "item_key": key, "created": created, "pdf_key": pdf,
                      "attanger": attanger,
                      "title": meta["title"], "collection_keys": final_cols,
                      "requested_collection_keys": selected,
                      "classification_reason": args.classification_reason,
                      "zotero_link": zot.select_link(key)},
                     ensure_ascii=False, indent=2))


def cmd_add_note(zot: Zotero, cfg: dict, args) -> None:
    cache = load_cache(zot)
    cache["identity"] = zot.cache_identity
    key = _resolve_item(zot, args, cache)
    markdown = Path(args.markdown).read_text(encoding="utf-8")
    tags = clean_tags(args.tags or [])
    replace = None
    same = [note_summary(k) for k in children(zot, key)
            if k.get("data", {}).get("itemType") == "note"]
    same = [n for n in same if args.kind in n["kinds"]]
    if same and not args.replace:
        die(f"item {key} already has a '{args.kind}' note ({same[0]['key']}, "
            f"{same[0]['text_chars']} chars). Pass --replace to overwrite it.", 4)
    if same:
        replace = {"key": same[0]["key"], "version": same[0]["version"]}
    note_key, embedded = write_note(zot, cfg, key, markdown, tags, None, replace, kind=args.kind)
    save_cache(cache, zot.dry_run)
    print(json.dumps({"item_key": key, "note_key": note_key, "replaced": bool(replace),
                      "embedded_images": embedded, "zotero_link": zot.select_link(key)}, indent=2))


def cmd_notes(zot: Zotero, cfg: dict, args) -> None:
    cache = load_cache(zot)
    cache["identity"] = zot.cache_identity
    key = _resolve_item(zot, args, cache)
    notes = [note_summary(k) for k in children(zot, key)
             if k.get("data", {}).get("itemType") == "note"]
    print(json.dumps({"item_key": key, "notes": notes}, ensure_ascii=False, indent=2))


def cmd_find(zot: Zotero, cfg: dict, args) -> None:
    cache = load_cache(zot)
    cache["identity"] = zot.cache_identity
    aid = norm_arxiv(args.arxiv)
    found = find_item(zot, aid, cache) if aid else None
    save_cache(cache, zot.dry_run)
    if not found:
        print(json.dumps({"arxiv_id": aid, "item_key": None}))
        raise SystemExit(3)
    print(json.dumps({"arxiv_id": aid, "item_key": found["key"],
                      "title": found["data"].get("title"), "zotero_link": zot.select_link(found["key"])},
                     ensure_ascii=False, indent=2))


def cmd_authorize(zot: Zotero, cfg: dict, args) -> None:
    if not isinstance(zot, LocalZotero):
        raise ZoteroError("authorize is for the local backend")
    zot.authorize()
    print(json.dumps({"authorized": not zot.dry_run}))


def cmd_attachments(zot: Zotero, cfg: dict, args) -> None:
    if not isinstance(zot, LocalZotero):
        raise ZoteroError("attachments requires the local backend")
    key = _resolve_item(zot, args, load_cache(zot))
    files = [zot.file_info(k) for k in children(zot, key)
             if k.get("data", {}).get("itemType") == "attachment"]
    print(json.dumps({"item_key": key, "attachments": files}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="log planned writes; change nothing")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("authorize")
    sub.add_parser("setup")
    sub.add_parser("collections")
    p = sub.add_parser("check-papers")
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p = sub.add_parser("push-daily")
    p.add_argument("--payload", required=True)
    p.add_argument("--enriched")
    p.add_argument("--output")
    p = sub.add_parser("upsert-paper")
    p.add_argument("--arxiv", required=True)
    p.add_argument("--directions", nargs="*", help="optional research tags only; does not select collections")
    p.add_argument("--collections", nargs="*", help="existing collection keys chosen by Agent; empty means the pending collection")
    p.add_argument("--classification-reason", default="", help="reason for the selected collections")
    p.add_argument("--tags", nargs="*")
    for name in ("add-note", "notes", "attachments"):
        p = sub.add_parser(name)
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--arxiv")
        g.add_argument("--item")
        if name == "add-note":
            p.add_argument("--markdown", required=True)
            p.add_argument("--kind", default="full-note", help="note type identified by heading, e.g. full-note, quick")
            p.add_argument("--tags", nargs="*")
            p.add_argument("--replace", action="store_true")
    p = sub.add_parser("find")
    p.add_argument("--arxiv", required=True)
    args = parser.parse_args()

    handler = {"check": cmd_check, "setup": cmd_setup, "push-daily": cmd_push_daily,
               "upsert-paper": cmd_upsert, "add-note": cmd_add_note, "notes": cmd_notes,
               "find": cmd_find, "attachments": cmd_attachments, "authorize": cmd_authorize,
               "collections": cmd_collections, "check-papers": cmd_check_papers}[args.command]
    try:
        cfg = zotero_config()
        backend = cfg.get("backend", "local")
        if backend not in ("local", "web"):
            raise ZoteroError("zotero.backend must be local or web")
        zot = (LocalZotero if backend == "local" else Zotero)(cfg, dry_run=args.dry_run)
        handler(zot, cfg, args)
    except (ZoteroError, URLError, OSError, ValueError) as err:
        die(str(err))


if __name__ == "__main__":
    main()
