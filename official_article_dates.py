"""Extract only explicit, full-year JioHotstar streaming dates from official article text."""
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urlsplit

MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
PATTERNS = (
    re.compile(r"\b(?:stream(?:ing)?|premier(?:e|es|ing))\s+on\s+JioHotstar\s+(?:from|on)\s+("+MONTH+r"\s+\d{1,2},?\s+20\d{2})\b", re.I),
    re.compile(r"\bJioHotstar\s+(?:from|on)\s+("+MONTH+r"\s+\d{1,2},?\s+20\d{2})\b", re.I),
)
class Visible(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

def explicit_release_date(html_text, title, today=None):
    from datetime import datetime
    parser = Visible()
    parser.feed(html_text)
    text = " ".join(" ".join(parser.parts).split())
    if not title or title.casefold() not in text.casefold():
        return None
    found = set()
    for pattern in PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1).replace(",", "")
            try:
                day = datetime.strptime(raw, "%B %d %Y").date()
            except ValueError:
                continue
            if day >= (today or date.today()):
                found.add(day)
    return next(iter(found)).isoformat() if len(found) == 1 else None

async def verify_jiostar_article(session, item, today=None):
    url = item.get("source_url", "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.jiostar.com", "jiostar.com"} or parsed.username or parsed.password or parsed.port:
        return None
    async with session.get(url, allow_redirects=False) as response:
        if response.status != 200 or "text/html" not in response.headers.get("Content-Type", "").lower():
            return None
        body = await response.content.read(750001)
        if len(body) > 750000:
            return None
    return explicit_release_date(body.decode("utf-8", "replace"), item.get("title", ""), today)
