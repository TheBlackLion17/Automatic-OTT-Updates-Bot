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

## Official announcement webpage verification

Automatic publishing now additionally requires `VERIFY_OFFICIAL_PAGE=true` (default). The bot fetches the configured official source URL (HTTPS, exact approved hostname, no redirects, HTML only, 750 KB limit), extracts visible text, and requires the full release title and explicit full release date to appear. A failed check leaves the release pending for manual review. Setting `VERIFY_OFFICIAL_PAGE=false` disables auto-publishing rather than bypassing this safety check.

This is a conservative text-matching safeguard, **not** independent semantic fact checking. A webpage may mention a title and date without confirming a premiere; pages requiring JavaScript may fail. A trusted structured JSON feed still must be operated/provided separately: the bot does not yet discover official feeds or generate them from platform websites. Do not enable unattended publishing without validating a real source. Run `venv/bin/python -m unittest -v test_official_discovery test_official_page_verifier`.

## Manual-review official discovery (recommended)

The periodic scanner and admin `/checknow` now import valid announcements from
`OFFICIAL_JSON_FEEDS` into the existing `releases` table with status
`pending`, **even when `AUTO_PUBLISH_VERIFIED=false`**. Admins review
with `/pending` and publish using `/approve ID` or discard with
`/reject ID`. Automatic publication is not used by this collector.

Configure in the VPS-only `.env`:

```dotenv
AUTO_PUBLISH_VERIFIED=false
VERIFY_OFFICIAL_PAGE=true
OFFICIAL_JSON_FEEDS=https://YOUR-TRUSTED-FEED-HOST/ott.json
TRUSTED_FEED_DOMAINS=YOUR-TRUSTED-FEED-HOST
OFFICIAL_SOURCE_DOMAINS=www.netflix.com,netflix.com
ALLOWED_PLATFORMS=Netflix,Prime Video,JioHotstar,SonyLIV,ZEE5
CHECK_INTERVAL_MINUTES=180
```

**Replace the placeholders with a real, operator-verified HTTPS JSON feed and
its exact hostname. No live feed is bundled or implied by these examples.**
The official webpage verifier checks that title and date appear on the page,
but cannot establish the meaning of that date. Human approval remains mandatory.
The existing `RSS_FEEDS` collector is separate: RSS headlines can enter the
pending queue without verified platform/date metadata.

Deployment: back up `.env`, pull `main`, run compilation and unit tests,
restart only `ott-updates`, and test `/checknow` and `/pending`.

## Official JioStar newsroom integration (opt-in)

