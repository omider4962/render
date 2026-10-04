"""SQLite storage for the panel's three fixed VLESS configuration IDs."""
import os
import aiosqlite
from datetime import datetime, timezone
from utils import new_uid

DB_PATH = os.environ.get("DB_PATH", "/data/omid3r.db")
try:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
except OSError:
    DB_PATH = "./omid3r.db"

_conn: aiosqlite.Connection | None = None


async def init_db():
    global _conn
    _conn = await aiosqlite.connect(DB_PATH)
    _conn.row_factory = aiosqlite.Row
    await _conn.execute("PRAGMA busy_timeout=5000")
    await _conn.execute("""
        CREATE TABLE IF NOT EXISTS fixed_configs (
            slot INTEGER PRIMARY KEY CHECK(slot BETWEEN 1 AND 3),
            uid TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    await _conn.commit()
    await ensure_configs()


async def ensure_configs():
    """Create slots once; migrate up to three previous VLESS UUIDs without deleting data."""
    async with _conn.execute("SELECT slot FROM fixed_configs") as cur:
        existing = {row[0] for row in await cur.fetchall()}
    legacy_uids = []
    if not existing:
        async with _conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'") as cur:
            has_old_users = await cur.fetchone()
        if has_old_users:
            async with _conn.execute("PRAGMA table_info(users)") as cur:
                columns = {row[1] for row in await cur.fetchall()}
            if {"uid", "protocol", "active"} <= columns:
                async with _conn.execute(
                    "SELECT uid FROM users WHERE active=1 AND lower(coalesce(protocol,'vless'))='vless' ORDER BY created_at LIMIT 3"
                ) as cur:
                    legacy_uids = [row[0] for row in await cur.fetchall()]
    now = datetime.now(timezone.utc).isoformat()
    for slot in range(1, 4):
        if slot in existing:
            continue
        uid = legacy_uids[slot - 1] if slot <= len(legacy_uids) else os.environ.get(f"VLESS_UUID_{slot}") or new_uid()
        await _conn.execute(
            "INSERT OR IGNORE INTO fixed_configs(slot,uid,created_at) VALUES(?,?,?)",
            (slot, uid, now),
        )
    await _conn.commit()


async def close_db():
    global _conn
    if _conn:
        await _conn.close()
        _conn = None


async def list_configs():
    async with _conn.execute("SELECT slot,uid,created_at FROM fixed_configs ORDER BY slot") as cur:
        return [dict(row) for row in await cur.fetchall()]


async def is_config(uid: str) -> bool:
    async with _conn.execute("SELECT 1 FROM fixed_configs WHERE uid=?", (uid,)) as cur:
        return await cur.fetchone() is not None
