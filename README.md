# Automatic OTT Updates Bot

Python / Pyrogram / MySQL Telegram bot for Movies_Hub_OG. RSS headlines are **unverified** and enter a manual approval queue. Official platform, release date, and trailer data must be verified by an admin; no automatic date inference.

## Install

```bash
sudo apt update && sudo apt install -y python3-venv mysql-server
sudo mysql -e "CREATE DATABASE IF NOT EXISTS ott_updates CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
# Create a least-privilege MySQL user with access to ott_updates, then:
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
# Fill in .env with bot token, API ID/hash, admin ID, chat ID and database credentials.
mysql -u ottbot -p ott_updates < schema.sql
venv/bin/python bot.py
```

When verified, install `ott-updates.service` under `/etc/systemd/system`, run `systemctl daemon-reload` and `systemctl enable --now ott-updates`.

## Commands

`/start`, `/latest`, `/upcoming`, `/today`; admins: `/checknow`, `/pending`, `/addrelease Title | Platform | Language | YYYY-MM-DD | https://trailer | Summary`, `/approve ID`, `/reject ID`.

Configure trusted HTTPS RSS URLs in `RSS_FEEDS` (comma-separated). Add the bot as an admin of the target channel/group with posting permission. Keep `.env` and session files out of Git.
