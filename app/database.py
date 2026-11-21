import os
import aiosqlite

DB_PATH = os.getenv("WEBHOOK_DB_PATH", "webhooks.db")


async def init_db(db_path: str | None = None):
    path = db_path or os.getenv("WEBHOOK_DB_PATH", DB_PATH)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                idempotency_key TEXT NOT NULL UNIQUE,
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                cleaned_text TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


async def save_event(
    idempotency_key: str,
    event_id: str,
    tenant_id: str,
    cleaned_text: str,
    fingerprint: str,
    
    db_path: str | None = None,
):
    path = db_path or os.getenv("WEBHOOK_DB_PATH", DB_PATH)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA busy_timeout=500;")
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO events (idempotency_key, event_id, tenant_id, cleaned_text, fingerprint, status)
            VALUES (?,?, ?, ?, ?, 'pending')
            """,
            (idempotency_key, event_id, tenant_id, cleaned_text, fingerprint),
        )
        await db.commit()
        return cursor.rowcount > 0


async def mark_delivered(event_id: str, db_path: str | None = None):
    path = db_path or os.getenv("WEBHOOK_DB_PATH", DB_PATH)
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA busy_timeout=500;")
        await db.execute(
            "UPDATE events SET status = 'delivered' WHERE event_id = ?",
            (event_id,),
        )
        await db.commit()
