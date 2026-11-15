"""Google Agenda en lecture seule (API Calendar v3), exposé au LLM via `calendar_list_events`.

Sécurité : scope `calendar.events.readonly` uniquement, aucune méthode d'écriture n'est appelée.
Le jeton OAuth et `credentials.json` restent sur la machine (fichiers 0600) et ne sont jamais
transmis au LLM ni écrits dans les journaux.

Première autorisation (navigateur) :  python -m app.functions.calendar auth
Vérification :                        python -m app.functions.calendar check
"""
import json
import logging
import os
import stat
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.function_registry import READ, FunctionError, FunctionRegistry, FunctionSpec, Param

log = logging.getLogger("jarvis.calendar")

SCOPES = ["https://www.googleapis.com/auth/calendar.events.readonly"]
AUTH_COMMAND = "python -m app.functions.calendar auth"
MAX_SPAN = timedelta(days=366)
DEFAULT_RESULTS, MAX_RESULTS = 20, 30
MAX_DESCRIPTION, MAX_LOCATION = 300, 200

TRIGGER = r"\b(?:agenda|calendrier|rendez-?vous|rdv|r[ée]union)\b"
DESCRIPTION = (
    "Consulte (lecture seule) l'agenda Google principal de l'utilisateur et retourne ses événements "
    "entre deux instants, triés par date de début. À utiliser pour toute question sur ses rendez-vous, "
    "réunions ou disponibilités (aujourd'hui, demain, cette semaine, prochain rendez-vous…). "
    "Pour « mon prochain rendez-vous » : début = maintenant, fin = maintenant + 30 jours."
)


def _private(path: str) -> None:
    """Fichier lisible par le seul propriétaire (0600)."""
    if os.path.exists(path) and stat.S_IMODE(os.stat(path).st_mode) & 0o077:
        os.chmod(path, 0o600)


def write_private(path: str, content: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, mode=0o700, exist_ok=True)
        os.chmod(parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.chmod(path, 0o600)


class GoogleCalendar:
    def __init__(self, credentials_path: str, token_path: str, timezone: str = "Europe/Paris",
                 service_factory=None):
        self.credentials_path, self.token_path = credentials_path, token_path
        self.tz = ZoneInfo(timezone)
        self._service_factory = service_factory  # tests : service Google simulé

    # --- authentification --------------------------------------------------------------
    def _credentials(self):
        from google.auth.exceptions import RefreshError, TransportError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        if not os.path.exists(self.token_path):
            if not os.path.exists(self.credentials_path):
                raise FunctionError(
                    "Google Agenda n'est pas configuré : le fichier d'identifiants OAuth "
                    f"({self.credentials_path}) est absent. Voir la section Google Agenda du README.")
            raise FunctionError(
                "Google Agenda n'est pas encore autorisé. Lance une fois dans un terminal : "
                f"{AUTH_COMMAND}")
        _private(self.token_path)
        try:
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        except (ValueError, KeyError, OSError):
            raise FunctionError(f"Le jeton Google est illisible. Relance : {AUTH_COMMAND}") from None
        if creds.valid:
            return creds
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                raise FunctionError("L'autorisation Google a expiré ou a été révoquée. "
                                    f"Relance : {AUTH_COMMAND}") from None
            except TransportError:
                raise FunctionError("Google Agenda est injoignable (réseau).") from None
            write_private(self.token_path, creds.to_json())
            return creds
        raise FunctionError(f"L'autorisation Google n'est plus valide. Relance : {AUTH_COMMAND}")

    def _service(self):
        if self._service_factory:
            return self._service_factory()
        from googleapiclient.discovery import build
        # Un service par appel : les objets httplib2 ne sont pas thread-safe.
        return build("calendar", "v3", credentials=self._credentials(), cache_discovery=False)

    # --- fonction exposée au LLM -------------------------------------------------------
    def _parse_bound(self, name: str, value: str, end: bool = False) -> datetime:
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            raise FunctionError(
                f"{name} n'est pas une date ISO 8601 valide : « {value} » "
                "(exemple : 2026-11-16T00:00:00).") from None
        if len(value.strip()) == 10:  # date seule : la fin désigne la journée entière
            dt = datetime.combine(dt.date() + timedelta(days=1 if end else 0), datetime.min.time())
        return dt.replace(tzinfo=self.tz) if dt.tzinfo is None else dt.astimezone(self.tz)

    def list_events(self, start: str, end: str, max_results: int = DEFAULT_RESULTS) -> dict:
        t0, t1 = self._parse_bound("start", start), self._parse_bound("end", end, end=True)
        if t1 <= t0:
            raise FunctionError("La fin de la période doit être postérieure à son début.")
        if t1 - t0 > MAX_SPAN:
            raise FunctionError("La période demandée dépasse un an : demande une période plus courte.")
        service = self._service()
        try:
            response = service.events().list(
                calendarId="primary", timeMin=t0.isoformat(), timeMax=t1.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=max_results,
                timeZone=str(self.tz.key)).execute()
        except FunctionError:
            raise
        except Exception as e:  # noqa: BLE001 - erreurs réseau/API : jamais de détail sensible
            raise FunctionError(self._api_error(e)) from None
        events = sorted((self._format(e) for e in response.get("items", []) if e.get("status") != "cancelled"),
                        key=lambda ev: ev["_sort"])
        for ev in events:
            del ev["_sort"]
        return {"calendar": "primary", "timezone": str(self.tz.key),
                "period": {"start": t0.isoformat(), "end": t1.isoformat()},
                "count": len(events), "truncated": bool(response.get("nextPageToken")),
                "events": events}

    @staticmethod
    def _api_error(e: Exception) -> str:
        status = getattr(getattr(e, "resp", None), "status", None)
        log.warning("Erreur Google Agenda : %s (HTTP %s)", type(e).__name__, status)
        if status in (401, 403):
            return f"Google a refusé l'accès à l'agenda (HTTP {status}). Relance : {AUTH_COMMAND}"
        if status:
            return f"Google Agenda a répondu par une erreur (HTTP {status})."
        return "Google Agenda est injoignable pour le moment."

    def _format(self, item: dict) -> dict:
        start, end = item.get("start", {}), item.get("end", {})
        if "date" in start:  # journée entière : la date de fin de Google est exclusive
            first = date.fromisoformat(start["date"])
            last = max(date.fromisoformat(end["date"]) - timedelta(days=1), first) if "date" in end else first
            out = {"all_day": True, "start": first.isoformat(), "end": last.isoformat()}
            sort = datetime.combine(first, datetime.min.time(), self.tz)
        else:
            first = self._aware(start.get("dateTime", ""))
            last = self._aware(end["dateTime"]) if "dateTime" in end else first
            out = {"all_day": False, "start": first.isoformat(), "end": last.isoformat()}
            sort = first
        event = {"title": item.get("summary") or "(sans titre)", **out,
                 "recurring": bool(item.get("recurringEventId"))}
        if item.get("location"):
            event["location"] = item["location"][:MAX_LOCATION]
        if item.get("description"):
            event["description"] = item["description"][:MAX_DESCRIPTION]
        event["_sort"] = sort
        return event

    def _aware(self, value: str) -> datetime:
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=self.tz) if dt.tzinfo is None else dt.astimezone(self.tz)


