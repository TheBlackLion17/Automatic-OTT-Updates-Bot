"""Conservative official newsroom candidate discovery.

A candidate is a source headline, not a verified OTT release. Dates and
languages are deliberately left unknown for human review.
"""
import hashlib
import logging
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

LOG = logging.getLogger("ott")
JIOSTAR_NEWSROOM = "https://www.jiostar.com/newsroom/"
ALLOWED_HOSTS = {"www.jiostar.com", "jiostar.com"}
RELEASE_TERMS = (
    "streaming on jiohotstar", "stream on jiohotstar",
    "premiering on jiohotstar", "premieres on jiohotstar",
    "premiere on jiohotstar", "to stream on jiohotstar",
    "to premiere on jiohotstar", "on jiohotstar from",
    "streaming from", "to stream on", "only on jiohotstar",
)

class NewsLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.parts = []
    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)
    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            title = " ".join(" ".join(self.parts).split())
            if title:
                self.links.append((title, self.href))
            self.href = None
            self.parts = []

def candidates_from_html(content, base=JIOSTAR_NEWSROOM):
    parser = NewsLinks()
    parser.feed(content)
    found = []
    seen = set()
    for title, href in parser.links:
        url = urljoin(base, href)
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
                or parsed.username or parsed.password or parsed.port
                or not any(term in title.casefold() for term in RELEASE_TERMS)
                or url in seen or len(title) > 255):
            continue
        seen.add(url)
        found.append({
            "title": title, "source_url": url,
            "source_key": hashlib.sha256(("newsroom:" + url).encode()).hexdigest(),
            "platform": "JioHotstar",
            "summary": "Official newsroom headline; OTT release date and language require admin verification.",
        })
    return found[:30]

async def discover_jiostar(session):
    try:
        async with session.get(JIOSTAR_NEWSROOM, allow_redirects=False) as response:
            if response.status != 200 or "text/html" not in response.headers.get("Content-Type", "").lower():
                LOG.warning("JioStar newsroom response rejected: %s", response.status)
                return []
            payload = await response.content.read(750001)
            if len(payload) > 750000:
                LOG.warning("JioStar newsroom too large")
                return []
            return candidates_from_html(payload.decode("utf-8", errors="replace"))
    except Exception:
        LOG.exception("JioStar newsroom discovery failed")
        return []
