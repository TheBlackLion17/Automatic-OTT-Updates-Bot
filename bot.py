import asyncio, hashlib, html, logging, os
from datetime import date
from urllib.parse import urlsplit
import aiohttp, aiomysql, feedparser
from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from pyrogram.enums import ParseMode
from official_discovery import discover
from official_newsroom import discover_jiostar
from release_review import clean_newsroom_title, extract_explicit_languages, publication_issues
from official_page_verifier import verify_official_page
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ott")
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
TARGET = int(os.environ["TARGET_CHAT_ID"])
FEEDS = [x.strip() for x in os.getenv("RSS_FEEDS", "").split(",") if x.strip()]
INTERVAL = max(5, int(os.getenv("CHECK_INTERVAL_MINUTES", "60")))
OFFICIAL_FEEDS = [x.strip() for x in os.getenv("OFFICIAL_JSON_FEEDS", "").split(",") if x.strip()]
FEED_DOMAINS = set(filter(None, os.getenv("TRUSTED_FEED_DOMAINS", "").lower().split(",")))
OFFICIAL_DOMAINS = set(filter(None, os.getenv("OFFICIAL_SOURCE_DOMAINS", "").lower().split(",")))
PLATFORMS = set(filter(None, os.getenv("ALLOWED_PLATFORMS", "").casefold().split(",")))
AUTO_PUBLISH = os.getenv("AUTO_PUBLISH_VERIFIED", "false").lower() == "true"
ENABLE_JIOSTAR_NEWSROOM = os.getenv("ENABLE_JIOSTAR_NEWSROOM", "false").lower() == "true"
VERIFY_OFFICIAL_PAGE = os.getenv("VERIFY_OFFICIAL_PAGE", "true").lower() == "true"
app = Client("ott_updates", api_id=int(os.environ["API_ID"]), api_hash=os.environ["API_HASH"], bot_token=os.environ["BOT_TOKEN"])
pool = None

def valid_url(s):
    try:
        u = urlsplit(s)
        return u.scheme == "https" and bool(u.hostname) and not u.username and not u.password
    except Exception:
        return False

async def sql(statement, args=(), fetch=False):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(statement, args)
            return await cur.fetchall() if fetch else cur.lastrowid

def render(r):
    e = lambda s: html.escape(str(s or "Not confirmed"))
    return (f"🎬 <b>OTT UPDATE</b>\n\n<b>{e(r['title'])}</b>\n"
            f"📺 Platform: {e(r['platform'])}\n🌐 Language: {e(r['language'])}\n"
            f"📅 OTT release: {e(r['release_date'])}\n\n{e(r['summary'])}\n\n"
            "ℹ️ Verify the platform and release date against official sources.")

async def scan():
    count = 0
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "MoviesHubOTT/1.0"}) as session:
        for url in FEEDS:
            if not valid_url(url):
                continue
            try:
                async with session.get(url, allow_redirects=False) as resp:
                    resp.raise_for_status()
                    body = await resp.content.read(1000001)
                    if len(body) > 1000000:
                        raise ValueError("Feed too large")
                for item in feedparser.parse(body).entries[:25]:
                    link = item.get("link", "")
                    title = item.get("title", "").strip()[:255]
                    if not title or not valid_url(link):
                        continue
                    ident = hashlib.sha256(link.encode()).hexdigest()
                    await sql("INSERT IGNORE INTO releases(title,source_url,source_key,summary) VALUES(%s,%s,%s,%s)",
                              (title, link, ident, "RSS headline. Verify OTT details before approval."))
                    count += 1
            except Exception:
                log.exception("Feed failed: %s", url)
    return count

async def publish_release(r):
    buttons = []
    for label, field in (("🎞 Trailer", "trailer_url"), ("🔗 Official source", "source_url")):
        if valid_url(r.get(field) or ""):
            buttons.append([InlineKeyboardButton(label, url=r[field])])
    msg = await app.send_message(
        TARGET, render(r), parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
        disable_web_page_preview=True
    )
    await sql("UPDATE releases SET status='published',published_message_id=%s,published_at=NOW() WHERE id=%s",
              (msg.id, r["id"]))

