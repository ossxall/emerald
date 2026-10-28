import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from tools.format_html import format_html


class FormatHtmlTests(unittest.TestCase):
    def test_promotes_escaped_page_separators_and_groups_content(self):
        source = """<!doctype html>
        <html><body>
        &lt;div data-type='page' data-number='1' id='page-1' class='page-virtual'&gt;&lt;/div&gt;
        <table><tr><td><h3>First page</h3></td></tr></table>
        &lt;div data-type='page' data-number='2' id='page-2' class='page-virtual'&gt;&lt;/div&gt;
        <p>Second page</p>
        </body></html>"""

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.html"
            output_path = Path(temp_dir) / "formatted.html"
            source_path.write_text(source, encoding="utf-8")

            format_html(source_path, output_path)

            formatted = output_path.read_text(encoding="utf-8")

        soup = BeautifulSoup(formatted, "html.parser")
        pages = soup.select('div[data-type="page"]')

        self.assertEqual([page.get("data-number") for page in pages], ["1", "2"])
        self.assertIsNotNone(pages[0].select_one("table"))
        self.assertIn("First page", pages[0].get_text(" ", strip=True))
        self.assertIn("Second page", pages[1].get_text(" ", strip=True))
        self.assertNotIn("&lt;div data-type=", formatted)


if __name__ == "__main__":
    unittest.main()
