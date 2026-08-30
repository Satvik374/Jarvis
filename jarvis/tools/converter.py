"""Universal File Converter and Document Transformer for Jarvis.

Provides fast, standalone conversion across document formats, tabular data,
images, and encodings without requiring heavy external dependencies.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .files import _expand, _within, read_file


# --------------------------------------------------------------------------- #
# Markdown to HTML Converter with Rich Embedded Styling
# --------------------------------------------------------------------------- #

_HTML_THEMES = {
    "dark": {
        "bg": "#0f172a",
        "card_bg": "#1e293b",
        "text": "#e2e8f0",
        "muted": "#94a3b8",
        "accent": "#38bdf8",
        "code_bg": "#090d16",
        "border": "#334155",
        "table_stripe": "#1e293b",
        "table_header": "#334155",
        "blockquote_border": "#38bdf8",
    },
    "light": {
        "bg": "#f8fafc",
        "card_bg": "#ffffff",
        "text": "#1e293b",
        "muted": "#64748b",
        "accent": "#0284c7",
        "code_bg": "#f1f5f9",
        "border": "#e2e8f0",
        "table_stripe": "#f8fafc",
        "table_header": "#e2e8f0",
        "blockquote_border": "#0284c7",
    },
    "cyber": {
        "bg": "#0a0a0f",
        "card_bg": "#12121a",
        "text": "#00f0ff",
        "muted": "#708090",
        "accent": "#ff007f",
        "code_bg": "#050508",
        "border": "#1f1f2e",
        "table_stripe": "#151522",
        "table_header": "#222235",
        "blockquote_border": "#00f0ff",
    }
}


def _markdown_to_html_body(md_text: str) -> str:
    """Parse common Markdown into clean HTML."""
    lines = md_text.replace("\r\n", "\n").split("\n")
    out: List[str] = []
    in_code = False
    code_lang = ""
    code_buf: List[str] = []
    in_ul = False
    in_ol = False
    in_table = False
    table_rows: List[List[str]] = []

    def _close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def _close_table():
        nonlocal in_table, table_rows
        if in_table and table_rows:
            html = ["<div class=\"table-container\"><table>"]
            if len(table_rows) > 0:
                html.append("<thead><tr>")
                for cell in table_rows[0]:
                    html.append(f"<th>{_inline_md(cell)}</th>")
                html.append("</tr></thead>")
            if len(table_rows) > 1:
                html.append("<tbody>")
                for row in table_rows[1:]:
                    html.append("<tr>")
                    for cell in row:
                        html.append(f"<td>{_inline_md(cell)}</td>")
                    html.append("</tr>")
                html.append("</tbody>")
            html.append("</table></div>")
            out.append("\n".join(html))
            table_rows = []
            in_table = False

    def _inline_md(text: str) -> str:
        # Escape HTML entities first
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Bold & Italic
        text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
        text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
        text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
        # Inline code
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        # Links: [text](url)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text)
        # Checkboxes
        text = re.sub(r"^\[ \]\s*", '<input type="checkbox" disabled> ', text)
        text = re.sub(r"^\[x\]\s*", '<input type="checkbox" checked disabled> ', text)
        return text

    i = 0
    while i < len(lines):
        line = lines[i]

        # Code block fence
        if line.startswith("```"):
            if in_code:
                _close_lists()
                code_content = "\n".join(code_buf).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                cls_attr = f' class="language-{code_lang}"' if code_lang else ""
                out.append(f'<pre><code{cls_attr}>{code_content}</code></pre>')
                code_buf = []
                in_code = False
            else:
                _close_lists()
                _close_table()
                in_code = True
                code_lang = line[3:].strip()
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        trimmed = line.strip()

        # Empty line
        if not trimmed:
            _close_lists()
            _close_table()
            i += 1
            continue

        # Markdown Table
        if "|" in line and (line.startswith("|") or line.endswith("|") or len(line.split("|")) >= 3):
            parts = [p.strip() for p in line.split("|")]
            if parts and parts[0] == "":
                parts.pop(0)
            if parts and parts[-1] == "":
                parts.pop(-1)
            # Check if separator line
            if all(re.match(r"^:?-+:?$", p) for p in parts if p):
                i += 1
                continue
            if not in_table:
                _close_lists()
                in_table = True
                table_rows = []
            table_rows.append(parts)
            i += 1
            continue
        elif in_table:
            _close_table()

        # Headings
        if line.startswith("#"):
            _close_lists()
            level = len(line) - len(line.lstrip("#"))
            if 1 <= level <= 6:
                content = _inline_md(line[level:].strip())
                out.append(f"<h{level}>{content}</h{level}>")
                i += 1
                continue

        # Blockquote
        if line.startswith(">"):
            _close_lists()
            quote_text = _inline_md(line[1:].strip())
            out.append(f"<blockquote><p>{quote_text}</p></blockquote>")
            i += 1
            continue

        # Horizontal rule
        if trimmed in ("---", "***", "___") or re.match(r"^[-*_]{3,}$", trimmed):
            _close_lists()
            out.append("<hr>")
            i += 1
            continue

        # Unordered list
        if trimmed.startswith(("- ", "* ", "+ ")):
            if in_ol:
                _close_lists()
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            content = _inline_md(trimmed[2:])
            out.append(f"  <li>{content}</li>")
            i += 1
            continue

        # Ordered list
        m_ol = re.match(r"^(\d+)\.\s+(.*)$", trimmed)
        if m_ol:
            if in_ul:
                _close_lists()
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            content = _inline_md(m_ol.group(2))
            out.append(f"  <li>{content}</li>")
            i += 1
            continue

        # Regular paragraph
        _close_lists()
        out.append(f"<p>{_inline_md(trimmed)}</p>")
        i += 1

    _close_lists()
    _close_table()
    return "\n".join(out)


def markdown_to_html(
    md_text: str,
    title: str = "Document",
    theme: str = "dark",
    standalone: bool = True,
    custom_css: str = "",
) -> str:
    """Convert Markdown text to styled, responsive HTML."""
    body_html = _markdown_to_html_body(md_text)
    if not standalone:
        return body_html

    t = _HTML_THEMES.get(theme.lower(), _HTML_THEMES["dark"])
    css = f"""
        :root {{
            --bg: {t['bg']};
            --card-bg: {t['card_bg']};
            --text: {t['text']};
            --muted: {t['muted']};
            --accent: {t['accent']};
            --code-bg: {t['code_bg']};
            --border: {t['border']};
            --table-stripe: {t['table_stripe']};
            --table-header: {t['table_header']};
            --blockquote-border: {t['blockquote_border']};
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg);
            color: var(--text);
            line-height: 1.65;
            padding: 2.5rem 1.5rem;
            max-width: 900px;
            margin: 0 auto;
        }}
        h1, h2, h3, h4, h5, h6 {{
            color: var(--text);
            margin-top: 1.8rem;
            margin-bottom: 0.8rem;
            font-weight: 700;
            line-height: 1.25;
        }}
        h1 {{ font-size: 2.25rem; border-bottom: 2px solid var(--border); padding-bottom: 0.5rem; }}
        h2 {{ font-size: 1.75rem; border-bottom: 1px solid var(--border); padding-bottom: 0.4rem; }}
        h3 {{ font-size: 1.35rem; }}
        p, ul, ol, blockquote, .table-container, pre {{
            margin-bottom: 1.2rem;
        }}
        a {{ color: var(--accent); text-decoration: none; transition: opacity 0.2s; }}
        a:hover {{ text-decoration: underline; }}
        code {{
            font-family: "JetBrains Mono", Consolas, "Courier New", monospace;
            background: var(--code-bg);
            padding: 0.2em 0.4em;
            border-radius: 4px;
            font-size: 0.88em;
            border: 1px solid var(--border);
        }}
        pre {{
            background: var(--code-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1rem;
            overflow-x: auto;
        }}
        pre code {{
            background: transparent;
            padding: 0;
            border: none;
            font-size: 0.9em;
        }}
        blockquote {{
            border-left: 4px solid var(--blockquote-border);
            padding: 0.6rem 1.2rem;
            background: var(--card-bg);
            border-radius: 0 8px 8px 0;
            color: var(--muted);
            font-style: italic;
        }}
        ul, ol {{ padding-left: 2rem; }}
        li {{ margin-bottom: 0.35rem; }}
        .table-container {{
            overflow-x: auto;
            border: 1px solid var(--border);
            border-radius: 8px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }}
        th, td {{
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--border);
        }}
        th {{
            background: var(--table-header);
            font-weight: 600;
        }}
        tr:nth-child(even) td {{
            background: var(--table-stripe);
        }}
        hr {{
            border: 0;
            height: 1px;
            background: var(--border);
            margin: 2rem 0;
        }}
        {custom_css}
    """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>{css}</style>
</head>
<body>
{body_html}
</body>
</html>"""


