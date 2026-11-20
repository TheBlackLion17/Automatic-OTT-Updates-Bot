"""Independent deep-link file bot sharing the OTT MySQL database.
Only index media the bot is authorized to access. No DR DOOM dependencies.
"""
import asyncio
import logging
import os
import re
from urllib.parse import unquote

import aiomysql
from dotenv import load_dotenv
from pyrogram import Client, filters, idle
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

load_dotenv(os.getenv("MOVIE_LINK_ENV", ".env.movie_link"))
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("movie-link")

def required(key):
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Missing {key} in movie-link environment")
    return value

SOURCE = int(required("SOURCE_CHANNEL_ID"))
BOT_USERNAME = required("MOVIE_LINK_USERNAME").lstrip("@")
PAGE_SIZE = 8
ADMINS = {int(v.strip()) for v in os.getenv("MOVIE_LINK_ADMINS", "").split(",") if v.strip()}
app = Client("movie_link_session", api_id=int(required("API_ID")),
             api_hash=required("API_HASH"), bot_token=required("MOVIE_LINK_BOT_TOKEN"))
pool = None

def normalize(value):
    value = re.sub(r"\.(mkv|mp4|avi|mov|webm)$", "", value or "", flags=re.I)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.casefold().split())[:255]

def movie_title(message):
    media = message.document or message.video
    raw = (getattr(media, "file_name", None) or (message.caption or "").split("\n")[0]).strip()
    raw = re.sub(r"\.(mkv|mp4|avi|mov|webm)$", "", raw, flags=re.I)
    raw = re.sub(r"^(?:\[[^\]]+\]\s*)+", "", raw)
    raw = re.split(r"\b(?:480p|720p|1080p|2160p|4k|x264|x265|hevc|web[- ]?dl|bluray|hdrip)\b",
                   raw, maxsplit=1, flags=re.I)[0]
    return re.sub(r"[._]+", " ", raw).strip(" -|[]")[:255]

async def query(sql, params=(), one=False):
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, params)
            return await cur.fetchone() if one else await cur.fetchall()

async def setup():
    global pool
    pool = await aiomysql.create_pool(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=required("MYSQL_USER"), password=required("MYSQL_PASSWORD"),
        db=os.getenv("MYSQL_DATABASE", "ott_updates"),
        autocommit=True, charset="utf8mb4", minsize=1, maxsize=4)
    await query("""CREATE TABLE IF NOT EXISTS movie_link_files (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      source_channel BIGINT NOT NULL,
      source_message BIGINT NOT NULL,
      title VARCHAR(255) NOT NULL,
      normalized_title VARCHAR(255) NOT NULL,
      file_name VARCHAR(1024) NOT NULL DEFAULT '',
      file_size BIGINT NOT NULL DEFAULT 0,
      media_type VARCHAR(16) NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uq_source (source_channel,source_message),
      KEY idx_normalized (normalized_title(191))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")

async def index_message(message):
    media = message.document or message.video
    if not media or not message.chat or message.chat.id != SOURCE:
        return False
    title = movie_title(message)
    if not title:
        return False
    await query("""INSERT INTO movie_link_files
        (source_channel,source_message,title,normalized_title,file_name,file_size,media_type)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE title=VALUES(title),
        normalized_title=VALUES(normalized_title),
        file_name=VALUES(file_name),file_size=VALUES(file_size),
        media_type=VALUES(media_type)""",
        (SOURCE,message.id,title,normalize(title),
         (getattr(media,"file_name",None) or "")[:1024],
         getattr(media,"file_size",0) or 0,
         "video" if message.video else "document"))
    return True

@app.on_message(filters.channel & filters.chat(SOURCE) & (filters.document | filters.video))
async def on_channel(_, message):
    try:
        await index_message(message)
    except Exception:
        log.exception("Index failed for message %s", message.id)

async def search(message, term):
    key = normalize(unquote(term).replace("_", " "))
    if len(key) < 2:
        return await message.reply_text("Please send a movie name to search.")
    # Match a normalized title even when the announcement contains a year/punctuation.
    tokens = key.split()
    rows = await query("""SELECT id,title,file_name,file_size FROM movie_link_files
        WHERE normalized_title LIKE %s ORDER BY id DESC LIMIT %s""",
        ("%" + "%".join(tokens) + "%", PAGE_SIZE))
    if not rows and tokens and re.fullmatch(r"(19|20)\d\d", tokens[-1]):
        rows = await query("""SELECT id,title,file_name,file_size FROM movie_link_files
            WHERE normalized_title LIKE %s ORDER BY id DESC LIMIT %s""",
            ("%" + "%".join(tokens[:-1]) + "%", PAGE_SIZE))
    if not rows:
        return await message.reply_text("No matching files indexed yet. Please try again later.")
    buttons = [[InlineKeyboardButton(
        (row["file_name"] or row["title"])[:60], callback_data=f"ml:get:{row['id']}"
    )] for row in rows]
    await message.reply_text("🎬 Available files — select one:", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_message(filters.command("start") & filters.private)
async def start(_, message):
    payload = message.command[1] if len(message.command) > 1 else ""
    if payload.startswith("search_"):
        return await search(message, payload[7:])
    await message.reply_text("🎬 Welcome! Send a movie title to search indexed files.")

@app.on_message(filters.text & filters.private & ~filters.command(["start", "sync"]))
async def text_search(_, message):
    await search(message, message.text)

@app.on_callback_query(filters.regex(r"^ml:get:\d+$"))
async def deliver(_, callback):
    await callback.answer()
    if not callback.from_user:
        return
    file_id = int(callback.data.rsplit(":", 1)[1])
    row = await query("""SELECT source_channel,source_message FROM movie_link_files
                        WHERE id=%s""", (file_id,), one=True)
    if not row:
        return await callback.message.reply_text("File no longer indexed.")
    try:
        await app.copy_message(callback.from_user.id,
                               row["source_channel"], row["source_message"])
    except FloodWait as exc:
        await asyncio.sleep(exc.value)
        await app.copy_message(callback.from_user.id,
                               row["source_channel"], row["source_message"])
    except RPCError:
        log.exception("Cannot copy source message %s", row["source_message"])
        await callback.message.reply_text(
            "This file is unavailable. Check the bot's access to the source channel.")

@app.on_message(filters.command("sync") & filters.private)
async def sync(_, message):
    if not message.from_user or message.from_user.id not in ADMINS:
        return
    # Import known source message IDs from the OTT announcement table.
    # Telegram bots cannot enumerate arbitrary old channel history.
    rows = await query("""SELECT DISTINCT source_message_id FROM upload_announcements
                         ORDER BY source_message_id DESC LIMIT 500""")
    indexed = failed = 0
    for row in rows:
        try:
            original = await app.get_messages(SOURCE, int(row["source_message_id"]))
            if original and await index_message(original):
                indexed += 1
        except RPCError:
            failed += 1
    await message.reply_text(f"Sync finished: {indexed} indexed, {failed} inaccessible.")

async def main():
    await setup()
    await app.start()
    me = await app.get_me()
    if me.username.casefold() != BOT_USERNAME.casefold():
        raise RuntimeError("MOVIE_LINK_USERNAME does not match the bot token")
    log.info("Movie Link Bot running as @%s; source=%s", me.username, SOURCE)
    try:
        await idle()
    finally:
        await app.stop()
        pool.close()
        await pool.wait_closed()

if __name__ == "__main__":
    asyncio.run(main())
