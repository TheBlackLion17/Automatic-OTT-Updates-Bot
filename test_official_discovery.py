import unittest
from unittest.mock import patch
from official_discovery import validate_release

DOMAINS = {"netflix.com", "www.netflix.com"}
PLATFORMS = {"netflix"}
ITEM = {"title":"Example", "platform":"Netflix", "language":"Malayalam",
        "release_date":"2099-01-01", "source_url":"https://www.netflix.com/title/123"}

class DiscoveryTests(unittest.TestCase):
    def test_official_source(self):
        self.assertIsNotNone(validate_release(ITEM, DOMAINS, PLATFORMS))
    def test_untrusted_source(self):
        self.assertIsNone(validate_release({**ITEM,"source_url":"https://netflix.com.evil.test/title"}, DOMAINS, PLATFORMS))
    def test_missing_date(self):
        self.assertIsNone(validate_release({**ITEM,"release_date":""}, DOMAINS, PLATFORMS))
    def test_unapproved_platform(self):
        self.assertIsNone(validate_release({**ITEM,"platform":"Unknown"}, DOMAINS, PLATFORMS))
    def test_http_not_accepted(self):
        self.assertIsNone(validate_release({**ITEM,"source_url":"http://www.netflix.com/title/123"}, DOMAINS, PLATFORMS))

if __name__ == "__main__":
    unittest.main()