# --------------------------------------------------------------------------- #
# CSV / JSON / YAML / Excel Conversion Helpers
# --------------------------------------------------------------------------- #

def csv_to_json(csv_text: str, delimiter: str = ",", auto_types: bool = True) -> str:
    """Convert CSV string to formatted JSON array of objects."""
    f = io.StringIO(csv_text.strip())
    reader = csv.DictReader(f, delimiter=delimiter)
    records: List[Dict[str, Any]] = []

    def _cast(v: str) -> Any:
        if not auto_types or v is None:
            return v
        s = v.strip()
        if s.lower() == "true":
            return True
        if s.lower() == "false":
            return False
        if s.lower() in ("null", "none", ""):
            return None if s.lower() != "" else ""
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return v

    for row in reader:
        rec = {k: _cast(v) for k, v in row.items() if k is not None}
        records.append(rec)

    return json.dumps(records, indent=2)


def json_to_csv(json_text: str, delimiter: str = ",") -> str:
    """Convert JSON array of objects or dict to CSV formatted text."""
    data = json.loads(json_text)
    if isinstance(data, dict):
        data = [data]
    elif not isinstance(data, list):
        raise ValueError("JSON data must be an array of objects or a single object dictionary")

    if not data:
        return ""

    headers: List[str] = []
    for item in data:
        if isinstance(item, dict):
            for k in item.keys():
                if k not in headers:
                    headers.append(k)

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=headers, delimiter=delimiter, lineterminator="\n")
    writer.writeheader()
    for item in data:
        if isinstance(item, dict):
            writer.writerow({k: item.get(k, "") for k in headers})
        else:
            writer.writerow({headers[0]: str(item)} if headers else {})

    return out.getvalue()