The bot can now inspect the [JioStar newsroom](https://www.jiostar.com/newsroom/)
for headlines explicitly mentioning streaming or premieres on JioHotstar.
It queues qualifying official-domain links with `status=pending`, leaving
`release_date` and `language` unset because a headline alone cannot
establish either reliably. It does **not** scrape media files, infer dates,
or publish automatically. This collector is independent of structured JSON
feeds and is disabled by default until tested on your VPS.

In the VPS-only `.env`, set:

```dotenv
ENABLE_JIOSTAR_NEWSROOM=true
AUTO_PUBLISH_VERIFIED=false
CHECK_INTERVAL_MINUTES=180
```

Restart only `ott-updates`, send admin `/checknow`, then `/pending`.
Review each source and manually confirm its release date and language before
publication. Current `/approve ID` publishes the saved record as-is:
**do not approve undated newsroom candidates**. A future admin edit
command is needed for filling those fields. The newsroom may change its HTML,
and zero candidates is not proof that there are no releases.

Tests: `venv/bin/python -m unittest -v test_official_discovery test_official_page_verifier test_official_newsroom`.

## Admin review and safe publishing

Newsroom candidates now receive conservative title cleanup and extraction of
**explicitly mentioned** language names; no year or release date is inferred.
A candidate is still unverified until an admin checks the official article.

Commands (admin only):

- `/viewrelease ID` — show all saved fields and missing publication requirements.
- `/editrelease ID title | Correct title`
- `/editrelease ID language | Malayalam, Tamil`
- `/editrelease ID release_date | YYYY-MM-DD`
- `/editrelease ID platform | JioHotstar`
- `/editrelease ID source_url | https://www.jiostar.com/news/example/`
- `/editrelease ID summary | Confirmed official announcement`
- `/approve ID` — rejects missing/invalid title, platform, language,
  release date, or HTTPS source URL. A human must verify that the source
  actually confirms the date and language; URL syntax alone is not proof.

For existing pending JioStar records, back up the database and run
`venv/bin/python migrate_pending_newsroom.py` once. The migration preserves
IDs, source keys, status and dates, and only cleans titles / extracts explicit
languages. It can safely be rerun. Review any extracted languages because
newsroom text may mention languages in other contexts.

Run tests: `venv/bin/python -m unittest -v test_official_discovery test_official_page_verifier test_official_newsroom test_release_review`.

**Safety:** Leave `AUTO_PUBLISH_VERIFIED=false`. Manual approval validates
required fields, not the factual correctness of an announcement. Never enter
a year inferred from the current calendar or a headline lacking a year.


### Unattended verified publishing

Set `AUTO_PUBLISH_VERIFIED=true` **only** after configuring `OFFICIAL_JSON_FEEDS`, `TRUSTED_FEED_DOMAINS`, `OFFICIAL_SOURCE_DOMAINS`, and `ALLOWED_PLATFORMS`, and retain `VERIFY_OFFICIAL_PAGE=true`. Newly discovered structured announcements are published automatically only when the official page verification succeeds and the release has complete title, platform, language, date, and HTTPS source. RSS headlines and JioStar newsroom headlines remain pending for review because their release dates are not reliably verified. Existing pending or `sending` records are never automatically retried. If Telegram delivery is uncertain, the record stays `sending` to avoid duplicates. No automatic posts occur without a configured trusted structured feed.

## Opt-in unattended JioStar updates (November 2026)

A separate JioStar article verifier can automatically publish **new** newsroom announcements only when all of the following hold: the official HTTPS article explicitly says a title is streaming/premiering on JioHotstar from/on a full Month DD, YYYY date; the date is today or later; the title appears in the article; a language is explicitly present in the newsroom headline; and the article is an HTML response from the exact JioStar hostname without redirects. Ambiguous or incomplete announcements remain pending. This is strict text extraction, not semantic fact-checking; operators should monitor accuracy. Articles with a month/day but no year will not publish automatically.

Enable on the VPS after testing:
```dotenv
ENABLE_JIOSTAR_NEWSROOM=true
AUTO_PUBLISH_VERIFIED=true
VERIFY_OFFICIAL_PAGE=true
AUTO_PUBLISH_JIOSTAR=true
```

The new `AUTO_PUBLISH_JIOSTAR` flag defaults to **false**. Existing pending records are deliberately not auto-published or silently modified. Existing `sending` records are never retried automatically because Telegram may already have delivered the message. Other platforms still require a configured trusted structured feed. Run `venv/bin/python -m unittest -v test_official_article_dates` and review the channel before enabling unattended delivery.

## Operational improvements and safe rollout

The newsroom title cleaner removes known trailing article descriptions. Full-year JioStar streaming dates must be explicitly stated on the official article page; missing years and conflicting dates are not guessed.

Set RECHECK_PENDING_JIOSTAR=true in the VPS-only .env to opt in to periodic rechecking of existing pending JioHotstar records. Only records with an explicit or recorded language, a confirmed non-past date and a valid official article can publish. Existing sending, published and rejected records are never retried. Leave the flag false initially, inspect target permissions and investigate the unresolved sending record before enabling it. Enabling may publish eligible pending records immediately.

Admin commands: /stats reports database status totals, /failed lists uncertain sending records (not necessarily failed), and /autostatus checks settings and Telegram destination permissions without sending a test message. Logs record the Telegram message ID after successful API delivery, before database state update. Never reset sending records without checking the target channel for duplicates.

Run: venv/bin/python -m unittest discover -p 'test_*.py' -v. No destructive migration or bulk title changes are performed.
