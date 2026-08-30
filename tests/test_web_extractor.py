"""Unit and integration tests for Structured Web Data and Table Extraction Engine."""

from __future__ import annotations

import json
import pytest

from jarvis.config import load_config
from jarvis.perception.elements import Observation
from jarvis.tools import registry, web_extractor
from jarvis.tools.schema import ACTIONS_BY_NAME


_SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Global Market Intelligence 2026</title>
    <meta name="description" content="Comprehensive analysis of global tech sectors and GDP growth.">
    <meta property="og:title" content="Global Market Report 2026">
    <meta property="og:description" content="AI, Semiconductors, and Cloud Computing Growth Index">
    <meta name="twitter:card" content="summary_large_image">
    <link rel="canonical" href="https://example.com/market-report-2026">
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "Report",
        "headline": "Global Market Intelligence 2026",
        "author": "Jarvis Research Labs"
    }
    </script>
</head>
<body>
    <header>
        <nav>
            <a href="/">Home</a>
            <a href="/about">About Us</a>
        </nav>
    </header>

    <article>
        <h1>Global Tech Growth Analysis</h1>
        <p>The technology sector experienced accelerated momentum driven by autonomous AI architectures.</p>

        <h2>Key Market Indicators</h2>
        <table id="market-table">
            <thead>
                <tr>
                    <th>Sector</th>
                    <th>MarketCap (Billion USD)</th>
                    <th>GrowthRate</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>Artificial Intelligence</td>
                    <td>1450</td>
                    <td>+34.2%</td>
                </tr>
                <tr>
                    <td>Cloud Platforms</td>
                    <td>890</td>
                    <td>+18.5%</td>
                </tr>
                <tr>
                    <td>Quantum Computing</td>
                    <td>120</td>
                    <td>+45.0%</td>
                </tr>
            </tbody>
        </table>

        <h2>Resources & Downloads</h2>
        <ul>
            <li><a href="/downloads/q4_financial_report.pdf">Download Financial PDF</a></li>
            <li><a href="/downloads/raw_data.csv">Export Raw CSV Dataset</a></li>
            <li><a href="https://partner.org/research">External Partner Portal</a></li>
        </ul>
    </article>

    <footer>
        <p>Copyright 2026 Jarvis Inc.</p>
    </footer>
</body>
</html>
"""


def test_schema_and_registry_registration():
    """Verify extract_web_data is in schema and registered in handlers."""
    assert "extract_web_data" in ACTIONS_BY_NAME
    action = ACTIONS_BY_NAME["extract_web_data"]
    assert action.category == "system"
    assert any(p.name == "url" for p in action.params)
    assert any(p.name == "mode" for p in action.params)
    assert any(p.name == "output_format" for p in action.params)


def test_table_extraction_json():
    """Test extracting HTML table to structured JSON."""
    res = web_extractor.extract_web_data(html_content=_SAMPLE_HTML, mode="tables", output_format="json")
    data = json.loads(res)

    assert data["table_id"] == 1
    assert "Sector" in data["headers"]
    assert len(data["rows"]) == 3
    assert data["rows"][0]["Sector"] == "Artificial Intelligence"
    assert data["rows"][0]["MarketCap (Billion USD)"] == "1450"


def test_table_extraction_csv():
    """Test extracting HTML table to CSV string."""
    res = web_extractor.extract_web_data(html_content=_SAMPLE_HTML, mode="tables", output_format="csv")

    assert "Sector,MarketCap (Billion USD),GrowthRate" in res
    assert "Artificial Intelligence,1450,+34.2%" in res
    assert "Cloud Platforms,890,+18.5%" in res


def test_metadata_extraction():
    """Test extracting OpenGraph, Twitter, and JSON-LD metadata."""
    res = web_extractor.extract_web_data(html_content=_SAMPLE_HTML, mode="metadata")
    data = json.loads(res)

    assert data["title"] == "Global Market Intelligence 2026"
    assert data["canonical"] == "https://example.com/market-report-2026"
    assert data["open_graph"]["title"] == "Global Market Report 2026"
    assert data["twitter_card"]["card"] == "summary_large_image"
    assert len(data["json_ld"]) == 1
    assert data["json_ld"][0]["author"] == "Jarvis Research Labs"


def test_link_categorization():
    """Test extracting and classifying internal, external, and download links."""
    res = web_extractor.extract_web_data(
        url="https://example.com/report",
        html_content=_SAMPLE_HTML,
        mode="links",
    )
    data = json.loads(res)

    assert data["total_links"] >= 5
    dl_urls = [d["url"] for d in data["downloads"]]
    assert any("q4_financial_report.pdf" in u for u in dl_urls)
    assert any("raw_data.csv" in u for u in dl_urls)

    ext_urls = [e["url"] for e in data["external_links"]]
    assert any("partner.org" in u for u in ext_urls)


def test_article_readability_extraction():
    """Test extracting clean article text while stripping navbars and footers."""
    res = web_extractor.extract_web_data(html_content=_SAMPLE_HTML, mode="article")

    assert "# Global Tech Growth Analysis" in res
    assert "The technology sector experienced accelerated momentum" in res
    assert "Home" not in res  # Nav element stripped
    assert "Copyright 2026" not in res  # Footer element stripped


def test_registry_execution():
    """Test executing extract_web_data through registry dispatcher."""
    cfg = load_config()
    obs = Observation(
        screenshot_path=None,
        elements=[],
        screen_size=(1920, 1080),
        active_window="Browser",
    )

    res = registry.execute(
        name="extract_web_data",
        args={"html_content": _SAMPLE_HTML, "mode": "tables", "output_format": "csv"},
        obs=obs,
        cfg=cfg,
    )
    assert res.ok is True
    assert "Artificial Intelligence" in res.message
    assert res.needs_observe is False