def json_to_yaml_simple(data: Any, indent: int = 0) -> str:
    """Serialize Python object to YAML string without PyYAML dependency."""
    ind = "  " * indent
    if isinstance(data, dict):
        lines = []
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{ind}{k}:")
                lines.append(json_to_yaml_simple(v, indent + 1))
            else:
                val_str = json.dumps(v) if isinstance(v, str) and (":" in v or "\n" in v or not v) else str(v)
                lines.append(f"{ind}{k}: {val_str}")
        return "\n".join(lines)
    elif isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{ind}-")
                lines.append(json_to_yaml_simple(item, indent + 1))
            else:
                lines.append(f"{ind}- {item}")
        return "\n".join(lines)
    else:
        return f"{ind}{data}"


def csv_to_xlsx_file(csv_text: str, output_path: Path, delimiter: str = ",") -> None:
    """Write CSV data to valid .xlsx spreadsheet without external libraries."""
    import xml.etree.ElementTree as ET
    import zipfile

    f = io.StringIO(csv_text.strip())
    reader = csv.reader(f, delimiter=delimiter)
    rows = list(reader)

    # Build Worksheet XML
    sheet_data = []
    shared_strings: List[str] = []
    ss_map: Dict[str, int] = {}

    def _get_ss_idx(val: str) -> int:
        if val not in ss_map:
            idx = len(shared_strings)
            ss_map[val] = idx
            shared_strings.append(val)
            return idx
        return ss_map[val]

    for r_idx, row in enumerate(rows, 1):
        c_parts = []
        for c_idx, cell in enumerate(row, 1):
            col_letter = chr(64 + c_idx) if c_idx <= 26 else f"A{chr(64 + c_idx - 26)}"
            ref = f"{col_letter}{r_idx}"
            s_idx = _get_ss_idx(cell)
            c_parts.append(f'<c r="{ref}" t="s"><v>{s_idx}</v></c>')
        sheet_data.append(f'<row r="{r_idx}">{"".join(c_parts)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
        f'<sheetData>{"".join(sheet_data)}</sheetData>\n'
        '</worksheet>'
    )

    sst_items = "".join(f"<si><t>{s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}</t></si>" for s in shared_strings)
    sst_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">\n'
        f'{sst_items}\n'
        '</sst>'
    )

    wb_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">\n'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>\n'
        '</workbook>'
    )

    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>\n'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>\n'
        '</Relationships>'
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>\n'
        '</Relationships>'
    )

    types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
        '<Default Extension="xml" ContentType="application/xml"/>\n'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\n'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>\n'
        '</Types>'
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types_xml)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", wb_xml)
        z.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr("xl/sharedStrings.xml", sst_xml)


