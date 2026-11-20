import unittest
from datetime import date
from official_article_dates import explicit_release_date

class OfficialArticleDateTests(unittest.TestCase):
    def test_explicit_full_year(self):
        article = "<html><body><h1>DRIDAM</h1><p>DRIDAM streaming on JioHotstar from November 25, 2026.</p></body></html>"
        self.assertEqual(explicit_release_date(article, "DRIDAM", date(2026, 11, 20)), "2026-11-25")
    def test_no_year_is_not_verified(self):
        self.assertIsNone(explicit_release_date("<p>DRIDAM streaming on JioHotstar from November 25.</p>", "DRIDAM", date(2026, 11, 20)))
    def test_past_release_is_not_auto_published(self):
        self.assertIsNone(explicit_release_date("<p>DRIDAM streaming on JioHotstar from November 6, 2026.</p>", "DRIDAM", date(2026, 11, 20)))
    def test_conflicting_dates_are_not_verified(self):
        text = "<p>DRIDAM streaming on JioHotstar from November 25, 2026. DRIDAM streaming on JioHotstar from November 26, 2026.</p>"
        self.assertIsNone(explicit_release_date(text, "DRIDAM", date(2026, 11, 20)))
    def test_script_is_ignored(self):
        text = "<h1>DRIDAM</h1><script>DRIDAM streaming on JioHotstar from November 25, 2026.</script>"
        self.assertIsNone(explicit_release_date(text, "DRIDAM", date(2026, 11, 20)))

if __name__ == "__main__":
    unittest.main()
