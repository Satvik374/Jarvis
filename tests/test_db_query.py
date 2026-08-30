"""Unit and integration tests for Database & SQL Query Intelligence Engine."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import db_query, registry
from jarvis.tools.schema import ACTIONS_BY_NAME


@pytest.fixture
def temp_database():
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "ecommerce.db"
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Create schema
        cursor.execute("""
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
        """)

        cursor.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            category_id INTEGER,
            stock INTEGER DEFAULT 0,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )
        """)

        cursor.execute("CREATE INDEX idx_products_category ON products(category_id)")

        # Seed data
        cursor.execute("INSERT INTO categories (name) VALUES ('Electronics'), ('Home'), ('Books')")
        cursor.execute("""
        INSERT INTO products (name, price, category_id, stock) VALUES
        ('Laptop Ultra', 1299.99, 1, 15),
        ('Wireless Mouse', 29.50, 1, 120),
        ('Coffee Maker', 89.00, 2, 40),
        ('Python Handbook', 45.00, 3, 200)
        """)

        conn.commit()
        conn.close()

        yield db_path


def test_schema_and_registry_registration():
    """Verify db_query is in schema and registered in handlers."""
    assert "db_query" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["db_query"]
    assert action.category == "coding"
    assert any(p.name == "sql" for p in action.params)
    assert any(p.name == "op" for p in action.params)
    assert any(p.name == "output_format" for p in action.params)


def test_schema_introspection(temp_database):
    """Test full schema introspection of tables, foreign keys, and indexes."""
    res = db_query.db_query(path=str(temp_database), op="schema")

    assert "Database Schema" in res
    assert "TABLE: categories" in res
    assert "TABLE: products" in res
    assert "🔑 [PK]" in res
    assert "FK (category_id) -> categories(id)" in res
    assert "Index: idx_products_category" in res


def test_list_tables(temp_database):
    """Test listing all tables with row counts."""
    res = db_query.db_query(path=str(temp_database), op="tables")

    assert "categories" in res
    assert "products" in res
    assert "4" in res  # 4 products


def test_sql_query_table_format(temp_database):
    """Test querying and getting ASCII formatted table."""
    res = db_query.db_query(
        path=str(temp_database),
        sql="SELECT name, price, stock FROM products WHERE price > 50 ORDER BY price DESC",
        output_format="table",
    )

    assert "Laptop Ultra" in res
    assert "Coffee Maker" in res
    assert "Wireless Mouse" not in res


def test_sql_query_json_format(temp_database):
    """Test querying with structured JSON output."""
    res = db_query.db_query(
        path=str(temp_database),
        sql="SELECT name, price FROM products WHERE stock > 100",
        output_format="json",
    )
    data = json.loads(res)

    assert data["count"] == 2
    assert "columns" in data
    assert any(r["name"] == "Wireless Mouse" for r in data["records"])
    assert any(r["name"] == "Python Handbook" for r in data["records"])


def test_sql_query_csv_format(temp_database):
    """Test querying with CSV output format."""
    res = db_query.db_query(
        path=str(temp_database),
        sql="SELECT id, name FROM categories ORDER BY id",
        output_format="csv",
    )

    assert "id,name" in res
    assert "1,Electronics" in res
    assert "2,Home" in res


def test_explain_query_plan(temp_database):
    """Test running EXPLAIN QUERY PLAN."""
    res = db_query.db_query(
        path=str(temp_database),
        sql="SELECT * FROM products WHERE category_id = 1",
        op="explain",
    )

    assert "Query Plan for" in res
    assert "idx_products_category" in res or "products" in res


def test_parameterized_query(temp_database):
    """Test executing query with safe parameterized substitution."""
    res = db_query.db_query(
        path=str(temp_database),
        sql="SELECT name FROM products WHERE price < ?",
        params=[50.0],
        output_format="json",
    )
    data = json.loads(res)

    assert data["count"] == 2
    names = [r["name"] for r in data["records"]]
    assert "Wireless Mouse" in names
    assert "Python Handbook" in names


def test_registry_execution(temp_database):
    """Test executing db_query through registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Database",
    )

    res = registry.execute(
        name="db_query",
        args={"path": str(temp_database), "sql": "SELECT COUNT(*) as total FROM products", "output_format": "json"},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert '"total": 4' in res.message or '"total":4' in res.message or "4" in res.message
    assert res.needs_observe is False