# --------------------------------------------------------------------------- #
# Image Transformation Engine
# --------------------------------------------------------------------------- #

def convert_image(
    source_path: Path,
    target_path: Path,
    format_name: str = "",
    resize_width: Optional[int] = None,
    resize_height: Optional[int] = None,
    grayscale: bool = False,
    quality: int = 85,
) -> str:
    """Transform image formats, dimensions, and compression."""
    from PIL import Image

    with Image.open(source_path) as img:
        # Convert mode for target format compatibility
        fmt = (format_name or target_path.suffix.lstrip(".").lower()).upper()
        if fmt == "JPG":
            fmt = "JPEG"

        if grayscale:
            img = img.convert("L")
        elif fmt in ("JPEG", "BMP") and img.mode in ("RGBA", "P", "LA"):
            # JPEG/BMP don't support alpha channel; create white background
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                bg.paste(img, mask=img.split()[3])
            else:
                bg.paste(img.convert("RGB"))
            img = bg

        # Resize if requested
        if resize_width or resize_height:
            w, h = img.size
            if resize_width and resize_height:
                new_w, new_h = resize_width, resize_height
            elif resize_width:
                new_w = resize_width
                new_h = round(h * (resize_width / w))
            else:
                new_h = resize_height  # type: ignore
                new_w = round(w * (new_h / h))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        save_kw: Dict[str, Any] = {}
        if fmt in ("JPEG", "WEBP"):
            save_kw["quality"] = max(1, min(100, quality))
            save_kw["optimize"] = True

        img.save(target_path, format=fmt if fmt else None, **save_kw)

    return f"converted image {source_path.name} -> {target_path} ({img.size[0]}x{img.size[1]} {fmt})"


# --------------------------------------------------------------------------- #
# Main Universal File Conversion Dispatcher
# --------------------------------------------------------------------------- #

