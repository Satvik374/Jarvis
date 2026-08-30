"""Structured Web Data & Table Extraction Engine for Jarvis.

Extracts HTML tables, page metadata (OpenGraph, JSON-LD, Twitter cards),
categorized hyperlinks (internal, external, downloads), and clean article text
from live URLs or raw HTML documents without heavy external dependencies.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .files import _expand


# --------------------------------------------------------------------------- #
# Table Parser
# --------------------------------------------------------------------------- #

class HTMLTableParser(HTMLParser):
    """Parse HTML tables into structured rows and columns."""

    def __init__(self):
        super().__init__()
        self.tables: List[List[List[str]]] = []
        self._current_table: List[List[str]] = []
        self._current_row: List[str] = []
        self._current_cell: List[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        t = tag.lower()
        if t == "table":
            self._current_table = []
        elif t == "tr":
            self._current_row = []
        elif t in ("th", "td"):
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str):
        t = tag.lower()
        if t in ("th", "td"):
            self._in_cell = False
            cell_text = " ".join("".join(self._current_cell).split())
            self._current_row.append(cell_text)
            self._current_cell = []
        elif t == "tr":
            if self._current_row:
                self._current_table.append(self._current_row)
                self._current_row = []
        elif t == "table":
            if self._current_table:
                self.tables.append(self._current_table)
                self._current_table = []

    def handle_data(self, data: str):
        if self._in_cell:
            self._current_cell.append(data)


# --------------------------------------------------------------------------- #
# Metadata & OpenGraph Parser
# --------------------------------------------------------------------------- #

class MetadataParser(HTMLParser):
    """Extract page title, meta description, OpenGraph, Twitter, and JSON-LD schemas."""

    def __init__(self):
        super().__init__()
        self.title: str = ""
        self.meta: Dict[str, str] = {}
        self.og: Dict[str, str] = {}
        self.twitter: Dict[str, str] = {}
        self.canonical: str = ""
        self.json_ld: List[Any] = []
        self._in_title = False
        self._in_json_ld = False
        self._title_parts: List[str] = []
        self._json_ld_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        t = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if t == "title":
            self._in_title = True
            self._title_parts = []
        elif t == "link" and attr_dict.get("rel") == "canonical":
            self.canonical = attr_dict.get("href", "")
        elif t == "meta":
            name = attr_dict.get("name", "").lower()
            prop = attr_dict.get("property", "").lower()
            content = attr_dict.get("content", "")

            if name:
                self.meta[name] = content
                if name.startswith("twitter:"):
                    self.twitter[name.replace("twitter:", "")] = content
            if prop:
                if prop.startswith("og:"):
                    self.og[prop.replace("og:", "")] = content
                else:
                    self.meta[prop] = content

        elif t == "script" and attr_dict.get("type") == "application/ld+json":
            self._in_json_ld = True
            self._json_ld_parts = []

    def handle_endtag(self, tag: str):
        t = tag.lower()
        if t == "title":
            self._in_title = False
            self.title = " ".join("".join(self._title_parts).split())
        elif t == "script" and self._in_json_ld:
            self._in_json_ld = False
            raw = "".join(self._json_ld_parts).strip()
            if raw:
                try:
                    parsed = json.loads(raw)
                    self.json_ld.append(parsed)
                except Exception:
                    pass

    def handle_data(self, data: str):
        if self._in_title:
            self._title_parts.append(data)
        elif self._in_json_ld:
            self._json_ld_parts.append(data)


# --------------------------------------------------------------------------- #
# Link Extractor
# --------------------------------------------------------------------------- #

_DOWNLOAD_EXTS = {".pdf", ".zip", ".tar.gz", ".tgz", ".gz", ".csv", ".xlsx", ".docx", ".exe", ".msi", ".dmg", ".pkg", ".iso"}


class LinkParser(HTMLParser):
    """Extract and categorize all hyperlinks from HTML."""

    def __init__(self, base_url: str = ""):
        super().__init__()
        self.base_url = base_url
        self.base_domain = urllib.parse.urlparse(base_url).netloc.lower() if base_url else ""
        self.internal_links: List[Dict[str, str]] = []
        self.external_links: List[Dict[str, str]] = []
        self.downloads: List[Dict[str, str]] = []
        self._current_href: Optional[str] = None
        self._current_text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]):
        if tag.lower() == "a":
            attr_dict = {k.lower(): (v or "") for k, v in attrs}
            href = attr_dict.get("href", "").strip()
            if href and not href.startswith("#") and not href.startswith("javascript:"):
                self._current_href = href
                self._current_text = []

    def handle_endtag(self, tag: str):
        if tag.lower() == "a" and self._current_href is not None:
            text = " ".join("".join(self._current_text).split())
            href = self._current_href
            if self.base_url and not (href.startswith("http://") or href.startswith("https://")):
                href = urllib.parse.urljoin(self.base_url, href)

            entry = {"url": href, "text": text or href}
            parsed_href = urllib.parse.urlparse(href)
            path_low = parsed_href.path.lower()

            # Check download extension
            is_dl = any(path_low.endswith(ext) for ext in _DOWNLOAD_EXTS)
            if is_dl:
                self.downloads.append(entry)
            elif self.base_domain and parsed_href.netloc.lower() == self.base_domain:
                self.internal_links.append(entry)
            elif self.base_domain:
                self.external_links.append(entry)
            else:
                self.internal_links.append(entry)

            self._current_href = None
            self._current_text = []

    def handle_data(self, data: str):
        if self._current_href is not None:
            self._current_text.append(data)


# --------------------------------------------------------------------------- #
# Article Content Cleaner
# --------------------------------------------------------------------------- #

def extract_article_text(html: str) -> str:
    """Strip navigation, script, and footer boilerplate and extract clean article text."""
    # Remove script, style, nav, footer, header tags
    clean = re.sub(r"<(script|style|nav|footer|header|aside|noscript|form)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Convert headings to markdown
    clean = re.sub(r"<h1[^>]*>(.*?)</h1>", r"\n# \1\n", clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"<h2[^>]*>(.*?)</h2>", r"\n## \1\n", clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"<h3[^>]*>(.*?)</h3>", r"\n### \1\n", clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"<li[^>]*>(.*?)</li>", r"\n• \1", clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r"<p[^>]*>(.*?)</p>", r"\n\1\n", clean, flags=re.DOTALL | re.IGNORECASE)
    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", " ", clean)
    # Decode HTML entities
    import html as html_lib
    text = html_lib.unescape(text)
    # Collapse extra whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:300])


# --------------------------------------------------------------------------- #
# Fetch Helper
# --------------------------------------------------------------------------- #

def fetch_html(url: str, timeout: int = 20) -> str:
    """Fetch raw HTML from URL with realistic browser headers."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read()
        return raw.decode(charset, errors="replace")