def register(registry: FunctionRegistry, settings, service_factory=None) -> GoogleCalendar:
    calendar = GoogleCalendar(settings.google_credentials_path, settings.google_token_path,
                              settings.timezone, service_factory)
    registry.register(FunctionSpec(
        name="calendar_list_events",
        description=DESCRIPTION,
        params=(
            Param("start", "string", "Début de la période, ISO 8601 (ex. 2026-11-16T00:00:00, fuseau Europe/Paris)."),
            Param("end", "string", "Fin de la période, ISO 8601. Une date seule (2026-11-16) inclut toute la journée."),
            Param("max_results", "integer", f"Nombre maximal d'événements (défaut {DEFAULT_RESULTS}).",
                  required=False, minimum=1, maximum=MAX_RESULTS),
        ),
        handler=calendar.list_events, level=READ, trigger=TRIGGER,
        log_args=("start", "end", "max_results")))
    return calendar


def _cli(argv: list[str]) -> int:
    from app.config import get_settings
    settings = get_settings()
    command = argv[0] if argv else ""
    if command == "auth":
        from google_auth_oauthlib.flow import InstalledAppFlow
        if not os.path.exists(settings.google_credentials_path):
            print(f"Fichier introuvable : {settings.google_credentials_path}\n"
                  "Télécharge le JSON « application de bureau » depuis Google Cloud (voir README).")
            return 1
        _private(settings.google_credentials_path)
        flow = InstalledAppFlow.from_client_secrets_file(settings.google_credentials_path, SCOPES)
        creds = flow.run_local_server(port=0)
        write_private(settings.google_token_path, creds.to_json())
        print(f"Autorisation enregistrée ({settings.google_token_path}, droits 0600).")
        return 0
    if command == "check":
        calendar = GoogleCalendar(settings.google_credentials_path, settings.google_token_path,
                                  settings.timezone)
        now = datetime.now(calendar.tz)
        try:
            result = calendar.list_events(now.isoformat(), (now + timedelta(days=7)).isoformat())
        except FunctionError as e:
            print(f"Échec : {e}")
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print("Usage : python -m app.functions.calendar auth | check")
    return 2


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
