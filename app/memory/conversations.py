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

    def add_message(self, conversation_id: int, role: str, content: str,
                    kind: str = "chat") -> int:
        cur = self.conn.execute(
            "INSERT INTO messages (conversation_id, role, content, kind) VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, kind),
        )
        self.conn.commit()
        return cur.lastrowid

    def chat_message_ids(self, conversation_id: int, limit: int | None = None) -> list[int]:
        """Identifiants des messages conversationnels (même fenêtre que celle envoyée au LLM)."""
        rows = self.conn.execute(
            "SELECT id FROM messages WHERE conversation_id = ? AND kind != 'command' ORDER BY id",
            (conversation_id,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        return ids[-limit:] if limit else ids

    def messages(self, conversation_id: int, limit: int | None = None,
                 include_commands: bool = True) -> list[dict]:
        """Messages dans l'ordre chronologique ; kind='command' = commandes de mémoire."""
        query = "SELECT role, content FROM messages WHERE conversation_id = ?"
        if not include_commands:
            query += " AND kind != 'command'"
        rows = self.conn.execute(query + " ORDER BY id", (conversation_id,)).fetchall()
        msgs = [dict(r) for r in rows]
        return msgs[-limit:] if limit else msgs
