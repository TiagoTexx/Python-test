import os
import aiosqlite
import asyncio

DB_PATH = os.getenv("WEBHOOK_DB_PATH", "webhooks.db")
_db: aiosqlite.Connection | None = None
_db_write_lock = asyncio.Lock()
_pending_commits = 0
_commit_task: asyncio.Task | None = None

async def init_db(db_path: str | None = None):
    global _db
    path = db_path or os.getenv("WEBHOOK_DB_PATH", DB_PATH)
    _db = await aiosqlite.connect(path)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS events (
            idempotency_key TEXT NOT NULL UNIQUE,
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            cleaned_text TEXT NOT NULL,
            fingerprint TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id);")
    await _db.commit()


async def close_db():
    global _db, _pending_commits
    if _db is not None:
        if _pending_commits > 0:
            await _db.commit()
            _pending_commits = 0
        await _db.close()
        _db = None


async def _delayed_commit():
    global _pending_commits, _commit_task
    await asyncio.sleep(0.01)  # agrupa todas as escritas que chegarem numa janela de 10ms
    async with _db_write_lock:
        if _db is not None and _pending_commits > 0:
            await _db.commit()
            _pending_commits = 0
        _commit_task = None

async def save_event(idempotency_key, event_id, tenant_id, cleaned_text, fingerprint=None) -> bool:
    async with _db_write_lock:
        cursor = await _db.execute(
            "INSERT OR IGNORE INTO events "
            "(idempotency_key, event_id, tenant_id, cleaned_text, fingerprint, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (idempotency_key, event_id, tenant_id, cleaned_text, fingerprint),
        )
        await _db.commit()
        return cursor.rowcount > 0

async def mark_delivered(event_id: str, fingerprint: str) -> None:
    async with _db_write_lock:
        await _db.execute(
            "UPDATE events SET status = 'delivered', fingerprint = ? WHERE event_id = ?",
            (fingerprint, event_id),
        )
        await _db.commit()