# --------------------------------------------------------------------------- #
# Main Entrypoint
# --------------------------------------------------------------------------- #

def extract_web_data(
    url: str = "",
    html_content: str = "",
    mode: str = "tables",
    output_format: str = "json",
    timeout: int = 20,
    allow: tuple[str, ...] = (),
) -> str:
    """Extract structured data from live web pages or HTML content.

    Modes:
      - 'tables': Extract all HTML tables into structured rows/columns (JSON or CSV).
      - 'metadata': Extract page title, meta tags, OpenGraph, and JSON-LD microdata.
      - 'links': Extract hyperlinks categorized as internal, external, or downloads.
      - 'article': Extract clean, readable article text with boilerplate stripped.
    """
    html = html_content.strip()
    if not html:
        u = url.strip()
        if not u:
            return "extract_web_data requires either a 'url' or 'html_content'"
        try:
            html = fetch_html(u, timeout=timeout)
        except Exception as exc:
            return f"failed to fetch URL '{u}': {exc}"

    mode_clean = (mode or "tables").strip().lower()
    fmt = (output_format or "json").strip().lower()

    # 1. Tables Mode
    if mode_clean in ("tables", "table", "tabular"):
        parser = HTMLTableParser()
        parser.feed(html)
        if not parser.tables:
            return "no <table> elements found in HTML content"

        results = []
        for idx, tbl in enumerate(parser.tables, 1):
            if not tbl:
                continue
            headers = tbl[0]
            rows = tbl[1:]
            if fmt == "csv":
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerows(tbl)
                results.append(f"--- Table {idx} (CSV) ---\n" + buf.getvalue().strip())
            else:
                # Convert rows to dicts if unique headers exist
                if len(set(headers)) == len(headers) and headers:
                    row_dicts = [dict(zip(headers, r)) for r in rows]
                    results.append(json.dumps({"table_id": idx, "headers": headers, "rows": row_dicts}, indent=2))
                else:
                    results.append(json.dumps({"table_id": idx, "data": tbl}, indent=2))

        return "\n\n".join(results)

    # 2. Metadata Mode
    elif mode_clean in ("metadata", "meta", "opengraph", "schema"):
        meta_parser = MetadataParser()
        meta_parser.feed(html)
        data = {
            "title": meta_parser.title,
            "canonical": meta_parser.canonical,
            "meta_tags": meta_parser.meta,
            "open_graph": meta_parser.og,
            "twitter_card": meta_parser.twitter,
            "json_ld": meta_parser.json_ld,
        }
        return json.dumps(data, indent=2)

    # 3. Links Mode
    elif mode_clean in ("links", "urls", "downloads"):
        link_parser = LinkParser(base_url=url)
        link_parser.feed(html)
        data = {
            "total_links": len(link_parser.internal_links) + len(link_parser.external_links) + len(link_parser.downloads),
            "downloads": link_parser.downloads[:30],
            "internal_links": link_parser.internal_links[:50],
            "external_links": link_parser.external_links[:50],
        }
        return json.dumps(data, indent=2)

    # 4. Article Readability Mode
    elif mode_clean in ("article", "text", "readability", "content"):
        return extract_article_text(html)

    return f"unknown extract_web_data mode '{mode}' - supported: tables, metadata, links, article"
