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

## Trusted structured discovery and auto-publishing

Auto-publishing is **off by default**. Set `AUTO_PUBLISH_VERIFIED=true` only after configuring a trustworthy HTTPS JSON feed, its exact host in `TRUSTED_FEED_DOMAINS`, exact official platform hosts in `OFFICIAL_SOURCE_DOMAINS`, and platform names in `ALLOWED_PLATFORMS`. Example:

```dotenv
AUTO_PUBLISH_VERIFIED=true
OFFICIAL_JSON_FEEDS=https://updates.example.org/ott.json
TRUSTED_FEED_DOMAINS=updates.example.org
OFFICIAL_SOURCE_DOMAINS=www.netflix.com,netflix.com
ALLOWED_PLATFORMS=Netflix
```

**The example URL is a placeholder, not a live feed.** The source must return JSON like:

```json
{"releases":[{"title":"Example title","platform":"Netflix","language":"Malayalam","release_date":"2099-01-01","source_url":"https://www.netflix.com/title/example","trailer_url":"https://www.youtube.com/watch?v=example","summary":"Officially announced streaming release."}]}
```

The feed is only a transport: source trust and factual accuracy still depend on the operator maintaining the feed and the official announcement URL. The bot checks required fields, exact approved HTTPS domains, platform allowlists, non-past dates and duplicates; it **cannot independently prove that an official webpage actually confirms the release date**. Ordinary RSS headlines remain in the pending queue and are never auto-published. If no trusted JSON feed is configured, no automatic publishing occurs. No new MySQL tables are needed.

Run tests: `venv/bin/python -m unittest -v test_official_discovery`. Before deploying, verify the configured feed and destination chat. The `sending` status requires manual reconciliation if a Telegram send has an uncertain result.
