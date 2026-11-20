"""One-time, idempotent cleanup of pending JioStar newsroom candidates.

Does not invent dates, modify publication status, or touch other collectors.
Run from project directory with the virtualenv after backing up the database.
"""
import asyncio
import os
import aiomysql
from dotenv import load_dotenv
from release_review import clean_newsroom_title, extract_explicit_languages

async def main():
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
    conn = await aiomysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"], password=os.environ["MYSQL_PASSWORD"],
        db=os.getenv("MYSQL_DATABASE", "ott_updates"), autocommit=False,
        charset="utf8mb4")
    try:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT id,title,language FROM releases "
                "WHERE status='pending' AND platform='JioHotstar' "
                "AND source_url LIKE 'https://www.jiostar.com/news/%'")
            rows = await cur.fetchall()
            for row in rows:
                title = clean_newsroom_title(row["title"])
                language = row["language"] or extract_explicit_languages(row["title"])
                if title != row["title"] or language != row["language"]:
                    await cur.execute(
                        "UPDATE releases SET title=%s, language=%s "
                        "WHERE id=%s AND status='pending'",
                        (title, language, row["id"]))
            print(f"Reviewed {len(rows)} pending newsroom records; no release dates inferred.")
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(main())
