"""Database and SQL Query Intelligence Engine for Jarvis.

Enables native schema introspection, table inspection, query plan analysis,
and SQL execution (SQLite, and relational databases) with multiple output
formats (table, JSON, CSV) without external dependencies.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .files import _expand, _within


def _format_ascii_table(headers: List[str], rows: List[List[Any]]) -> str:
    """Format headers and rows into clean ASCII tabular view."""
    if not headers and not rows:
        return "(empty result set)"

    str_rows = [[str(val if val is not None else "NULL") for val in r] for r in rows]
    cols = len(headers) if headers else (len(rows[0]) if rows else 0)

    widths = [len(h) for h in headers] if headers else [0] * cols
    for r in str_rows:
        for idx, val in enumerate(r):
            if idx < len(widths):
                widths[idx] = max(widths[idx], len(val))
            else:
                widths.append(len(val))

    # Build border & header
    sep = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    lines = [sep]
    if headers:
        hdr_line = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
        lines.append(hdr_line)
        lines.append(sep)

    for r in str_rows:
        row_line = "| " + " | ".join(r[i].ljust(widths[i]) if i < len(r) else "".ljust(widths[i]) for i in range(len(widths))) + " |"
        lines.append(row_line)

    lines.append(sep)
    return "\n".join(lines)


def get_db_schema(conn: sqlite3.Connection) -> str:
    """Inspect and format database tables, columns, constraints, and indexes."""
    cursor = conn.cursor()
    cursor.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name")
    tables = cursor.fetchall()

    if not tables:
        return "database has no user tables"

    schema_lines = [f"Database Schema ({len(tables)} tables/views):\n"]
    for tbl_name, tbl_type in tables:
        cursor.execute(f"PRAGMA table_info('{tbl_name}')")
        cols = cursor.fetchall()  # (cid, name, type, notnull, dflt_value, pk)

        cursor.execute(f"PRAGMA foreign_key_list('{tbl_name}')")
        fks = cursor.fetchall()  # (id, seq, table, from, to, on_update, on_delete, match)

        cursor.execute(f"SELECT COUNT(*) FROM '{tbl_name}'")
        row_cnt = cursor.fetchone()[0]

        schema_lines.append(f"📊 {tbl_type.upper()}: {tbl_name} ({row_cnt} rows)")
        for c in cols:
            cid, cname, ctype, notnull, dflt, pk = c
            pk_str = " 🔑 [PK]" if pk else ""
            nn_str = " NOT NULL" if notnull else ""
            df_str = f" DEFAULT {dflt}" if dflt is not None else ""
            schema_lines.append(f"   • {cname:<20} {ctype:<12}{pk_str}{nn_str}{df_str}")

        if fks:
            for fk in fks:
                schema_lines.append(f"     ↪ FK ({fk[3]}) -> {fk[2]}({fk[4]})")

        # Indexes
        cursor.execute(f"PRAGMA index_list('{tbl_name}')")
        idx_list = cursor.fetchall()
        if idx_list:
            for idx in idx_list:
                schema_lines.append(f"     ⚡ Index: {idx[1]} ({'UNIQUE' if idx[2] else 'INDEX'})")

        schema_lines.append("")

    return "\n".join(schema_lines)


def list_db_tables(conn: sqlite3.Connection) -> str:
    """Enumerate all tables and view row counts."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]

    if not tables:
        return "no tables found in database"

    rows = []
    for t in tables:
        cursor.execute(f"SELECT COUNT(*) FROM '{t}'")
        cnt = cursor.fetchone()[0]
        rows.append([t, cnt])

    return f"Tables in Database ({len(tables)}):\n" + _format_ascii_table(["Table Name", "Row Count"], rows)


def explain_query_plan(conn: sqlite3.Connection, sql: str) -> str:
    """Run EXPLAIN QUERY PLAN on SQL query."""
    cursor = conn.cursor()
    cursor.execute(f"EXPLAIN QUERY PLAN {sql}")
    headers = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    return f"Query Plan for: {sql[:80]}...\n" + _format_ascii_table(headers, rows)


def execute_sql(
    conn: sqlite3.Connection,
    sql: str,
    params: Optional[List[Any]] = None,
    limit: int = 100,
    output_format: str = "table",
) -> str:
    """Execute SQL query or statement and return formatted results."""
    cursor = conn.cursor()
    sql_clean = sql.strip()
    p = params or []

    cursor.execute(sql_clean, p)

    if cursor.description is not None:
        # SELECT / Read query
        headers = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(max(1, limit))
        total_fetched = len(rows)

        fmt = output_format.lower().strip()
        if fmt == "json":
            records = [dict(zip(headers, r)) for r in rows]
            return json.dumps({"count": total_fetched, "columns": headers, "records": records}, indent=2, default=str)
        elif fmt == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(headers)
            w.writerows(rows)
            return buf.getvalue().strip()
        else:
            tbl_str = _format_ascii_table(headers, rows)
            limit_note = f" (showing first {total_fetched} rows)" if total_fetched >= limit else f" ({total_fetched} rows)"
            return tbl_str + limit_note

    else:
        # DML / Write query (INSERT, UPDATE, DELETE, CREATE, etc.)
        conn.commit()
        affected = cursor.rowcount
        return f"statement executed successfully ({affected} row(s) affected)"


def db_query(
    path: str = "",
    sql: str = "",
    op: str = "query",
    params: Optional[List[Any]] = None,
    limit: int = 100,
    output_format: str = "table",
    allow: tuple[str, ...] = (),
) -> str:
    """Inspect database schema or execute SQL queries on SQLite databases.

    Operations:
      - 'query': Execute SQL query/statement and format result (table, json, csv).
      - 'schema': Introspect complete database schema (tables, columns, types, foreign keys, indexes).
      - 'tables': List all tables with row counts.
      - 'explain': Explain query execution plan.
    """
    db_target = path.strip()
    if db_target.startswith("sqlite:///"):
        db_target = db_target.replace("sqlite:///", "")

    if not db_target:
        from ..memory.manager import get_default_db_path
        p = get_default_db_path()
    else:
        p = _expand(db_target)

    if not p.exists() and not str(p).endswith(":memory:"):
        return f"database file not found: {p}"

    op_clean = (op or "query").strip().lower()

    try:
        conn = sqlite3.connect(str(p), timeout=15.0)
        try:
            if op_clean in ("schema", "ddl", "introspect"):
                return get_db_schema(conn)
            elif op_clean in ("tables", "list_tables", "list"):
                return list_db_tables(conn)
            elif op_clean in ("explain", "plan"):
                if not sql.strip():
                    return "explain op requires a 'sql' query"
                return explain_query_plan(conn, sql)
            elif op_clean in ("query", "sql", "exec", "select"):
                if not sql.strip():
                    return "query op requires a 'sql' statement"
                return execute_sql(conn, sql=sql, params=params, limit=limit, output_format=output_format)
            else:
                return f"unknown db_query op '{op}' - supported: query, schema, tables, explain"
        finally:
            conn.close()
    except Exception as exc:
        return f"database query error: {exc}"
