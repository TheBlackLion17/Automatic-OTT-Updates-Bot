"""MySQL-backed Telegram media indexing and private delivery."""
import asyncio
import html
import logging
import os
import re
from pyrogram import filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import RPCError

log = logging.getLogger("ott.files")
SOURCE_IDS = [int(x.strip()) for x in os.getenv("FILE_SOURCE_CHANNEL_IDS", os.getenv("SOURCE_CHANNEL_ID", "")).split(",") if x.strip()]
UPDATE_ID = int(os.getenv("FILE_UPDATE_CHAT_ID", os.getenv("TARGET_CHAT_ID", "0")))
LOG_ID = int(os.getenv("FILE_LOG_CHAT_ID", "0"))
FORCE_IDS = [int(x.strip()) for x in os.getenv("FILE_FORCE_CHANNEL_IDS", "").split(",") if x.strip()]
DELETE_SECONDS = max(0, int(os.getenv("FILE_DELETE_SECONDS", "300")))
ENABLED = os.getenv("FILE_INDEX_MODE", "false").lower() == "true"

async def initialize(sql):
    await sql("""CREATE TABLE IF NOT EXISTS indexed_media (
      id BIGINT AUTO_INCREMENT PRIMARY KEY,
      file_unique_id VARCHAR(255) NOT NULL UNIQUE,
      file_id TEXT NOT NULL,
      file_name VARCHAR(512) NOT NULL,
      file_size BIGINT NOT NULL DEFAULT 0,
      media_type VARCHAR(20) NOT NULL,
      source_chat_id BIGINT NOT NULL,
      source_message_id BIGINT NOT NULL,
      update_message_id BIGINT NULL,
      publish_state VARCHAR(20) NOT NULL DEFAULT 'pending',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      INDEX idx_filename(file_name(191)),
      UNIQUE KEY uniq_source_message(source_chat_id,source_message_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
    await sql("""CREATE TABLE IF NOT EXISTS file_bot_users (
      user_id BIGINT PRIMARY KEY, username VARCHAR(255), first_name VARCHAR(255),
      joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")

def media_from(message):
    for kind in ("document", "video", "audio", "photo"):
        value = getattr(message, kind, None)
        if value and getattr(value, "file_unique_id", None):
            return kind, value
    return None, None

async def register_user(app, sql, user):
    changed = await sql("INSERT IGNORE INTO file_bot_users(user_id,username,first_name) VALUES(%s,%s,%s)",
                        (user.id, user.username, user.first_name), affected=True)
    if changed and LOG_ID:
        try:
            await app.send_message(LOG_ID, "👤 New user registered\nID: <code>%s</code>\nName: %s\nUsername: %s" %
                (user.id, html.escape(user.first_name or ""), html.escape(user.username or "-")))
        except RPCError:
            log.exception("Unable to log new user")
    return bool(changed)

async def membership(app, user_id):
    missing = []
    for chat_id in FORCE_IDS:
        try:
            member = await app.get_chat_member(chat_id, user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED) or (
                member.status == ChatMemberStatus.RESTRICTED and not member.is_member):
                missing.append(chat_id)
        except RPCError:
            log.exception("Membership check failed for channel %s", chat_id)
            return None
    return missing

async def deliver(app, sql, message, user_id, file_id):
    rows = await sql("SELECT * FROM indexed_media WHERE id=%s", (file_id,), fetch=True)
    if not rows:
        return await message.reply_text("File not found.")
    missing = await membership(app, user_id)
    if missing is None:
        return await message.reply_text("Membership verification is temporarily unavailable. Try again later.")
    if missing:
        buttons = []
        for chat_id in missing:
            try:
                chat = await app.get_chat(chat_id)
                if chat.username:
                    url = "https://t.me/" + chat.username
                else:
                    url = getattr(chat, "invite_link", None)
                if url:
                    buttons.append([InlineKeyboardButton("Join " + (chat.title or "Channel")[:35], url=url)])
            except RPCError:
                log.exception("Unable to resolve required channel")
        buttons.append([InlineKeyboardButton("✅ Verify & Get File", callback_data="fileget:" + str(file_id))])
        return await message.reply_text("🔒 Join the required channels, then tap Verify.", reply_markup=InlineKeyboardMarkup(buttons))
    item = rows[0]
    try:
        # Copy from source rather than reuploading; Telegram permissions still apply.
        sent = await app.copy_message(user_id, item["source_chat_id"], item["source_message_id"])
        notice = await app.send_message(user_id, "✅ File delivered. Auto-delete in %s seconds." % DELETE_SECONDS)
    except RPCError:
        log.exception("File delivery failed for %s", file_id)
        return await message.reply_text("File unavailable or bot lacks source-channel access.")
    if DELETE_SECONDS:
        async def cleanup():
            await asyncio.sleep(DELETE_SECONDS)
            for msg in (sent, notice):
                try:
                    await msg.delete()
                except RPCError:
                    log.warning("Auto-delete failed for message %s", msg.id)
        asyncio.create_task(cleanup())

def register(app, sql, admins):
    if not ENABLED:
        return

    @app.on_message(filters.channel & (filters.document | filters.video | filters.audio | filters.photo))
    async def index_new(_, message):
        if message.chat.id not in SOURCE_IDS:
            return
        kind, media = media_from(message)
        if not media:
            return
        filename = getattr(media, "file_name", None) or (message.caption or "").split("\n")[0][:255] or ("Photo " + str(message.id))
        changed = await sql("""INSERT IGNORE INTO indexed_media
          (file_unique_id,file_id,file_name,file_size,media_type,source_chat_id,source_message_id)
          VALUES(%s,%s,%s,%s,%s,%s,%s)""",
          (media.file_unique_id, media.file_id, filename[:512], getattr(media, "file_size", 0) or 0,
           kind, message.chat.id, message.id), affected=True)
        if not changed:
            return
        rows = await sql("SELECT id FROM indexed_media WHERE file_unique_id=%s", (media.file_unique_id,), fetch=True)
        file_id = rows[0]["id"]
        me = await app.get_me()
        url = "https://t.me/%s?start=file_%s" % (me.username, file_id)
        text = "🆕 <b>NEW FILE ADDED</b>\n\n📁 %s\n📦 %.2f MB\n📂 %s" % (
            html.escape(filename[:512]), (getattr(media, "file_size", 0) or 0) / 1048576, kind)
        try:
            posted = await app.send_message(UPDATE_ID, text, reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📥 Get File in PM", url=url)]]))
            await sql("UPDATE indexed_media SET publish_state='published',update_message_id=%s WHERE id=%s",
                      (posted.id, file_id))
        except RPCError:
            log.exception("Update post uncertain for indexed file %s", file_id)
            await sql("UPDATE indexed_media SET publish_state='uncertain' WHERE id=%s", (file_id,))

    @app.on_message(filters.private & filters.command("start"), group=-1)
    async def file_start(_, message):
        if not message.from_user:
            return
        await register_user(app, sql, message.from_user)
        match = re.fullmatch(r"file_(\d+)", message.command[1]) if len(message.command) > 1 else None
        if match:
            await deliver(app, sql, message, message.from_user.id, int(match.group(1)))
            message.stop_propagation()

    @app.on_callback_query(filters.regex(r"^fileget:\d+$"))
    async def file_verify(_, query):
        await query.answer()
        if query.message and query.from_user:
            await deliver(app, sql, query.message, query.from_user.id, int(query.data.split(":")[1]))

    @app.on_message(filters.private & filters.command("files"))
    async def files(_, message):
        await register_user(app, sql, message.from_user)
        query = message.text.partition(" ")[2].strip()
        if not query:
            return await message.reply_text("Usage: /files search words")
        rows = await sql("SELECT id,file_name FROM indexed_media WHERE file_name LIKE %s ORDER BY id DESC LIMIT 10",
                         ("%" + query.replace("%", "\\%").replace("_", "\\_")[:120] + "%",), fetch=True)
        if not rows:
            return await message.reply_text("No matching files.")
        me = await app.get_me()
        buttons = [[InlineKeyboardButton(r["file_name"][:55], url="https://t.me/%s?start=file_%s" % (me.username, r["id"]))]
                   for r in rows]
        await message.reply_text("📁 Matching files:", reply_markup=InlineKeyboardMarkup(buttons))

    @app.on_message(filters.private & filters.command("filestats"))
    async def file_stats(_, message):
        if message.from_user.id not in admins:
            return
        files = await sql("SELECT COUNT(*) AS n FROM indexed_media", fetch=True)
        users = await sql("SELECT COUNT(*) AS n FROM file_bot_users", fetch=True)
        posted = await sql("SELECT COUNT(*) AS n FROM indexed_media WHERE publish_state='published'", fetch=True)
        await message.reply_text("📊 File bot\nFiles: %s\nUsers: %s\nPublished: %s" %
                                 (files[0]["n"], users[0]["n"], posted[0]["n"]))
