"""Read-only diagnostics and conservative pending re-verification helpers."""
import logging
import aiohttp
from official_article_dates import verify_jiostar_article
from release_review import clean_newsroom_title, extract_explicit_languages, publication_issues

log = logging.getLogger("ott")

async def recheck_pending(sql, publish_claimed, enabled):
    if not enabled:
        return 0
    rows = await sql("SELECT * FROM releases WHERE status='pending' AND platform='JioHotstar' ORDER BY id LIMIT 30", fetch=True)
    published = 0
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        for row in rows:
            title = clean_newsroom_title(row["title"])
            language = extract_explicit_languages(row["title"]) or row.get("language")
            if not language:
                continue
            try:
                day = await verify_jiostar_article(session, {"source_url": row["source_url"], "title": title})
            except Exception:
                log.exception("Pending verification error for release %s", row["id"])
                continue
            if not day:
                continue
            candidate = dict(row, title=title, language=language, release_date=day)
            if publication_issues(candidate):
                continue
            await sql("UPDATE releases SET title=%s,language=%s,release_date=%s WHERE id=%s AND status='pending'",
                      (title, language, day, row["id"]))
            fresh = await sql("SELECT * FROM releases WHERE id=%s AND status='pending'", (row["id"],), True)
            if fresh and await publish_claimed(fresh[0]):
                published += 1
    return published
