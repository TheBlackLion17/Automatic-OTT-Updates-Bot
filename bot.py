import asyncio, hashlib, html, logging, os
from datetime import date
from urllib.parse import urlsplit
import aiohttp, aiomysql, feedparser
from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ott")
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
TARGET = int(os.environ["TARGET_CHAT_ID"])
FEEDS = [x.strip() for x in os.getenv("RSS_FEEDS", "").split(",") if x.strip()]
INTERVAL = max(5, int(os.getenv("CHECK_INTERVAL_MINUTES", "60")))
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

async def loop():
    while True:
        try:
            await scan()
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
    await m.reply_text(f"Feed scan complete. Processed up to {n} headlines; duplicates are ignored.")

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
    await sql("UPDATE releases SET status='sending' WHERE id=%s AND status='pending'", (ident,))
    buttons = []
    for label, field in (("🎞 Trailer","trailer_url"),("🔗 Source","source_url")):
        if valid_url(r.get(field) or ""):
            buttons.append([InlineKeyboardButton(label,url=r[field])])
    try:
        msg = await app.send_message(TARGET, render(r), parse_mode="html",
                                     reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
                                     disable_web_page_preview=True)
    except Exception:
        log.exception("Delivery failed; review before retry")
        return await m.reply_text("Delivery uncertain; status remains sending to prevent duplicate retries.")
    await sql("UPDATE releases SET status='published',published_message_id=%s,published_at=NOW() WHERE id=%s", (msg.id,ident))
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
    asyncio.run(main())
