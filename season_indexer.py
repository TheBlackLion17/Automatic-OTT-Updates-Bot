"""Season-grouped Telegram indexing, announcements and private delivery."""
import asyncio
import html
import logging
import os
import re
from datetime import datetime, timedelta
from pyrogram import Client, filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import RPCError
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger("ott.seasons")
ENABLED = os.getenv("FILE_INDEX_MODE", "false").lower() == "true"
SOURCES = {int(v) for v in os.getenv("FILE_SOURCE_CHANNEL_IDS", "").split(",") if v.strip()}
UPDATE = (os.getenv("FILE_UPDATE_CHAT_USERNAME", "").strip()
          or int(os.getenv("FILE_UPDATE_CHAT_ID", os.getenv("TARGET_CHAT_ID", "0"))))
FORCE = [int(v) for v in os.getenv("FILE_FORCE_CHANNEL_IDS", "").split(",") if v.strip()]
DELETE_AFTER = max(0, int(os.getenv("FILE_DELETE_SECONDS", "300")))
HISTORY_SESSION = os.getenv("FILE_HISTORY_SESSION", "").strip()
HISTORY_API_ID = os.getenv("API_ID", "")
HISTORY_API_HASH = os.getenv("API_HASH", "")
_history = None
_locks = {}
_bot_name = None

def parse_episode(message):
    media = message.document or message.video or message.audio
    if not media or not media.file_unique_id:
        return None
    name = (getattr(media, "file_name", None) or (message.caption or "").splitlines()[0]).strip()
    # Accept Wednesday S01 E01, Wednesday Season 1 Episode 01, and separators.
    match = re.search(r"(?i)\b(?:s(?:eason)?[ ._-]*)(\d{1,2})[ ._-]*(?:e(?:p(?:isode)?)?[ ._-]*)(\d{1,3})\b", name)
    if not match:
        return None
    title = re.sub(r"[._-]+", " ", name[:match.start()]).strip(" -._")
    if not title:
        return None
    return title[:255], int(match.group(1)), int(match.group(2)), media, name[:512]

LANGUAGES = ("Malayalam", "English", "Hindi", "Tamil", "Telugu", "Kannada", "Korean", "Japanese", "Spanish", "French", "German", "Arabic")
def detect_language(text):
    for language in LANGUAGES:
        if re.search(r"(?i)(?<![a-z])" + language + r"(?![a-z])", text):
            return language
    return "Unknown"

def detect_quality(text):
    m = re.search(r"(?i)(?<![a-z0-9])(2160p|4k|1080p|720p|480p)(?![a-z0-9])", text)
    return ("4K" if m.group(1).lower() in ("2160p", "4k") else m.group(1).lower()) if m else "Unknown"

