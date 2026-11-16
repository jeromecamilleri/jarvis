"""Rappels et alarmes : parseur, service, redémarrage, notification, garde-fous."""
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.model_adapter import TEXT_AND_TOOLS, ModelResponse
from app.core.commands import REMINDER_CLAIM_CORRECTION, claims_reminder_set
from app.core.reminder_parser import parse_reminder
from app.core.reminders import AudioNotifier
from app.main import create_app

PARIS = ZoneInfo("Europe/Paris")
T0 = datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc)  # 17:00 à Paris


class Clock:
    def __init__(self, now=T0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


class Notifier:
    def __init__(self, error=None):
        self.spoken, self.error = [], error

    def notify(self, text):
        self.spoken.append(text)
        if self.error:
            raise RuntimeError(self.error)


class LLM:
    capabilities = TEXT_AND_TOOLS

    def __init__(self, reply="réponse de test"):
        self.calls, self.reply = [], reply

    def generate(self, request):
        self.calls.append(request.messages)
        return ModelResponse(self.reply)


def build(db_path, clock=None, notifier=None, llm=None):
    clock, notifier, llm = clock or Clock(), notifier or Notifier(), llm or LLM()
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=llm, clock=clock,
                     notifier=notifier, tz=PARIS)
    return app, clock, notifier, llm


@pytest.fixture
def env(db_path):
    app, clock, notifier, llm = build(db_path)
    client = TestClient(app)  # sans `with` : le planificateur n'est pas démarré
    return client, app, clock, notifier, llm


def say(client, message, cid=None):
    r = client.post("/chat", json={"message": message, "conversation_id": cid})
    assert r.status_code == 200
    return r.json()["reply"]


# --- parseur ---------------------------------------------------------------------------
@pytest.mark.parametrize("text, minutes, message", [
    ("Mets une alarme dans une minute.", 1, "Alarme"),
    ("Préviens-moi dans une minute.", 1, "Rappel"),
    ("Mets un minuteur de 10 minutes.", 10, "Minuteur terminé"),
    ("Rappelle-moi dans une heure de sortir le linge.", 60, "sortir le linge"),
    ("Dans 30 minutes, rappelle-moi de vérifier le serveur.", 30, "vérifier le serveur"),
    ("Rappelle-moi dans une heure et demie d'appeler Paul", 90, "appeler Paul"),
    ("Rappelle-moi dans une demi-heure de manger", 30, "manger"),
    ("Peux-tu mettre une alarme dans 5 minutes ?", 5, "Alarme"),
])
def test_relative_commands(text, minutes, message):
    cmd = parse_reminder(text, T0, PARIS)
    assert cmd.action == "create"
    assert cmd.due == T0 + timedelta(minutes=minutes)
    assert cmd.message == message


def test_absolute_commands_use_local_time_stored_as_utc():
    cmd = parse_reminder("Réveille-moi demain à 7 heures.", T0, PARIS)
    assert cmd.due.astimezone(timezone.utc) == datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)
    assert cmd.message == "Réveil"
    soir = parse_reminder("Rappelle-moi demain à 8 heures du soir de sortir", T0, PARIS)
    assert soir.due.astimezone(PARIS).hour == 20 and soir.message == "sortir"
    today = parse_reminder("Rappelle-moi aujourd'hui à 18h30 de partir", T0, PARIS)
    assert today.due.astimezone(PARIS).strftime("%H:%M") == "18:30"


@pytest.mark.parametrize("text", [
    "Mets une alarme",                                  # pas d'échéance
    "Rappelle-moi à 7 heures",                          # jour absent
    "Réveille-moi demain matin",                        # pas d'heure
    "Rappelle-moi aujourd'hui à 10 heures",             # déjà passée (il est 17 h)
    "Rappelle-moi dans 1h30 d'appeler",                 # précision que le parseur ne gère pas
    "Rappelle-moi dans 5 minutes et dans 10 minutes",   # deux échéances
    "Mets une alarme demain à 25 heures",               # heure invalide
    "Rappelle-moi dans 3 jours",                        # unité non prise en charge
    "Rappelle-moi dans 0 minute",
])
def test_ambiguous_commands_are_clarified_not_guessed(text):
    cmd = parse_reminder(text, T0, PARIS)
    assert cmd.action == "clarify" and cmd.due is None and "rien n'a été programmé" in cmd.reply


@pytest.mark.parametrize("text", [
    "Comment mettre une alarme sur mon téléphone ?",
    "Bonjour Jarvis, comment ça va ?",
    "Dans quelle ville est la tour Eiffel ?",
    "Mon employeur est maintenant Eviden",
])
def test_ordinary_messages_are_not_reminders(text):
    assert parse_reminder(text, T0, PARIS) is None


