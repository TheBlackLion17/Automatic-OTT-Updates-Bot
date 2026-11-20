"""Conservative official announcement page checks; fail closed."""
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlsplit
from official_discovery import host_allowed

class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self.hidden:
            self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

def date_variants(value):
    day = date.fromisoformat(value)
    return {
        day.isoformat().casefold(),
        day.strftime("%B %d, %Y").casefold(),
        day.strftime("%B %-d, %Y").casefold(),
        day.strftime("%d %B %Y").casefold(),
        day.strftime("%-d %B %Y").casefold(),
    }

def text_supports_release(text, item):
    # Normalize whitespace and punctuation; require all significant title words
    # and an explicit full calendar date in the same official page.
    normalized = re.sub(r"\s+", " ", text).casefold()
    title = re.findall(r"[\w]+", item["title"].casefold())
    if not title or not all(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", normalized) for w in title):
        return False
    return any(re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", normalized) for v in date_variants(item["release_date"]))

async def verify_official_page(session, item, official_domains):
    url = item["source_url"]
    if not host_allowed(url, official_domains):
        return False
    try:
        async with session.get(url, allow_redirects=False) as response:
            if response.status != 200:
                return False
            mime = response.headers.get("Content-Type", "").lower().split(";")[0].strip()
            if mime not in ("text/html", "application/xhtml+xml"):
                return False
            if response.content_length and response.content_length > 750000:
                return False
            data = await response.content.read(750001)
            if len(data) > 750000:
                return False
        parser = VisibleText()
        parser.feed(data.decode("utf-8", errors="replace"))
        return text_supports_release(" ".join(parser.parts), item)
    except Exception:
        return False
