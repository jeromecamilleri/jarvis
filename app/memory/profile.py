import sqlite3


class ProfileStore:
    """Informations personnelles stables, enregistrées explicitement."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO profile (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = CURRENT_TIMESTAMP",
            (key, value),
        )
        self.conn.commit()

    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM profile WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def delete(self, key: str) -> bool:
        cur = self.conn.execute("DELETE FROM profile WHERE key = ?", (key,))
        self.conn.commit()
        return cur.rowcount > 0

    def all(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT key, value FROM profile ORDER BY key")
        return {r["key"]: r["value"] for r in rows}
