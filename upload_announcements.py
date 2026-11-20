"""Source-channel movie announcements; never copies uploaded media."""
import hashlib
import html
import json
import logging
import os
import re
from urllib.parse import quote

import aiohttp
from dotenv import load_dotenv
from pyrogram import filters

load_dotenv()

log = logging.getLogger("ott.uploads")

SOURCE = int(os.getenv("SOURCE_CHANNEL_ID", "-1002518840015"))
DESTINATION = int(os.environ["TARGET_CHAT_ID"])
AUTOFILTER = os.getenv("AUTOFILTER_USERNAME", "agsfilterV3_bot").lstrip("@")
ENABLED = os.getenv("DATABASE_UPLOAD_MODE", "false").lower() == "true"


async def initialize(sql):
    await sql("""CREATE TABLE IF NOT EXISTS upload_announcements (
        title_hash CHAR(64) PRIMARY KEY,
        movie_title VARCHAR(255) NOT NULL,
        source_message_id BIGINT NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'sending',
        destination_message_id BIGINT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")


def extract_title(message):
    media = message.document or message.video
    if not media:
        return None
    filename = getattr(media, "file_name", None)
    raw = filename or (message.caption or "").splitlines()[0] if (filename or message.caption) else ""
    raw = re.sub(r"\.(mkv|mp4|avi|mov|webm|m4v)$", "", raw.strip(), flags=re.I)
    raw = re.sub(r"[._]+", " ", raw)
    raw = re.split(
        r"\b(?:S\d{1,2}E\d{1,3}|S\d{1,2}|720p|1080p|2160p|480p|4K|HEVC|x264|x265|WEB[- ]?DL|BluRay|HDRip|Malayalam|Hindi|Tamil|Telugu|English)\b",
        raw, maxsplit=1, flags=re.I
    )[0]
    title = re.sub(r"\s+", " ", raw).strip(" -[]()")
    if not title or len(title) > 180 or not re.search(r"[A-Za-z0-9]", title):
        return None
    return title


def start_payload(title):
    # Telegram start payload: ASCII letters/digits/underscore/hyphen, max 64 chars.
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", title).strip("_")
    return ("search_" + slug[:57]) if slug else None


def register(app, sql):
    @app.on_message(filters.chat(SOURCE) & filters.channel & (filters.document | filters.video))
    async def announce(_, message):
        if not ENABLED:
            return
        title = extract_title(message)
        if not title:
            log.info("Source message %s has no parseable title; skipped", message.id)
            return
        payload = start_payload(title)
        if not payload:
            return
        key = hashlib.sha256(title.casefold().encode("utf-8")).hexdigest()
        # INSERT IGNORE atomically reserves title; uncertain sends stay reserved.
        reserved = await sql(
            "INSERT IGNORE INTO upload_announcements(title_hash,movie_title,source_message_id) VALUES(%s,%s,%s)",
            (key, title, message.id)
        )
        # INSERT IGNORE lastrowid can be 0 for duplicate rows.
        if not reserved:
            return
        url = "https://t.me/" + AUTOFILTER + "?start=" + quote(payload, safe="")
        text = ("🎬 <b>NEW MOVIE ADDED</b>\n\n"
                "<b>" + html.escape(title) + "</b>\n\n"
                "📥 Available in our movie database.")
        params = {
            "chat_id": DESTINATION,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": json.dumps({"inline_keyboard": [[{
                "text": "📥 Download Now", "url": url
            }]]})
        }
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                async with session.post(
                    "https://api.telegram.org/bot" + os.environ["BOT_TOKEN"] + "/sendMessage",
                    data=params
                ) as response:
                    result = await response.json(content_type=None)
            if not result.get("ok"):
                raise RuntimeError(str(result.get("description", "Telegram rejected message"))[:160])
            message_id = result["result"]["message_id"]
            await sql(
                "UPDATE upload_announcements SET status='published',destination_message_id=%s WHERE title_hash=%s",
                (message_id, key)
            )
            log.info("Announced upload %s as message %s", message.id, message_id)
        except Exception:
            log.exception("Delivery uncertain for upload %s; reservation retained", message.id)
