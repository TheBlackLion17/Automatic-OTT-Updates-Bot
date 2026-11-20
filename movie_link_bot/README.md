# Movie Link Bot (same repository, same MySQL database)

The existing OTT bot is unchanged. This second Telegram bot reads and writes its own
`movie_link_files` table inside the existing `ott_updates` database. It does **not**
use DR. DOOM or reuse another bot's file IDs.

## Requirements
- Create a *second* bot in BotFather. Add it as an administrator with access to
  the source channel (default `-1002518840015`).
- Run both bots on the VPS that can access the OTT MySQL database.
- Never reuse the OTT bot token or Pyrogram session name.
- Use the existing repo's `requirements.txt` and virtual environment.

## Configure
```bash
cd /root/ott-updates-bot
cp movie_link_bot/.env.example .env.movie_link
chmod 600 .env.movie_link
nano .env.movie_link
venv/bin/python -m py_compile movie_link_bot/bot.py
```
Use the same MYSQL_DATABASE as the OTT bot, and a MySQL user permitted to
CREATE, SELECT, INSERT and UPDATE the `movie_link_files` table. The sync
command also needs SELECT on `upload_announcements`. Do not commit tokens.

## Service
```bash
sudo cp movie_link_bot/ags-movie-link.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ags-movie-link.service
sudo journalctl -u ags-movie-link.service -n 40 --no-pager
```
Adjust paths in the service file if your checkout is elsewhere.

## Workflow
OTT upload -> announcement button -> `https://t.me/NEW_BOT?start=search_Movie_Title`
-> matching file buttons -> copy source-channel message into private chat.

Existing files: send `/sync` as an admin to import up to 500 known source
message IDs from the OTT `upload_announcements` table. This is **not** a
complete channel-history scan. New uploads are indexed automatically while
the new bot is running. Older media absent from announcement records needs
a separate authorized history import.

## Switch the OTT button only after testing
In the existing OTT bot's VPS-only `.env`, set
`AUTOFILTER_USERNAME=NEW_BOT_USERNAME` (without @), then restart
`ott-updates.service`. The existing `upload_announcements.py` already
reads this setting. This affects **future** announcements; existing Telegram
messages retain their old buttons.

Do not change the OTT `BOT_TOKEN` or its Pyrogram session.
