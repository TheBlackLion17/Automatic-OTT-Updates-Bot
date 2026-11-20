"""Conservative cleanup and publication readiness checks for OTT releases."""
import re
from datetime import date
from urllib.parse import urlsplit

MONTHS = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
SUFFIX = re.compile(r"\s+(?:to stream|streaming|premieres?|premiering|to premiere|will stream|is streaming)\s+on\s+JioHotstar\b.*$", re.I)
LEADING = re.compile(r"^(?:Hotstar Specials\s*)?[‘'\"“]?(.+?)[’'\"”]?$")
LANGUAGE_WORDS = ("Malayalam", "Tamil", "Telugu", "Kannada", "Hindi", "English", "Bengali", "Marathi")

def clean_newsroom_title(headline):
    """Remove descriptive article excerpts and streaming suffixes, without inventing a title."""
    headline = " ".join((headline or "").split())
    if not headline:
        return ""
    # Preserve the source's named title; stop before the newsroom's streaming announcement.
    m = SUFFIX.search(headline)
    if not m:
        return headline[:255]
    prefix = headline[:m.start()].strip(" -–—:‘’“”\"'")
    if prefix.casefold().startswith("hotstar specials "):
        prefix = prefix[len("hotstar specials "):].strip(" -–—:‘’“”\"'")
    return prefix[:255] if prefix else headline[:255]

def extract_explicit_languages(text):
    """Only extract language names literally present in an announcement."""
    found = [lang for lang in LANGUAGE_WORDS if re.search(r"\b" + lang + r"\b", text or "", re.I)]
    return ", ".join(found) if found else None

def publication_issues(record):
    problems = []
    for key, label in (("title", "title"), ("platform", "platform"), ("language", "language"), ("release_date", "release date"), ("source_url", "official source URL")):
        if not record.get(key) or not str(record[key]).strip():
            problems.append(label)
    if record.get("release_date"):
        try:
            day = date.fromisoformat(str(record["release_date"]))
            if not 2000 <= day.year <= 2100:
                problems.append("plausible release year")
        except (ValueError, TypeError):
            problems.append("valid ISO release date")
    url = record.get("source_url") or ""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port:
            problems.append("valid HTTPS official source")
    except ValueError:
        problems.append("valid HTTPS official source")
    return problems