async def scan_official():
    """Import trusted structured announcements into the review queue.

    Discovery is independent of AUTO_PUBLISH_VERIFIED. Nothing from this
    collector is published automatically; an admin must use /approve ID.
    """
    if not (OFFICIAL_FEEDS and FEED_DOMAINS and OFFICIAL_DOMAINS and PLATFORMS):
        return 0
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "MoviesHubOTT/1.0"}) as session:
        items = await discover(session, OFFICIAL_FEEDS, FEED_DOMAINS, OFFICIAL_DOMAINS, PLATFORMS)
        added = 0
        for item in items:
            # Only insert genuinely new records. Never reset an existing status.
            existing = await sql("SELECT id FROM releases WHERE source_key=%s", (item["source_key"],), True)
            if existing:
                continue
            if VERIFY_OFFICIAL_PAGE:
                try:
                    if not await verify_official_page(session, item, OFFICIAL_DOMAINS):
                        log.info("Official page not confirmed: %s", item["source_url"])
                        continue
                except Exception:
                    log.exception("Official page verification failed: %s", item["source_url"])
                    continue
            await sql(
                "INSERT IGNORE INTO releases "
                "(title,platform,language,release_date,trailer_url,summary,source_url,source_key,status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending')",
                tuple(item[k] for k in (
                    "title", "platform", "language", "release_date",
                    "trailer_url", "summary", "source_url", "source_key"
                ))
            )
            added += 1
        if added:
            log.info("Imported up to %d official announcements for admin review", added)
        return added

async def scan_newsroom():
    """Queue official newsroom headlines, never infer release dates."""
    if not ENABLE_JIOSTAR_NEWSROOM:
        return 0
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "MoviesHubOTT/1.0"}) as session:
        candidates = await discover_jiostar(session)
    count = 0
    for item in candidates:
        existing = await sql("SELECT id FROM releases WHERE source_key=%s", (item["source_key"],), True)
        if existing:
            continue
        await sql(
            "INSERT IGNORE INTO releases(title,source_url,source_key,platform,language,summary,status) "
            "VALUES(%s,%s,%s,%s,%s,%s,'pending')",
            (clean_newsroom_title(item["title"]), item["source_url"], item["source_key"], item["platform"], extract_explicit_languages(item["title"]), item["summary"])
        )
        count += 1
    if count:
        log.info("Queued up to %d official newsroom headlines for manual review", count)
    return count

async def loop():
    while True:
        try:
            await scan()
            await scan_official()
            await scan_newsroom()
        except Exception:
            log.exception("Scan failed")
        await asyncio.sleep(INTERVAL * 60)

@app.on_message(filters.command("start") & filters.private)
async def start(_, m):
    await m.reply_text("🎬 Movies_Hub_OG OTT Updates\n/latest — recent updates\n/upcoming — upcoming verified dates\n/today — today's releases")

@app.on_message(filters.command(["latest", "upcoming", "today"]) & filters.private)
async def listings(_, m):
    command = m.command[0]
    condition = "status='published'"
    args = ()
    if command == "upcoming":
        condition += " AND release_date > %s"
        args = (date.today(),)
    elif command == "today":
        condition += " AND release_date = %s"
        args = (date.today(),)
    rows = await sql(f"SELECT * FROM releases WHERE {condition} ORDER BY id DESC LIMIT 10", args, True)
    await m.reply_text("\n".join(f"• {r['title']} — {r['platform'] or 'Platform unconfirmed'}" for r in rows) or "No verified updates yet.")

@app.on_message(filters.command("checknow") & filters.private)
async def check(_, m):
    if m.from_user.id not in ADMINS:
        return
    n = await scan()
    official = await scan_official()
    newsroom = await scan_newsroom()
    await m.reply_text(f"Scan complete: up to {n} RSS headlines, {official} structured official candidates and {newsroom} JioStar newsroom headlines. Review with /pending.")

@app.on_message(filters.command("pending") & filters.private)
async def pending(_, m):
    if m.from_user.id not in ADMINS:
        return
    rows = await sql("SELECT id,title FROM releases WHERE status='pending' ORDER BY id DESC LIMIT 20", fetch=True)
    await m.reply_text("\n".join(f"{r['id']}: {r['title']}" for r in rows) or "No pending updates.")

@app.on_message(filters.command("addrelease") & filters.private)
async def add(_, m):
    if m.from_user.id not in ADMINS:
        return
    parts = [p.strip() for p in m.text.partition(" ")[2].split("|")]
    if len(parts) != 6:
        return await m.reply_text("Usage: /addrelease Title | Platform | Language | YYYY-MM-DD | https://official-trailer-url | Summary")
    title, platform, language, day, trailer, summary = parts
    try:
        date.fromisoformat(day)
        if trailer and not valid_url(trailer):
            raise ValueError("Trailer URL must be HTTPS")
    except ValueError as e:
        return await m.reply_text(f"Invalid date or trailer URL: {e}")
    ident = hashlib.sha256(("manual:"+title+platform+day).encode()).hexdigest()
    await sql("INSERT IGNORE INTO releases(title,platform,language,release_date,trailer_url,summary,source_key) VALUES(%s,%s,%s,%s,%s,%s,%s)",
              (title[:255],platform[:100],language[:100],day,trailer,summary,ident))
    await m.reply_text("Added to pending queue. Use /pending then /approve ID.")

