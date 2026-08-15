import tempfile
import unittest
from pathlib import Path

from jarvis.config import Config, BrowserConfig
from jarvis.browser_engine.driver import BrowserDriver, DOMElement, BrowserSnapshot
from jarvis.tools import registry
from jarvis.tools.schema import ACTIONS_BY_NAME


_TEST_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Jarvis Test Page</title>
</head>
<body>
    <h1>Welcome to Jarvis Browser Engine</h1>
    <h2>Search and Automate</h2>
    <form id="search-form">
        <label for="search-input">Search Query:</label>
        <input type="text" id="search-input" name="q" placeholder="Enter keywords..." />
        <button type="button" id="submit-btn" onclick="document.getElementById('result').innerText = 'Searched: ' + document.getElementById('search-input').value">Search</button>
    </form>
    <select id="category-select">
        <option value="all">All Categories</option>
        <option value="tech">Technology</option>
        <option value="science">Science</option>
    </select>
    <a href="https://example.com" id="test-link">Example Link</a>
    <div id="result">Initial State</div>
</body>
</html>
"""


class BrowserEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_html = tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode="w+", encoding="utf-8")
        cls.tmp_html.write(_TEST_HTML)
        cls.tmp_html.close()
        cls.file_url = Path(cls.tmp_html.name).as_uri()

        # Initialize headless driver for test suite
        cfg = Config(browser=BrowserConfig(headless=True))
        cls.driver = BrowserDriver(cfg=cfg)

    @classmethod
    def tearDownClass(cls):
        cls.driver.close()
        p = Path(cls.tmp_html.name)
        if p.exists():
            p.unlink()

    def test_01_navigate_and_snapshot(self):
        res = self.driver.navigate(self.file_url, headless=True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["title"], "Jarvis Test Page")
        self.assertIn("Welcome to Jarvis Browser Engine", res["snapshot"])

        # Check snapshot elements
        snap = self.driver.snapshot()
        self.assertGreaterEqual(len(snap.elements), 3)
        tags = [e.tag for e in snap.elements]
        self.assertIn("input", tags)
        self.assertIn("button", tags)
        self.assertIn("a", tags)

    def test_02_type_and_click(self):
        # 1. Type into search input
        type_res = self.driver.type_text("input[name=q]", "Artificial Intelligence")
        self.assertTrue(type_res["ok"])

        # 2. Click submit button
        click_res = self.driver.click("#submit-btn")
        self.assertTrue(click_res["ok"])

        # 3. Verify DOM text updated
        eval_res = self.driver.evaluate("document.getElementById('result').innerText")
        self.assertTrue(eval_res["ok"])
        self.assertEqual(eval_res["result"], "Searched: Artificial Intelligence")

    def test_03_indexed_ref_interaction(self):
        # Fresh snapshot populates indexed map (e.g. e1, e2)
        snap = self.driver.snapshot()
        self.assertTrue(len(snap.elements) > 0)
        first_el = snap.elements[0]

        # Type using indexed tag ref 'e1' or whatever input is
        input_el = next((e for e in snap.elements if e.tag == "input"), None)
        self.assertIsNotNone(input_el)

        type_res = self.driver.type_text(input_el.id, "Indexed Control Test")
        self.assertTrue(type_res["ok"])

        # Click using button indexed ref
        btn_el = next((e for e in snap.elements if e.tag == "button"), None)
        self.assertIsNotNone(btn_el)
        click_res = self.driver.click(btn_el.id)
        self.assertTrue(click_res["ok"])

        eval_res = self.driver.evaluate("document.getElementById('result').innerText")
        self.assertEqual(eval_res["result"], "Searched: Indexed Control Test")

    def test_04_select_and_scroll(self):
        sel_res = self.driver.select_option("#category-select", "science")
        self.assertTrue(sel_res["ok"])

        scroll_res = self.driver.scroll("down", 200)
        self.assertTrue(scroll_res["ok"])

    def test_05_extract_content(self):
        ext_res = self.driver.extract_content(mode="text")
        self.assertTrue(ext_res["ok"])
        self.assertIn("Welcome to Jarvis Browser Engine", ext_res["content"])
        self.assertIn("Search and Automate", ext_res["content"])

    def test_06_screenshot_capture(self):
        shot_path = self.driver.take_screenshot()
        self.assertTrue(bool(shot_path))
        p = Path(shot_path)
        self.assertTrue(p.exists())
        self.assertGreater(p.stat().st_size, 100)

    def test_07_registry_tool_action(self):
        self.assertIn("browser_action", ACTIONS_BY_NAME)

        # Execute navigate via registry
        r1 = registry.execute("browser_action", {"action": "navigate", "url": self.file_url, "headless": True}, None, self.driver.cfg)
        self.assertTrue(r1.ok)
        self.assertIn("Jarvis Test Page", r1.message)
        self.assertIsNotNone(r1.image_path)
        self.assertTrue(Path(r1.image_path).exists())

        # Execute type & click via registry
        r2 = registry.execute("browser_action", {"action": "type", "target": "#search-input", "text": "Registry Test"}, None, self.driver.cfg)
        self.assertTrue(r2.ok)

        r3 = registry.execute("browser_action", {"action": "click", "target": "#submit-btn"}, None, self.driver.cfg)
        self.assertTrue(r3.ok)
        self.assertIsNotNone(r3.image_path)

        # Execute eval via registry
        r4 = registry.execute("browser_action", {"action": "eval", "text": "document.getElementById('result').innerText"}, None, self.driver.cfg)
        self.assertTrue(r4.ok)
        self.assertIn("Registry Test", r4.message)


if __name__ == "__main__":
    unittest.main()
