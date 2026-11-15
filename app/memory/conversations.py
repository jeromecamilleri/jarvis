import sqlite3


class ConversationStore:
    """Historique des échanges (distinct du profil personnel)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self) -> int:
        cur = self.conn.execute("INSERT INTO conversations DEFAULT VALUES")
        self.conn.commit()
        return cur.lastrowid

    def exists(self, conversation_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return row is not None

    def add_message(self, conversation_id: int, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, role, content),
        )
        self.conn.commit()

    def messages(self, conversation_id: int, limit: int | None = None) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
        msgs = [dict(r) for r in rows]
        return msgs[-limit:] if limit else msgs