@app.on_message(filters.command("viewrelease") & filters.private)
async def viewrelease(_, m):
    if m.from_user.id not in ADMINS:
        return
    try:
        ident = int(m.command[1])
    except (IndexError, ValueError):
        return await m.reply_text("Usage: /viewrelease ID")
    rows = await sql("SELECT * FROM releases WHERE id=%s", (ident,), True)
    if not rows:
        return await m.reply_text("Release not found.")
    r = rows[0]
    fields = ("title", "platform", "language", "release_date", "source_url", "trailer_url", "summary", "status")
    message = "\n".join(f"{key}: {r.get(key) or 'NOT SET'}" for key in fields)
    problems = publication_issues(r)
    await m.reply_text(f"Release #{ident}\n{message}\n\n" +
                       ("Missing/invalid: " + ", ".join(problems) if problems else "Ready for admin approval."),
                       disable_web_page_preview=True)

@app.on_message(filters.command("editrelease") & filters.private)
async def editrelease(_, m):
    if m.from_user.id not in ADMINS:
        return
    import re
    raw = m.text.partition(" ")[2].strip()
    match = re.fullmatch(r"(\d+)\s+(title|platform|language|release_date|source_url|trailer_url|summary)\s*\|\s*(.*)", raw, re.I | re.S)
    if not match:
        return await m.reply_text("Usage: /editrelease ID field | value\nFields: title, platform, language, release_date, source_url, trailer_url, summary")
    ident, field, value = int(match[1]), match[2].lower(), match[3].strip()
    if field in ("title", "platform", "language") and (not value or len(value) > (255 if field == "title" else 100)):
        return await m.reply_text("A nonempty value within the field length limit is required.")
    if field == "release_date":
        try:
            date.fromisoformat(value)
        except ValueError:
            return await m.reply_text("Date must be YYYY-MM-DD and verified against the official announcement.")
    if field == "source_url" and not valid_url(value):
        return await m.reply_text("Official source URL must use HTTPS.")
    if field == "trailer_url" and value and not valid_url(value):
        return await m.reply_text("Trailer URL must use HTTPS.")
    rows = await sql("SELECT id FROM releases WHERE id=%s AND status='pending'", (ident,), True)
    if not rows:
        return await m.reply_text("Only pending releases can be edited.")
    await sql(f"UPDATE releases SET {field}=%s WHERE id=%s AND status='pending'", (value or None, ident))
    await m.reply_text(f"Updated {field} for #{ident}. Verify the official source, then /viewrelease {ident}.")

@app.on_message(filters.command("approve") & filters.private)
async def approve(_, m):
    if m.from_user.id not in ADMINS:
        return
    try:
        ident = int(m.command[1])
    except (IndexError, ValueError):
        return await m.reply_text("Usage: /approve ID")
    rows = await sql("SELECT * FROM releases WHERE id=%s AND status='pending'", (ident,), True)
    if not rows:
        return await m.reply_text("Not found or already processed.")
    r = rows[0]
    problems = publication_issues(r)
    if problems:
        return await m.reply_text("Cannot publish. Missing/invalid: " + ", ".join(problems) + f"\nUse /viewrelease {ident} and /editrelease.")
    await sql("UPDATE releases SET status='sending' WHERE id=%s AND status='pending'", (ident,))
    try:
        await publish_release(r)
    except Exception:
        log.exception("Delivery failed; review before retry")
        return await m.reply_text("Delivery uncertain; status remains sending to prevent duplicate retries.")
    await m.reply_text("Published.")

@app.on_message(filters.command("reject") & filters.private)
async def reject(_, m):
    if m.from_user.id not in ADMINS:
        return
    try:
        ident = int(m.command[1])
    except (IndexError, ValueError):
        return await m.reply_text("Usage: /reject ID")
    await sql("UPDATE releases SET status='rejected' WHERE id=%s AND status='pending'", (ident,))
    await m.reply_text("Rejected if pending.")

async def main():
    global pool
    pool = await aiomysql.create_pool(host=os.getenv("MYSQL_HOST","127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT","3306")),user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],db=os.getenv("MYSQL_DATABASE","ott_updates"),
        autocommit=True)
    await app.start()
    task = asyncio.create_task(loop())
    try:
        await idle()
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)
        await app.stop()
        pool.close()
        await pool.wait_closed()

if __name__ == "__main__":
    # Pyrogram 2.x expects its client and dispatcher on its own event loop.
    # Avoid asyncio.run(), which creates a separate event loop.
    app.run(main())
