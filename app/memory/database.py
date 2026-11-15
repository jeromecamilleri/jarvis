import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'chat',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Index dérivé de `messages` : peut être supprimé et reconstruit sans perte (backfill).
EPISODIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    first_message_id INTEGER NOT NULL REFERENCES messages(id),
    last_message_id INTEGER NOT NULL REFERENCES messages(id),
    part INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    text TEXT NOT NULL,
    display TEXT NOT NULL,
    embedding BLOB,
    UNIQUE (first_message_id, part)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, tokenize = 'unicode61 remove_diacritics 2'
);
"""

# Rappels : table indépendante des mémoires (profil, historique, index épisodique).
# due_at / created_at / fired_at : UTC, format fixe « YYYY-MM-DDTHH:MM:SSZ » (comparable en SQL).
REMINDERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'fired', 'cancelled', 'missed')),
    created_at TEXT NOT NULL,
    fired_at TEXT,
    seen INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_status_due ON reminders (status, due_at);
"""

BUSY_TIMEOUT_S = 30


class Database:
    """Une connexion SQLite par thread, même API que sqlite3.Connection pour les stores.

    Chaque connexion active les clés étrangères et attend jusqu'à BUSY_TIMEOUT_S un verrou
    d'écriture. Les stores valident chaque écriture avec commit() (transactions courtes).
    """

    def __init__(self, path: str):
        self.path = path
        self._local = threading.local()

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            self._local.conn = conn
        return conn

    def execute(self, *args):
        return self.conn.execute(*args)

    def executemany(self, *args):
        return self.conn.executemany(*args)

    def executescript(self, script: str):
        return self.conn.executescript(script)

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def close(self) -> None:
        """Ferme la connexion du thread courant."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def _migrate(db: Database) -> None:
    columns = [r["name"] for r in db.execute("PRAGMA table_info(messages)")]
    if "kind" not in columns:
        db.execute("ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'")
        db.commit()
    # Ancien index épisodique (sans extraits découpés) : données dérivées, reconstruites
    # par EpisodicMemory.backfill(). Les messages ne sont jamais touchés.
    chunk_columns = [r["name"] for r in db.execute("PRAGMA table_info(chunks)")]
    if chunk_columns and "display" not in chunk_columns:
        db.execute("DROP TABLE chunks")
        db.execute("DROP TABLE IF EXISTS chunks_fts")
        db.commit()


def connect(path: str) -> Database:
    db = Database(path)
    db.executescript(SCHEMA)
    _migrate(db)
    db.executescript(EPISODIC_SCHEMA)
    db.executescript(REMINDERS_SCHEMA)
    return db