# --- création, liste, annulation -------------------------------------------------------
def test_create_reminder_is_deterministic_and_stored_in_utc(env):
    client, app, clock, notifier, llm = env
    reply = say(client, "Rappelle-moi dans une heure de sortir le linge.")
    assert "n°1" in reply and "sortir le linge" in reply and "18:00" in reply
    assert llm.calls == []
    row = app.state.reminder_store.get(1)
    assert row["status"] == "pending" and row["due_at"] == "2026-11-15T17:00:00Z"
    assert row["message"] == "sortir le linge" and row["fired_at"] is None


def test_list_pending_reminders(env):
    client, *_ = env
    assert "Aucun rappel" in say(client, "Quelles alarmes sont programmées ?")
    say(client, "Rappelle-moi dans une heure de sortir le linge.")
    say(client, "Réveille-moi demain à 7 heures.")
    reply = say(client, "Quelles alarmes sont programmées ?")
    assert "n°1" in reply and "sortir le linge" in reply and "n°2" in reply and "demain à 07:00" in reply
    assert [r["id"] for r in client.get("/reminders").json()] == [1, 2]


def test_cancel_single_reminder(env):
    client, app, *_ = env
    say(client, "Mets une alarme dans une minute.")
    assert "annulé" in say(client, "Annule mon rappel.")
    assert app.state.reminder_store.get(1)["status"] == "cancelled"
    assert "Aucun rappel" in say(client, "Annule mon rappel.")


def test_cancel_with_several_asks_which_one_then_by_number_or_all(env):
    client, app, *_ = env
    say(client, "Mets une alarme dans une minute.")
    say(client, "Rappelle-moi dans 5 minutes de boire.")
    reply = say(client, "Annule mon rappel.")
    assert "précise" in reply and len(app.state.reminder_store.pending()) == 2
    assert "n°2 annulé" in say(client, "Annule le rappel 2")
    assert [r["id"] for r in app.state.reminder_store.pending()] == [1]
    assert "Aucun rappel en attente" in say(client, "Annule le rappel 2")
    say(client, "Mets une alarme dans 3 minutes.")
    say(client, "Annule tous mes rappels")
    assert app.state.reminder_store.pending() == []


# --- déclenchement ---------------------------------------------------------------------
def test_fire_at_due_time_marks_fired_and_notifies(env):
    client, app, clock, notifier, _ = env
    say(client, "Rappelle-moi dans une heure de sortir le linge.")
    clock.advance(hours=1)
    assert app.state.reminders.fire(1) is True
    assert notifier.spoken == ["Rappel : sortir le linge"]
    row = app.state.reminder_store.get(1)
    assert row["status"] == "fired" and row["fired_at"] == "2026-11-15T17:00:00Z"


def test_default_message_is_spoken_without_prefix(env):
    client, app, _, notifier, _ = env
    say(client, "Mets une alarme dans une minute.")
    app.state.reminders.fire(1)
    assert notifier.spoken == ["Alarme"]