async def initialize(sql):
    await sql("""CREATE TABLE IF NOT EXISTS season_groups (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        title_key VARCHAR(255) NOT NULL,
        season_number INT NOT NULL,
        announcement_id BIGINT NULL,
        publish_state VARCHAR(20) NOT NULL DEFAULT 'pending',
        UNIQUE KEY uniq_season(title_key,season_number)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    await sql("""CREATE TABLE IF NOT EXISTS season_episodes (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        group_id BIGINT NOT NULL,
        episode_number INT NOT NULL,
        file_unique_id VARCHAR(255) NOT NULL UNIQUE,
        source_chat_id BIGINT NOT NULL,
        source_message_id BIGINT NOT NULL,
        file_name VARCHAR(512) NOT NULL,
        file_size BIGINT NOT NULL DEFAULT 0,
        language VARCHAR(64) NOT NULL DEFAULT 'Unknown',
        UNIQUE KEY uniq_message(source_chat_id,source_message_id),
        INDEX idx_group_episode(group_id,episode_number)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cols = await sql("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='season_episodes' AND COLUMN_NAME='language'",fetch=True)
    if not cols:
        await sql("ALTER TABLE season_episodes ADD COLUMN language VARCHAR(64) NOT NULL DEFAULT 'Unknown'")
    await sql("""CREATE TABLE IF NOT EXISTS movie_groups (
        id BIGINT AUTO_INCREMENT PRIMARY KEY, title VARCHAR(255) NOT NULL,
        title_key VARCHAR(255) NOT NULL, release_year INT NOT NULL,
        announcement_id BIGINT NULL, publish_state VARCHAR(20) NOT NULL DEFAULT 'pending',
        UNIQUE KEY uniq_movie(title_key,release_year)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    await sql("""CREATE TABLE IF NOT EXISTS movie_files (
        id BIGINT AUTO_INCREMENT PRIMARY KEY, group_id BIGINT NOT NULL,
        language VARCHAR(64) NOT NULL DEFAULT 'Unknown',
        quality VARCHAR(20) NOT NULL DEFAULT 'Unknown',
        file_unique_id VARCHAR(255) NOT NULL UNIQUE,
        source_chat_id BIGINT NOT NULL, source_message_id BIGINT NOT NULL,
        file_name VARCHAR(512) NOT NULL,
        UNIQUE KEY uniq_movie_message(source_chat_id,source_message_id),
        INDEX idx_movie_group(group_id,language,quality)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    await sql("""CREATE TABLE IF NOT EXISTS season_deliveries (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL,
        delete_at DATETIME NOT NULL,
        UNIQUE KEY uniq_delivery(user_id,message_id),
        INDEX idx_delete(delete_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")

async def announce(app, sql, group_id):
    rows = await sql("SELECT * FROM season_groups WHERE id=%s", (group_id,), fetch=True)
    if not rows:
        return
    group = rows[0]
    count = await sql("SELECT COUNT(*) AS n, MIN(episode_number) AS first_ep, MAX(episode_number) AS last_ep FROM season_episodes WHERE group_id=%s", (group_id,), fetch=True)
    stats = count[0]
    if not stats["n"]:
        return
    global _bot_name
    if not _bot_name:
        _bot_name = (await app.get_me()).username
    url = f"https://t.me/{_bot_name}?start=season_{group_id}"
    body = (f"🎬 <b>{html.escape(group['title'])} — Season {group['season_number']}</b>\n\n"
            f"📺 Episodes available: {stats['n']} (E{stats['first_ep']:02d}–E{stats['last_ep']:02d})\n"
            "📥 Tap below to get the full available season in PM.")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📥 Get Season", url=url)]])
    if group["announcement_id"]:
        try:
            await app.edit_message_text(UPDATE, group["announcement_id"], body, reply_markup=keyboard)
            return
        except RPCError as exc:
            if "MESSAGE_NOT_MODIFIED" in str(exc).upper():
                return
            log.exception("Season announcement edit failed for %s", group_id)
            return  # Never post a replacement automatically; avoid duplicates.
    # Mark as sending before Telegram call. Unknown outcomes require manual review.
    changed = await sql("UPDATE season_groups SET publish_state='sending' WHERE id=%s AND publish_state='pending' AND announcement_id IS NULL", (group_id,), affected=True)
    if not changed:
        return
    try:
        posted = await app.send_message(UPDATE, body, reply_markup=keyboard)
        await sql("UPDATE season_groups SET announcement_id=%s,publish_state='published' WHERE id=%s", (posted.id,group_id))
    except Exception:
        log.exception("Season announcement outcome uncertain for %s; check channel before retrying",group_id)

async def index_one(app, sql, message):
    parsed = parse_episode(message)
    if not parsed or message.chat.id not in SOURCES:
        return False
    title, season, episode, media, filename = parsed
    language = detect_language(filename + ' ' + (message.caption or ''))
    key = " ".join(title.casefold().split())
    await sql("INSERT IGNORE INTO season_groups(title,title_key,season_number) VALUES(%s,%s,%s)", (title,key,season))
    groups = await sql("SELECT id FROM season_groups WHERE title_key=%s AND season_number=%s", (key,season),fetch=True)
    group_id = groups[0]["id"]
    inserted = await sql("""INSERT IGNORE INTO season_episodes
        (group_id,episode_number,file_unique_id,source_chat_id,source_message_id,file_name,file_size,language)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
        (group_id,episode,media.file_unique_id,message.chat.id,message.id,filename,getattr(media,"file_size",0) or 0,language),affected=True)
    if not inserted and language != 'Unknown':
        await sql("UPDATE season_episodes SET language=%s WHERE file_unique_id=%s AND language='Unknown'",(language,media.file_unique_id))
    if inserted:
        await announce(app,sql,group_id)
    return bool(inserted)

async def membership(app, user_id):
    missing = []
    for chat_id in FORCE:
        try:
            member = await app.get_chat_member(chat_id,user_id)
            if member.status in (ChatMemberStatus.LEFT,ChatMemberStatus.BANNED) or (member.status == ChatMemberStatus.RESTRICTED and not member.is_member):
                missing.append(chat_id)
        except RPCError:
            log.exception("Cannot verify membership for %s",chat_id)
            return None
    return missing

async def choose_season_language(sql,message,group_id):
    group = await sql("SELECT title,season_number FROM season_groups WHERE id=%s",(group_id,),fetch=True)
    if not group:
        return await message.reply_text("Season not found.")
    rows = await sql("SELECT language,COUNT(*) n FROM season_episodes WHERE group_id=%s GROUP BY language ORDER BY language",(group_id,),fetch=True)
    buttons = [[InlineKeyboardButton(f"{r['language']} ({r['n']})",callback_data=f"sl:{group_id}:{i}")] for i,r in enumerate(rows)]
    await message.reply_text(f"📺 {group[0]['title']} Season {group[0]['season_number']}\nChoose language:",reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

async def check_join(app,message,user_id,callback):
    missing = await membership(app,user_id)
    if missing is None:
        await message.reply_text("Membership verification unavailable.")
        return False
    if not missing:
        return True
    buttons = []
    for chat_id in missing:
        try:
            chat = await app.get_chat(chat_id)
            link = "https://t.me/"+chat.username if chat.username else getattr(chat,"invite_link",None)
            if link:
                buttons.append([InlineKeyboardButton("Join "+(chat.title or "Channel")[:35],url=link)])
        except RPCError:
            log.exception("Cannot get join link")
    buttons.append([InlineKeyboardButton("Verify",callback_data=callback)])
    await message.reply_text("Join required channels first.",reply_markup=InlineKeyboardMarkup(buttons))
    return False

async def deliver(app,sql,message,user_id,items,label):
    if not items:
        return await message.reply_text("No files available.")
    delivered = 0
    for item in items:
        try:
            sent = await app.copy_message(user_id,item["source_chat_id"],item["source_message_id"])
            delivered += 1
            if DELETE_AFTER:
                await sql("INSERT IGNORE INTO season_deliveries(user_id,message_id,delete_at) VALUES(%s,%s,%s)",
                    (user_id,sent.id,datetime.utcnow()+timedelta(seconds=DELETE_AFTER)))
        except RPCError:
            log.exception("Delivery failed for file %s",item["id"])
        await asyncio.sleep(0.3)
    await app.send_message(user_id,f"Sent {delivered}/{len(items)} files: {label}."
        +(f" Auto-delete after {DELETE_AFTER} seconds." if DELETE_AFTER else ""))

async def send_season_language(app,sql,message,user_id,group_id,lang_id):
    langs = await sql("SELECT language FROM season_episodes WHERE group_id=%s GROUP BY language ORDER BY language",(group_id,),fetch=True)
    if lang_id >= len(langs):
        return await message.reply_text("Language option expired.")
    if not await check_join(app,message,user_id,f"sl:{group_id}:{lang_id}"):
        return
    language = langs[lang_id]["language"]
    items = await sql("SELECT * FROM season_episodes WHERE group_id=%s AND language=%s ORDER BY episode_number,id",(group_id,language),fetch=True)
    await deliver(app,sql,message,user_id,items,f"Season {group_id} / {language}")

async def index_movie(app,sql,message):
    media = message.document or message.video or message.audio
    if not media or not media.file_unique_id or message.chat.id not in SOURCES:
        return False
    name = (getattr(media,"file_name",None) or (message.caption or "").splitlines()[0]).strip()
    if parse_episode(message):
        return False
    year = re.search(r"\b(?:19|20)\d{2}\b",name)
    if not year:
        return False
    title = re.sub(r"[._-]+"," ",name[:year.start()]).strip()
    if not title:
        return False
    key = " ".join(title.casefold().split())
    await sql("INSERT IGNORE INTO movie_groups(title,title_key,release_year) VALUES(%s,%s,%s)",(title[:255],key[:255],int(year.group())))
    groups = await sql("SELECT id FROM movie_groups WHERE title_key=%s AND release_year=%s",(key[:255],int(year.group())),fetch=True)
    group_id = groups[0]["id"]
    language = detect_language(name+" "+(message.caption or ""))
    quality = detect_quality(name+" "+(message.caption or ""))
    inserted = await sql("""INSERT IGNORE INTO movie_files
        (group_id,language,quality,file_unique_id,source_chat_id,source_message_id,file_name)
        VALUES(%s,%s,%s,%s,%s,%s,%s)""",(group_id,language,quality,media.file_unique_id,message.chat.id,message.id,name[:512]),affected=True)
    if inserted:
        await announce_movie(app,sql,group_id)
    return bool(inserted)

async def announce_movie(app,sql,group_id):
    rows = await sql("SELECT * FROM movie_groups WHERE id=%s",(group_id,),fetch=True)
    group = rows[0]
    global _bot_name
    if not _bot_name:
        _bot_name = (await app.get_me()).username
    body = f"🎬 <b>{html.escape(group['title'])} ({group['release_year']})</b>\n\nChoose language and quality in PM."
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Get Movie",url=f"https://t.me/{_bot_name}?start=movie_{group_id}")]])
    if group["announcement_id"]:
        try:
            await app.edit_message_text(UPDATE,group["announcement_id"],body,reply_markup=kb)
        except RPCError as exc:
            if "MESSAGE_NOT_MODIFIED" not in str(exc).upper():
                log.exception("Could not edit movie announcement")
        return
    changed = await sql("UPDATE movie_groups SET publish_state='sending' WHERE id=%s AND publish_state='pending' AND announcement_id IS NULL",(group_id,),affected=True)
    if not changed:
        return
    try:
        posted = await app.send_message(UPDATE,body,reply_markup=kb)
        await sql("UPDATE movie_groups SET announcement_id=%s,publish_state='published' WHERE id=%s",(posted.id,group_id))
    except Exception:
        log.exception("Movie announcement outcome uncertain")

async def choose_movie_language(sql,message,group_id):
    rows = await sql("SELECT language,COUNT(*) n FROM movie_files WHERE group_id=%s GROUP BY language ORDER BY language",(group_id,),fetch=True)
    buttons = [[InlineKeyboardButton(f"{r['language']} ({r['n']})",callback_data=f"ml:{group_id}:{i}")] for i,r in enumerate(rows)]
    await message.reply_text("Choose movie language:",reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

async def choose_movie_quality(sql,message,group_id,lang_id):
    langs = await sql("SELECT language FROM movie_files WHERE group_id=%s GROUP BY language ORDER BY language",(group_id,),fetch=True)
    if lang_id >= len(langs):
        return await message.reply_text("Language expired.")
    rows = await sql("SELECT quality FROM movie_files WHERE group_id=%s AND language=%s GROUP BY quality ORDER BY quality",(group_id,langs[lang_id]["language"]),fetch=True)
    buttons = [[InlineKeyboardButton(r["quality"],callback_data=f"mq:{group_id}:{lang_id}:{i}")] for i,r in enumerate(rows)]
    await message.reply_text("Choose quality:",reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

async def send_movie_quality(app,sql,message,user_id,group_id,lang_id,quality_id):
    langs = await sql("SELECT language FROM movie_files WHERE group_id=%s GROUP BY language ORDER BY language",(group_id,),fetch=True)
    if lang_id >= len(langs):
        return await message.reply_text("Language expired.")
    language = langs[lang_id]["language"]
    qualities = await sql("SELECT quality FROM movie_files WHERE group_id=%s AND language=%s GROUP BY quality ORDER BY quality",(group_id,language),fetch=True)
    if quality_id >= len(qualities):
        return await message.reply_text("Quality expired.")
    if not await check_join(app,message,user_id,f"mq:{group_id}:{lang_id}:{quality_id}"):
        return
    quality = qualities[quality_id]["quality"]
    items = await sql("SELECT * FROM movie_files WHERE group_id=%s AND language=%s AND quality=%s ORDER BY id",(group_id,language,quality),fetch=True)
    await deliver(app,sql,message,user_id,items,f"Movie {group_id} / {language} / {quality}")

async def deletion_worker(app,sql):
    while True:
        try:
            due = await sql("SELECT id,user_id,message_id FROM season_deliveries WHERE delete_at <= UTC_TIMESTAMP() ORDER BY id LIMIT 100",fetch=True)
            for row in due:
                try:
                    await app.delete_messages(row["user_id"],row["message_id"])
                    await sql("DELETE FROM season_deliveries WHERE id=%s",(row["id"],))
                except RPCError:
                    log.exception("Unable to delete delivered file %s",row["id"])
            await asyncio.sleep(15)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Delivery cleanup worker error")
            await asyncio.sleep(30)

async def scan_history(app,sql):
    global _history
    if not HISTORY_SESSION:
        log.info("Historical scan disabled; set FILE_HISTORY_SESSION for old posts")
        return
    _history = Client("season_history",api_id=int(HISTORY_API_ID),api_hash=HISTORY_API_HASH,session_string=HISTORY_SESSION,in_memory=True)
    await _history.start()
    try:
        for chat_id in SOURCES:
            # Idempotent: repeated scans skip known unique file IDs.
            async for post in _history.get_chat_history(chat_id):
                try:
                    if not await index_one(app,sql,post):
                        await index_movie(app,sql,post)
                except Exception:
                    log.exception("Failed to index historical message %s/%s",chat_id,post.id)
    finally:
        await _history.stop()
        _history = None

def register(app,sql,admins):
    if not ENABLED:
        return
    @app.on_message(filters.channel & (filters.document|filters.video|filters.audio))
    async def new_media(_,message):
        if message.chat.id in SOURCES:
            if not await index_one(app,sql,message):
                await index_movie(app,sql,message)

    @app.on_message(filters.private & filters.command("start"),group=-1)
    async def season_start(_,message):
        if len(message.command)>1:
            match = re.fullmatch(r"season_(\d+)",message.command[1])
            if match:
                await choose_season_language(sql,message,int(match.group(1)))
                message.stop_propagation()
            movie = re.fullmatch(r"movie_(\d+)",message.command[1])
            if movie:
                await choose_movie_language(sql,message,int(movie.group(1)))
                message.stop_propagation()

    @app.on_callback_query(filters.regex(r"^(sl|ml|mq):\\d+:\\d+(?::\\d+)?$"))
    async def language_callback(_,query):
        await query.answer()
        if not query.message or not query.from_user or query.message.chat.type.value != "private":
            return
        parts = query.data.split(":")
        if parts[0] == "sl":
            await send_season_language(app,sql,query.message,query.from_user.id,int(parts[1]),int(parts[2]))
        elif parts[0] == "ml":
            await choose_movie_quality(sql,query.message,int(parts[1]),int(parts[2]))
        else:
            await send_movie_quality(app,sql,query.message,query.from_user.id,int(parts[1]),int(parts[2]),int(parts[3]))

    @app.on_callback_query(filters.regex(r"^seasonget:\d+$"))
    async def verify(_,query):
        await query.answer()
        if query.message and query.from_user:
            await choose_season_language(sql,query.message,int(query.data.split(":")[1]))

    @app.on_message(filters.private & filters.command("indexold"))
    async def old(_,message):
        if message.from_user.id not in admins:
            return
        if not HISTORY_SESSION:
            return await message.reply_text("Set FILE_HISTORY_SESSION in .env for historical channel indexing.")
        if getattr(app,"season_history_task",None) and not app.season_history_task.done():
            return await message.reply_text("Historical indexing is already running.")
        app.season_history_task = asyncio.create_task(scan_history(app,sql))
        await message.reply_text("Historical indexing started. Existing files are skipped; season posts are edited.")

    @app.on_message(filters.private & filters.command("seasonstats"))
    async def stats(_,message):
        if message.from_user.id not in admins:
            return
        groups = await sql("SELECT COUNT(*) AS n FROM season_groups",fetch=True)
        episodes = await sql("SELECT COUNT(*) AS n FROM season_episodes",fetch=True)
        await message.reply_text(f"📊 Seasons: {groups[0]['n']}\nEpisodes: {episodes[0]['n']}")
