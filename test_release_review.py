import unittest
from release_review import clean_newsroom_title, extract_explicit_languages, publication_issues

class ReviewTests(unittest.TestCase):
    def test_clean_newsroom(self):
        self.assertEqual(clean_newsroom_title("DRIDAM to Stream on JioHotstar from June 12 Details here"), "DRIDAM")
        self.assertEqual(clean_newsroom_title("Mollywood Times – Streaming on JioHotstar from July 3"), "Mollywood Times")
        self.assertEqual(clean_newsroom_title("Hotstar Specials ‘Land of Football’ – Streaming on JioHotstar from July 10"), "Land of Football")

    def test_languages_explicit_only(self):
        self.assertEqual(extract_explicit_languages("Available in Malayalam, Tamil and Hindi"), "Malayalam, Tamil, Hindi")
        self.assertIsNone(extract_explicit_languages("Streaming tomorrow"))

    def test_blocks_incomplete(self):
        self.assertIn("release date", publication_issues({"title": "Film", "platform": "JioHotstar", "language": "Malayalam", "source_url": "https://www.jiostar.com/news/film/"}))

    def test_complete(self):
        self.assertEqual(publication_issues({"title": "Film", "platform": "JioHotstar", "language": "Malayalam", "release_date": "2026-11-20", "source_url": "https://www.jiostar.com/news/film/"}), [])

if __name__ == "__main__":
    unittest.main()