def test_reminder_fires_only_once_even_concurrently(env):
    client, app, _, notifier, _ = env
    say(client, "Mets une alarme dans une minute.")
    results = []
    threads = [threading.Thread(target=lambda: results.append(app.state.reminders.fire(1)))
               for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1 and len(notifier.spoken) == 1
    assert app.state.reminders.fire(1) is False


def test_cancelled_reminder_never_fires(env):
    client, app, _, notifier, _ = env
    say(client, "Mets une alarme dans une minute.")
    say(client, "Annule mon rappel.")
    assert app.state.reminders.fire(1) is False and notifier.spoken == []


def test_audio_failure_keeps_reminder_fired_visible_and_never_repeats(db_path):
    app, clock, notifier, _ = build(db_path, notifier=Notifier(error="pw-play indisponible"))
    client = TestClient(app)
    say(client, "Mets une alarme dans une minute.")
    assert app.state.reminders.fire(1) is True
    row = app.state.reminder_store.get(1)
    assert row["status"] == "fired" and "pw-play indisponible" in row["error"]
    assert app.state.reminders.fire(1) is False and len(notifier.spoken) == 1
    alerts = client.get("/reminders/alerts").json()
    assert [a["id"] for a in alerts] == [1] and "pw-play" in alerts[0]["error"]
    assert client.post("/reminders/1/seen").status_code == 200
    assert client.get("/reminders/alerts").json() == []


# --- redémarrage -----------------------------------------------------------------------
def test_restart_restores_future_reminders(db_path):
    # Horloge lointaine : APScheduler utilise l'heure réelle et ne doit pas rattraper l'échéance.
    base = T0 + timedelta(days=3650)
    app, *_ = build(db_path, clock=Clock(base))
    say(TestClient(app), "Rappelle-moi dans une heure de sortir le linge.")
    app2, *_ = build(db_path, clock=Clock(base + timedelta(minutes=10)))
    with TestClient(app2):
        job = app2.state.reminders.scheduler.get_job("reminder-1")
        assert job is not None and job.next_run_time.astimezone(timezone.utc) == base + timedelta(hours=1)
        assert app2.state.reminder_store.get(1)["status"] == "pending"


def test_reminders_due_during_downtime_become_missed_and_are_not_fired(db_path):
    base = T0 + timedelta(days=3650)  # futur lointain : insensible à l'horloge réelle (APScheduler)
    app, *_ = build(db_path, clock=Clock(base))
    client = TestClient(app)
    say(client, "Mets une alarme dans une minute.")
    say(client, "Rappelle-moi dans 3 heures de dîner.")
    notifier2 = Notifier()
    app2, *_ = build(db_path, clock=Clock(base + timedelta(hours=1)), notifier=notifier2)
    with TestClient(app2) as c2:
        store = app2.state.reminder_store
        assert store.get(1)["status"] == "missed" and store.get(2)["status"] == "pending"
        assert app2.state.reminders.scheduler.get_job("reminder-1") is None
        assert app2.state.reminders.scheduler.get_job("reminder-2") is not None
        alerts = c2.get("/reminders/alerts").json()
        assert [(a["id"], a["status"]) for a in alerts] == [(1, "missed")]
    assert notifier2.spoken == []
    app3, *_ = build(db_path, clock=Clock(base + timedelta(hours=1)))
    with TestClient(app3):  # un 2e redémarrage ne rejoue rien
        assert app3.state.reminder_store.get(1)["status"] == "missed"


# --- garde-fous face à Qwen ------------------------------------------------------------
@pytest.mark.parametrize("text", ["Mets une alarme", "Rappelle-moi à 7 heures", "Rappelle-moi dans 1h30 d'appeler"])
def test_ambiguous_request_creates_nothing_and_skips_the_llm(env, text):
    client, app, _, _, llm = env
    reply = say(client, text)
    assert "rien n'a été programmé" in reply and llm.calls == []
    assert app.state.reminder_store.pending() == []
    assert app.state.reminder_store.db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0


@pytest.mark.parametrize("claim", [
    "C'est fait, j'ai programmé une alarme pour demain 7 h.",
    "J'ai mis un rappel dans une heure.",
    "Ton rappel est programmé.",
    "Je te préviendrai dans dix minutes.",
    "Je vais te rappeler de sortir le linge.",
])
def test_llm_false_reminder_confirmation_is_corrected(db_path, claim):
    app, *_ = build(db_path, llm=LLM(claim))
    client = TestClient(app)
    reply = say(client, "Tu pourras penser à mon linge plus tard")
    assert claim in reply and REMINDER_CLAIM_CORRECTION.strip() in reply
    assert app.state.reminder_store.pending() == []


@pytest.mark.parametrize("honest", [
    "Je ne peux pas programmer de rappel moi-même ; dis « Rappelle-moi dans une heure de… ».",
    "Un rappel est un message programmé à une heure précise.",
    "Bonjour ! Comment puis-je vous aider ?",
])
def test_honest_replies_are_left_untouched(honest):
    assert not claims_reminder_set(honest)


def test_reminder_exchange_is_kept_apart_from_memories(env):
    client, app, _, _, llm = env
    cid = client.post("/chat", json={"message": "Rappelle-moi dans une heure de sortir le linge"}).json()["conversation_id"]
    say(client, "Une question ordinaire", cid)
    assert [m["content"] for m in llm.calls[0]] and all(
        "linge" not in m["content"] for m in llm.calls[0])  # l'échange de rappel n'est pas envoyé au LLM
    assert client.get("/profile").json() == {}
    assert app.state.episodic.search("linge", 5) == []
    assert len(client.get(f"/conversations/{cid}").json()["messages"]) == 4


# --- notification non bloquante --------------------------------------------------------
def test_slow_notification_does_not_block_other_requests(db_path):
    started, release = threading.Event(), threading.Event()

    class SlowNotifier:
        spoken = []

        def notify(self, text):
            self.spoken.append(text)
            started.set()
            release.wait(10)

    slow = SlowNotifier()
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=LLM(), notifier=slow, tz=PARIS)
    with TestClient(app) as client:  # horloge réelle : échéance dans 1 s
        assert "programmé" in say(client, "Mets une alarme dans 1 seconde.")
        assert started.wait(5), "le planificateur n'a pas déclenché le rappel"
        t0 = time.monotonic()
        assert client.get("/health").status_code == 200
        assert "réponse de test" in say(client, "Une autre question")
        assert time.monotonic() - t0 < 2
        release.set()
    assert slow.spoken == ["Alarme"]
    assert app.state.reminder_store.get(1)["status"] == "fired"


# --- lecture audio (sans matériel) -----------------------------------------------------
class FakeTTS:
    def synthesize(self, text):
        return b"RIFF"


def test_audio_notifier_reports_player_failures():
    AudioNotifier(FakeTTS(), "true").notify("Alarme")
    with pytest.raises(RuntimeError, match="échoué"):
        AudioNotifier(FakeTTS(), "false").notify("Alarme")
    with pytest.raises(RuntimeError, match="introuvable"):
        AudioNotifier(FakeTTS(), "lecteur-inexistant").notify("Alarme")