def convert_file(
    source: str,
    target: str = "",
    target_format: str = "",
    options: Optional[Dict[str, Any]] = None,
    allow: tuple[str, ...] = (),
) -> str:
    """Universal document, data, image, and encoding converter.

    Parameters:
      - source: Path to source file
      - target: Path to output file (if omitted, derives from target_format or returns text)
      - target_format: Format name (e.g. 'html', 'md', 'json', 'csv', 'yaml', 'xlsx', 'png', 'webp', 'base64', 'utf8')
      - options: Optional dict with formatting/theme/resize parameters
      - allow: Allowed write locations sandbox
    """
    s_path = _expand(source)
    if not s_path.exists():
        return f"source file not found: {s_path}"
    if not s_path.is_file():
        return f"source is not a file: {s_path}"

    opts = options or {}
    src_ext = s_path.suffix.lower().lstrip(".")
    tgt_fmt = (target_format or "").strip().lower()

    target_str = (target or "").strip()
    if target_str:
        t_path = _expand(target_str)
        if not _within(t_path, allow):
            return (f"refused: target {t_path} is outside allowed write locations "
                    f"(home dir + configured allow_paths)")
        if not tgt_fmt:
            tgt_fmt = t_path.suffix.lower().lstrip(".")
    else:
        if not tgt_fmt:
            return "convert_file needs either a 'target' filename or 'target_format' specified"
        t_path = s_path.parent / f"{s_path.stem}.{tgt_fmt}"
        if not _within(t_path, allow):
            return f"refused: target {t_path} is outside allowed write locations"

    try:
        # 1. Base64 encode/decode
        if tgt_fmt in ("base64", "b64"):
            data_bytes = s_path.read_bytes()
            encoded_str = base64.b64encode(data_bytes).decode("ascii")
            if opts.get("data_uri"):
                mime = opts.get("mime", f"image/{src_ext}" if src_ext in ("png", "jpg", "jpeg", "webp") else "application/octet-stream")
                encoded_str = f"data:{mime};base64,{encoded_str}"
            t_path.write_text(encoded_str, encoding="utf-8")
            return f"encoded {s_path.name} to Base64 -> {t_path} ({len(encoded_str)} chars)"

        if src_ext in ("base64", "b64") or opts.get("decode_base64"):
            b64_content = s_path.read_text(encoding="utf-8").strip()
            if "," in b64_content and b64_content.startswith("data:"):
                b64_content = b64_content.split(",", 1)[1]
            decoded_bytes = base64.b64decode(b64_content)
            t_path.write_bytes(decoded_bytes)
            return f"decoded Base64 {s_path.name} -> {t_path} ({len(decoded_bytes)} bytes)"

        # 2. Markdown -> HTML
        if (src_ext in ("md", "markdown", "txt") and tgt_fmt in ("html", "htm")) or opts.get("markdown"):
            md_content = s_path.read_text(encoding="utf-8", errors="replace")
            html_out = markdown_to_html(
                md_content,
                title=opts.get("title", s_path.stem),
                theme=opts.get("theme", "dark"),
                standalone=opts.get("standalone", True),
                custom_css=opts.get("css", ""),
            )
            t_path.write_text(html_out, encoding="utf-8")
            return f"converted Markdown {s_path.name} -> styled HTML {t_path} (theme: {opts.get('theme', 'dark')})"

        # 3. CSV <-> JSON
        if src_ext == "csv" and tgt_fmt in ("json", "js"):
            csv_content = s_path.read_text(encoding="utf-8", errors="replace")
            json_out = csv_to_json(csv_content, delimiter=opts.get("delimiter", ","), auto_types=opts.get("auto_types", True))
            t_path.write_text(json_out, encoding="utf-8")
            return f"converted CSV {s_path.name} -> JSON array {t_path}"

        if src_ext in ("json", "js") and tgt_fmt == "csv":
            json_content = s_path.read_text(encoding="utf-8", errors="replace")
            csv_out = json_to_csv(json_content, delimiter=opts.get("delimiter", ","))
            t_path.write_text(csv_out, encoding="utf-8")
            return f"converted JSON {s_path.name} -> CSV {t_path}"

        # 4. JSON <-> YAML
        if src_ext in ("json", "js") and tgt_fmt in ("yaml", "yml"):
            json_data = json.loads(s_path.read_text(encoding="utf-8", errors="replace"))
            try:
                import yaml  # type: ignore
                yaml_out = yaml.dump(json_data, sort_keys=False, allow_unicode=True)
            except Exception:
                yaml_out = json_to_yaml_simple(json_data)
            t_path.write_text(yaml_out, encoding="utf-8")
            return f"converted JSON {s_path.name} -> YAML {t_path}"

        # 5. CSV <-> XLSX
        if src_ext == "csv" and tgt_fmt in ("xlsx", "excel"):
            csv_content = s_path.read_text(encoding="utf-8", errors="replace")
            csv_to_xlsx_file(csv_content, t_path, delimiter=opts.get("delimiter", ","))
            return f"converted CSV {s_path.name} -> Excel Workbook {t_path}"

        if src_ext in ("xlsx", "xlsm") and tgt_fmt == "csv":
            from .documents import _read_xlsx
            sheet_text = _read_xlsx(s_path)
            lines = [l for l in sheet_text.splitlines() if not l.startswith("--- sheet:")]
            csv_lines = [l.replace("\t", ",") for l in lines if l.strip()]
            t_path.write_text("\n".join(csv_lines), encoding="utf-8")
            return f"extracted Excel {s_path.name} -> CSV {t_path}"

        # 6. Image Conversions
        _IMG_FORMATS = {"png", "jpg", "jpeg", "webp", "bmp", "gif", "ico", "tiff"}
        if src_ext in _IMG_FORMATS or tgt_fmt in _IMG_FORMATS:
            return convert_image(
                source_path=s_path,
                target_path=t_path,
                format_name=tgt_fmt,
                resize_width=opts.get("resize_width") or opts.get("width"),
                resize_height=opts.get("resize_height") or opts.get("height"),
                grayscale=bool(opts.get("grayscale", False)),
                quality=int(opts.get("quality", 85)),
            )

        # 7. Text Encoding Normalization / Line endings
        if tgt_fmt in ("utf8", "utf-8", "crlf", "lf"):
            text_data = s_path.read_text(encoding="utf-8", errors="replace")
            if tgt_fmt == "crlf":
                text_data = text_data.replace("\r\n", "\n").replace("\n", "\r\n")
            elif tgt_fmt == "lf":
                text_data = text_data.replace("\r\n", "\n")
            with t_path.open("w", encoding="utf-8", newline="") as f:
                f.write(text_data)
            return f"normalized encoding/newlines {s_path.name} -> {t_path}"

    except Exception as exc:
        return f"could not convert {s_path.name} to {tgt_fmt}: {exc}"

    return f"unsupported conversion format: from '{src_ext}' to '{tgt_fmt}'"
