#!/usr/bin/env python3
"""Convert the Markdown subset used by this skill into Zotero note HTML.

Zotero 7's note editor renders math stored as
    inline:  <span class="math">$...$</span>
    display: <pre class="math">$$...$$</pre>
and supports headings, paragraphs, lists, blockquotes, tables, code and links.
Images are emitted as <img src="URL" alt="..."> and are finalized by
zotero_push.py (uploaded as embedded images or turned into figure links).

Supported Markdown: ATX headings, paragraphs, **bold**, *italic*, ~~strike~~,
`code`, [links](url), <autolinks>, ![images](url), fenced code, $$ blocks,
\\[ \\] blocks, inline $..$ and \\( \\), nested -/*/+ and 1. lists,
> blockquotes, pipe tables, --- rules. No external dependencies.

CLI: python3 md2zotero.py note.md [--out note.html]
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

_TOKEN = "\u0000{}\u0000"
_TOKEN_RE = re.compile("\u0000(\\d+)\u0000")


class _Stash:
    def __init__(self) -> None:
        self.items: list[str] = []

    def put(self, value: str) -> str:
        self.items.append(value)
        return _TOKEN.format(len(self.items) - 1)

    def restore(self, text: str) -> str:
        # Tokens may nest (e.g. link text containing code), so loop until stable.
        for _ in range(5):
            new = _TOKEN_RE.sub(lambda m: self.items[int(m.group(1))], text)
            if new == text:
                break
            text = new
        return text


def _math_inline(tex: str) -> str:
    return f'<span class="math">${html.escape(tex.strip(), quote=False)}$</span>'


def _math_block(tex: str) -> str:
    return f'<pre class="math">$${html.escape(tex.strip(), quote=False)}$$</pre>'


_INLINE_MATH = re.compile(r"(?<![\\$])\$(?!\s)([^$\n]+?)(?<![\s\\])\$(?![\d$])")
_PAREN_MATH = re.compile(r"\\\((.+?)\\\)")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_LINK = re.compile(r"\[([^\]]+)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")
_BARE_URL = re.compile(r"(?<![\"'=(>\w])(https?://[^\s<>()\"']+[^\s<>()\"'.,;:!?])")


def _safe_url(url: str) -> str:
    url = url.strip()
    if re.match(r"^(https?|zotero|mailto):", url, re.I) or url.startswith("#"):
        return html.escape(url, quote=True)
    return "#"


def inline(text: str) -> str:
    stash = _Stash()
    text = _CODE_SPAN.sub(lambda m: stash.put(f"<code>{html.escape(m.group(2), quote=False)}</code>"), text)
    text = _PAREN_MATH.sub(lambda m: stash.put(_math_inline(m.group(1))), text)
    text = _INLINE_MATH.sub(lambda m: stash.put(_math_inline(m.group(1))), text)
    text = _IMAGE.sub(lambda m: stash.put(
        f'<img src="{_safe_url(m.group(2))}" alt="{html.escape(m.group(1), quote=True)}">'), text)

    def link(m: re.Match) -> str:
        label = inline_basic(html.escape(m.group(1), quote=False), stash)
        return stash.put(f'<a href="{_safe_url(m.group(2))}">{label}</a>')

    text = _LINK.sub(link, text)
    text = _AUTOLINK.sub(lambda m: stash.put(f'<a href="{_safe_url(m.group(1))}">{html.escape(m.group(1))}</a>'), text)
    text = html.escape(text, quote=False)
    text = _BARE_URL.sub(lambda m: stash.put(f'<a href="{_safe_url(html.unescape(m.group(1)))}">{m.group(1)}</a>'), text)
    text = inline_basic(text, stash)
    text = text.replace("  \n", "<br>").replace("\n", " ")
    return stash.restore(text)


def inline_basic(text: str, stash: _Stash | None = None) -> str:
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\*\w])", r"<em>\1</em>", text)
    text = re.sub(r"(?<![_\w])_(?=\S)(.+?)(?<=\S)_(?![_\w])", r"<em>\1</em>", text)
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", r"<s>\1</s>", text)
    return text


# ── block level ─────────────────────────────────────────────────────────────

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^(\s*)(```+|~~~+)\s*([\w+-]*)\s*$")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _split_row(line: str) -> list[str]:
    """Split a pipe-table row, ignoring pipes inside `code`, $math$ or escaped \\|."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells, buf, in_code, in_math, i = [], [], False, False, 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if ch == "`":
            in_code = not in_code
        elif ch == "$" and not in_code:
            in_math = not in_math
        if ch == "|" and not in_code and not in_math:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t").replace("\t", "    ")) if line.strip() else 0


def _render_list(lines: list[str]) -> str:
    """Render consecutive list lines (with continuation lines) into nested lists."""
    items_tree: list[dict] = []
    current_stack: list[tuple[int, dict]] = []  # (indent, node)

    def new_node(ordered: bool, text: str) -> dict:
        return {"ordered": ordered, "text": [text], "children": []}

    for line in lines:
        m = _LIST_ITEM.match(line)
        if m:
            ind = len(m.group(1).replace("\t", "    "))
            ordered = m.group(2)[0].isdigit()
            node = new_node(ordered, m.group(3))
            while current_stack and current_stack[-1][0] >= ind:
                current_stack.pop()
            if current_stack:
                current_stack[-1][1]["children"].append(node)
            else:
                items_tree.append(node)
            current_stack.append((ind, node))
        elif current_stack:
            current_stack[-1][1]["text"].append(line.strip())

    def render(nodes: list[dict]) -> str:
        # Consecutive siblings of the same kind share one <ul>/<ol>.
        html_parts, run, run_ordered = [], [], None
        for node in nodes + [None]:
            if node is None or (run and node["ordered"] != run_ordered):
                if run:
                    tag = "ol" if run_ordered else "ul"
                    html_parts.append(f"<{tag}>{''.join(run)}</{tag}>")
                run = []
            if node is None:
                break
            run_ordered = node["ordered"]
            body = inline("\n".join(t for t in node["text"] if t))
            body = re.sub(r"^\[ \]\s*", "☐ ", body)
            body = re.sub(r"^\[[xX]\]\s*", "☑ ", body)
            run.append(f"<li>{body}{render(node['children'])}</li>")
        return "".join(html_parts)

    return render(items_tree)


def convert(markdown: str) -> str:
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    # Strip YAML front matter.
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            text = text[end + 4:].lstrip("\n")
    # Drop HTML comments (template hints).
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    para: list[str] = []

    def flush_para() -> None:
        if para:
            out.append(f"<p>{inline(chr(10).join(para))}</p>")
            para.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            flush_para()
            i += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            flush_para()
            marker = fence.group(2)
            lang = fence.group(3)
            body = []
            i += 1
            while i < n and not lines[i].strip().startswith(marker):
                body.append(lines[i])
                i += 1
            i += 1
            code = "\n".join(body)
            if lang in ("math", "latex", "tex"):
                out.append(_math_block(code))
            else:
                out.append(f"<pre>{html.escape(code, quote=False)}</pre>")
            continue

        if stripped.startswith("$$") or stripped.startswith("\\["):
            flush_para()
            opener, closer = ("$$", "$$") if stripped.startswith("$$") else ("\\[", "\\]")
            rest = stripped[len(opener):]
            if rest.rstrip().endswith(closer) and rest.strip() != "":
                out.append(_math_block(rest.rstrip()[: -len(closer)]))
                i += 1
                continue
            body = [rest] if rest else []
            i += 1
            while i < n and closer not in lines[i]:
                body.append(lines[i])
                i += 1
            if i < n:
                tail = lines[i].split(closer, 1)[0]
                if tail.strip():
                    body.append(tail)
            i += 1
            out.append(_math_block("\n".join(body)))
            continue

        heading = _HEADING.match(stripped)
        if heading:
            flush_para()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        if _HR.match(stripped):
            flush_para()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith(">"):
            flush_para()
            block = []
            while i < n and lines[i].strip().startswith(">"):
                block.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(f"<blockquote>{convert(chr(10).join(block))}</blockquote>")
            continue

        if "|" in stripped and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            flush_para()
            header = _split_row(line)
            i += 2
            rows = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_row(lines[i]))
                i += 1
            head_html = "".join(f"<th>{inline(c)}</th>" for c in header)
            body_html = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>" for row in rows
            )
            out.append(f"<table><tr>{head_html}</tr>{body_html}</table>")
            continue

        if _LIST_ITEM.match(line) and not para:
            block = []
            while i < n:
                cur = lines[i]
                if _LIST_ITEM.match(cur) or (cur.strip() and cur.startswith((" ", "\t")) and block):
                    block.append(cur)
                    i += 1
                elif not cur.strip() and i + 1 < n and (
                        _LIST_ITEM.match(lines[i + 1]) or lines[i + 1].startswith(("  ", "\t"))):
                    i += 1
                else:
                    break
            out.append(_render_list(block))
            continue

        para.append(stripped if not line.endswith("  ") else stripped + "  ")
        i += 1

    flush_para()
    return "\n".join(out)


def to_note_html(markdown: str) -> str:
    return f"<div>\n{convert(markdown)}\n</div>"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = to_note_html(args.markdown.read_text(encoding="utf-8"))
    if args.out:
        args.out.write_text(result, encoding="utf-8")
    else:
        sys.stdout.write(result + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
