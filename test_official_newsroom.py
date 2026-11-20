import unittest
from official_newsroom import candidates_from_html

class NewsroomTests(unittest.TestCase):
    def test_valid_headline(self):
        html = '<a href="/newsroom/example/">Example Film to Stream on JioHotstar from December 14</a>'
        rows = candidates_from_html(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform"], "JioHotstar")
        self.assertNotIn("release_date", rows[0])
        self.assertTrue(rows[0]["source_url"].startswith("https://www.jiostar.com/"))
    def test_unrelated_news_excluded(self):
        self.assertEqual(candidates_from_html('<a href="/news/">JioStar appoints executive</a>'), [])
    def test_external_link_excluded(self):
        self.assertEqual(candidates_from_html('<a href="https://evil.example/film">Film to stream on JioHotstar</a>'), [])
    def test_duplicate_excluded(self):
        link = '<a href="/news/film">Film to stream on JioHotstar</a>'
        self.assertEqual(len(candidates_from_html(link + link)), 1)

if __name__ == "__main__":
    unittest.main()
