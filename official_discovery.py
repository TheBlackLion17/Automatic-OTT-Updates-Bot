"""Fail-closed discovery from explicitly trusted structured HTTPS release feeds.

A trusted feed is a JSON document {"releases":[...]} served from an operator-approved
HTTPS host. Each release must link to an official announcement on an approved
platform domain. This checks provenance and required metadata, not factual truth.
"""
import hashlib
from datetime import date, datetime
from urllib.parse import urlsplit

def host_allowed(url, domains):
    try:
        u = urlsplit(url)
        return (u.scheme == "https" and u.hostname is not None
                and not u.username and not u.password and not u.port
                and u.hostname.lower() in domains)
    except ValueError:
        return False

def validate_release(item, official_domains, allowed_platforms):
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    platform = item.get("platform")
    language = item.get("language")
    release_date = item.get("release_date")
    source_url = item.get("source_url")
    if not all(isinstance(x, str) and x.strip() for x in
               (title, platform, language, release_date, source_url)):
        return None
    if len(title) > 255 or len(platform) > 100 or len(language) > 100:
        return None
    if platform.casefold() not in allowed_platforms:
        return None
    if not host_allowed(source_url, official_domains):
        return None
    try:
        day = date.fromisoformat(release_date)
    except ValueError:
        return None
    if day < date.today():
        return None
    trailer = item.get("trailer_url") or ""
    if trailer and (not isinstance(trailer, str) or
                    not host_allowed(trailer, official_domains | {"youtube.com", "www.youtube.com", "youtu.be"})):
        trailer = ""
    summary = str(item.get("summary") or "")[:900]
    key = hashlib.sha256(("official:"+source_url+"|"+title.casefold()+"|"+release_date).encode()).hexdigest()
    return {"title": title.strip(), "platform": platform.strip(), "language": language.strip(),
            "release_date": day.isoformat(), "source_url": source_url,
            "trailer_url": trailer, "summary": summary, "source_key": key}

async def discover(session, urls, feed_domains, official_domains, platforms):
    results = []
    for url in urls:
        if not host_allowed(url, feed_domains):
            continue
        try:
            async with session.get(url, allow_redirects=False) as response:
                response.raise_for_status()
                if response.content_length and response.content_length > 1000000:
                    continue
                payload = await response.content.read(1000001)
                if len(payload) > 1000000:
                    continue
                import json
                data = json.loads(payload)
            if not isinstance(data, dict) or not isinstance(data.get("releases"), list):
                continue
            for item in data["releases"][:100]:
                verified = validate_release(item, official_domains, platforms)
                if verified:
                    results.append(verified)
        except Exception:
            import logging
            logging.getLogger("ott").exception("Trusted feed failed: %s", url)
    return results
