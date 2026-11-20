import unittest
from official_page_verifier import text_supports_release

ITEM = {"title": "Example Movie", "release_date": "2099-01-07"}

class OfficialPageTests(unittest.TestCase):
    def test_full_date_and_title(self):
        self.assertTrue(text_supports_release("Example Movie premieres January 7, 2099.", ITEM))
    def test_no_date(self):
        self.assertFalse(text_supports_release("Example Movie coming soon.", ITEM))
    def test_wrong_date(self):
        self.assertFalse(text_supports_release("Example Movie premieres January 8, 2099.", ITEM))
    def test_wrong_title(self):
        self.assertFalse(text_supports_release("Another Movie premieres January 7, 2099.", ITEM))

if __name__ == "__main__":
    unittest.main()
