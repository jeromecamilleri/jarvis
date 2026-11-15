"""Service de rappels : SQLite est la source de vérité, APScheduler ne fait que réveiller le service.

Aucun LLM n'intervient : création, annulation et déclenchement sont déterministes.
"""
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from tzlocal import get_localzone

from app.core.reminder_parser import DEFAULTS, ReminderCommand, parse_reminder
from app.memory.reminders import ReminderStore, from_ts

log = logging.getLogger("jarvis.reminders")
if not logging.getLogger("jarvis").handlers:  # uvicorn ne configure pas les journaux de l'application
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    logging.getLogger("jarvis").addHandler(_handler)
    logging.getLogger("jarvis").setLevel(logging.INFO)

ACCEPTED_FORMS = "« Annule le rappel 3 », « Annule tous mes rappels »"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AudioNotifier:
    """Lit le message avec Piper puis le lecteur audio de la session (pw-play par défaut)."""

    def __init__(self, tts, player: str = "pw-play", timeout: float = 60):
        self.tts, self.player, self.timeout = tts, player, timeout

    def notify(self, text: str) -> None:
        if shutil.which(self.player) is None:
            raise RuntimeError(f"lecteur audio introuvable : {self.player}")
        wav = self.tts.synthesize(text)
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="jarvis-reminder-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav)
            result = subprocess.run([self.player, path], capture_output=True, text=True,
                                    timeout=self.timeout)
            if result.returncode != 0:
                raise RuntimeError(f"{self.player} a échoué ({result.returncode}) : "
                                   f"{(result.stderr or result.stdout).strip()}")
        finally:
            os.unlink(path)


class ReminderService:
    def __init__(self, store: ReminderStore, notifier=None, clock: Callable[[], datetime] = utc_now,
                 tz=None):
        self.store, self.notifier, self.clock = store, notifier, clock
        self.tz = tz or get_localzone()
        self.scheduler = AsyncIOScheduler(timezone=timezone.utc)

    # --- cycle de vie -------------------------------------------------------------------
    def start(self) -> list[dict]:
        """Marque les rappels échus pendant l'arrêt `missed`, reprogramme les futurs. Retourne les manqués."""
        missed = self.store.mark_missed(self.clock())
        for r in missed:
            log.warning("Rappel manqué pendant l'arrêt de Jarvis : n°%s « %s » (prévu %s UTC)",
                        r["id"], r["message"], r["due_at"])
        self.scheduler.start()
        for r in self.store.pending():
            self._schedule(r["id"], from_ts(r["due_at"]))
        return missed

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def _schedule(self, reminder_id: int, due: datetime) -> None:
        if not self.scheduler.running:
            return
        self.scheduler.add_job(
            self.fire, DateTrigger(run_date=due, timezone=timezone.utc), args=[reminder_id],
            id=f"reminder-{reminder_id}", replace_existing=True, misfire_grace_time=None)

    def _unschedule(self, reminder_id: int) -> None:
        if self.scheduler.running and self.scheduler.get_job(f"reminder-{reminder_id}"):
            self.scheduler.remove_job(f"reminder-{reminder_id}")

    # --- opérations ---------------------------------------------------------------------
    def create(self, message: str, due: datetime) -> int:
        reminder_id = self.store.create(message, due, self.clock())
        self._schedule(reminder_id, due)
        return reminder_id

    def cancel(self, reminder_id: int) -> bool:
        ok = self.store.cancel(reminder_id)
        self._unschedule(reminder_id)
        return ok

    def fire(self, reminder_id: int) -> bool:
        """Déclenche à l'échéance. Le passage atomique pending -> fired garantit une seule notification,
        même si la lecture audio échoue (l'échec est enregistré, jamais rejoué)."""
        reminder = self.store.get(reminder_id)
        if reminder is None or not self.store.claim_fire(reminder_id, self.clock()):
            return False
        log.info("Rappel n°%s déclenché : %s", reminder_id, reminder["message"])
        try:
            if self.notifier is None:
                raise RuntimeError("aucun notifieur audio configuré")
            self.notifier.notify(self.spoken(reminder["message"]))
        except Exception as e:  # noqa: BLE001 - toute panne audio doit rester visible mais inoffensive
            log.error("Lecture audio du rappel n°%s impossible : %s", reminder_id, e)
            self.store.set_error(reminder_id, str(e))
        return True

    @staticmethod
    def spoken(message: str) -> str:
        return message if message in DEFAULTS.values() else f"Rappel : {message}"

    # --- commandes textuelles -----------------------------------------------------------
    def handle(self, message: str) -> str | None:
        """Réponse déterministe si `message` est une commande de rappel, sinon None."""
        cmd = parse_reminder(message, self.clock(), self.tz)
        if cmd is None:
            return None
        if cmd.action == "clarify":
            return cmd.reply
        if cmd.action == "list":
            return self._list_reply()
        if cmd.action == "cancel":
            return self._cancel_reply(cmd)
        reminder_id = self.create(cmd.message, cmd.due)
        return (f"Rappel n°{reminder_id} programmé {self.format_due(cmd.due)} : {cmd.message}. "
                "Je te préviendrai à voix haute à ce moment-là.")

    def format_due(self, due: datetime) -> str:
        now = self.clock().astimezone(self.tz)
        local = due.astimezone(self.tz)
        fmt = "%H:%M:%S" if due - now < timedelta(minutes=10) else "%H:%M"
        days = (local.date() - now.date()).days
        if days == 0:
            return f"aujourd'hui à {local.strftime(fmt)}"
        if days == 1:
            return f"demain à {local.strftime(fmt)}"
        return f"le {local.strftime('%d/%m/%Y')} à {local.strftime(fmt)}"

    def _line(self, r: dict) -> str:
        return f"- n°{r['id']} : {self.format_due(from_ts(r['due_at']))} — {r['message']}"

    def _list_reply(self) -> str:
        pending = self.store.pending()
        if not pending:
            return "Aucun rappel n'est programmé."
        return "Rappels programmés :\n" + "\n".join(self._line(r) for r in pending)

    def _cancel_reply(self, cmd: ReminderCommand) -> str:
        pending = self.store.pending()
        if cmd.target == "all":
            if not pending:
                return "Aucun rappel n'est programmé."
            for r in pending:
                self.cancel(r["id"])
            return f"{len(pending)} rappel(s) annulé(s)."
        if isinstance(cmd.target, int):
            return (f"Rappel n°{cmd.target} annulé." if self.cancel(cmd.target)
                    else f"Aucun rappel en attente n'a le numéro {cmd.target}.")
        if not pending:
            return "Aucun rappel n'est programmé."
        if len(pending) == 1:
            self.cancel(pending[0]["id"])
            return f"Rappel n°{pending[0]['id']} annulé : {pending[0]['message']}."
        return ("Plusieurs rappels sont programmés, précise lequel annuler :\n"
                + "\n".join(self._line(r) for r in pending) + f"\n{ACCEPTED_FORMS}.")
