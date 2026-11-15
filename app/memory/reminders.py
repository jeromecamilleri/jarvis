"""Table `reminders` (SQLite = source de vérité des rappels)."""
from datetime import datetime, timezone

TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def to_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(TS_FORMAT)


def from_ts(value: str) -> datetime:
    return datetime.strptime(value, TS_FORMAT).replace(tzinfo=timezone.utc)


class ReminderStore:
    def __init__(self, db):
        self.db = db

    def create(self, message: str, due_at: datetime, now: datetime) -> int:
        cur = self.db.execute(
            "INSERT INTO reminders (message, due_at, created_at) VALUES (?, ?, ?)",
            (message, to_ts(due_at), to_ts(now)),
        )
        self.db.commit()
        return cur.lastrowid

    def get(self, reminder_id: int) -> dict | None:
        row = self.db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return dict(row) if row else None

    def pending(self) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM reminders WHERE status = 'pending' ORDER BY due_at, id").fetchall()
        return [dict(r) for r in rows]

    def cancel(self, reminder_id: int) -> bool:
        cur = self.db.execute(
            "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (reminder_id,))
        self.db.commit()
        return cur.rowcount == 1

    def claim_fire(self, reminder_id: int, now: datetime) -> bool:
        """Passe pending -> fired de façon atomique : un seul appelant obtient True."""
        cur = self.db.execute(
            "UPDATE reminders SET status = 'fired', fired_at = ? WHERE id = ? AND status = 'pending'",
            (to_ts(now), reminder_id))
        self.db.commit()
        return cur.rowcount == 1

    def mark_missed(self, now: datetime) -> list[dict]:
        """Rappels en attente dont l'échéance est passée : ils ne sont jamais déclenchés."""
        rows = self.db.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id",
            (to_ts(now),)).fetchall()
        missed = [dict(r) for r in rows]
        for r in missed:
            self.db.execute(
                "UPDATE reminders SET status = 'missed' WHERE id = ? AND status = 'pending'", (r["id"],))
        self.db.commit()
        return missed

    def set_error(self, reminder_id: int, error: str) -> None:
        self.db.execute("UPDATE reminders SET error = ? WHERE id = ?", (error[:500], reminder_id))
        self.db.commit()

    def alerts(self) -> list[dict]:
        """Rappels déclenchés ou manqués que l'interface n'a pas encore affichés."""
        rows = self.db.execute(
            "SELECT * FROM reminders WHERE status IN ('fired', 'missed') AND seen = 0 ORDER BY due_at, id"
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_seen(self, reminder_id: int) -> bool:
        cur = self.db.execute(
            "UPDATE reminders SET seen = 1 WHERE id = ? AND status IN ('fired', 'missed')",
            (reminder_id,))
        self.db.commit()
        return cur.rowcount == 1
