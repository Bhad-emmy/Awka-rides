"""Static UI regression checks; do not require production credentials."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    "index.html", "driver.html", "dispatcher.html",
    "payment-records.html", "driver-earnings.html",
]
LEGAL = ["privacy.html", "terms.html"]


class UserInterfaceChecks(unittest.TestCase):
    def test_all_pages_have_mobile_viewport(self):
        for filename in PAGES + LEGAL:
            with self.subTest(page=filename):
                text = (ROOT / filename).read_text(encoding="utf-8")
                self.assertIn('name="viewport"', text)
                self.assertIn("/ui-enhancements.css", text)

    def test_legal_links_are_available_on_each_page(self):
        for filename in PAGES + LEGAL:
            with self.subTest(page=filename):
                text = (ROOT / filename).read_text(encoding="utf-8")
                self.assertIn('href="/privacy.html"', text)
                self.assertIn('href="/terms.html"', text)
                self.assertIn('class="oka-legal-footer', text)

    def test_authentication_sign_up_links_to_terms(self):
        text = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="app-signup-form"', text)
        self.assertIn('class="splash-legal"', text)
        self.assertIn('class="legal-inline"', text)

    def test_legal_pages_are_drafts_and_not_connected_to_auth(self):
        for filename in LEGAL:
            with self.subTest(page=filename):
                text = (ROOT / filename).read_text(encoding="utf-8")
                self.assertIn("Draft — business-owner review required.", text)
                self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", text)
                self.assertNotIn('src="app-test.js"', text)

    def test_responsive_motion_and_breakpoints(self):
        css = (ROOT / "ui-enhancements.css").read_text(encoding="utf-8")
        for selector in [
            ".customer-page", ".driver-page", ".dispatcher-page",
            ".payments-page", ".earnings-page", ".legal-page",
            "prefers-reduced-motion:reduce", "max-width:599px",
            "max-width:380px", "orientation:landscape",
            "oka-fade-up", "oka-spin",
        ]:
            with self.subTest(selector=selector):
                self.assertIn(selector, css)


if __name__ == "__main__":
    unittest.main()
