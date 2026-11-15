"""Module Google Agenda avec réponses Google simulées (aucun compte, aucun réseau)."""
import inspect
import os
import stat

import pytest

import app.functions.calendar as cal
from app.config import Settings
from app.core.function_registry import READ, FunctionRegistry


class StrictEvents:
    """N'expose que `list` : tout appel d'écriture (insert, delete…) lève AttributeError."""
    def __init__(self, response, calls):
        self.response, self.calls = response, calls

    def list(self, **kwargs):
        self.calls.append(kwargs)
        response = self.response

        class Req:
            def execute(self_inner):
                if isinstance(response, Exception):
                    raise response
                return response
        return Req()


class StrictService:
    def __init__(self, response):
        self.calls = []
        self._events = StrictEvents(response, self.calls)

    def events(self):
        return self._events


def make(response, tmp_path=None):
    service = StrictService(response)
    calendar = cal.GoogleCalendar("/nonexistent/c.json", "/nonexistent/t.json", "Europe/Paris", lambda: service)
    return calendar, service


TIMED = {"summary": "Point Atos", "start": {"dateTime": "2026-11-16T14:00:00+01:00"},
         "end": {"dateTime": "2026-11-16T15:00:00+01:00"}, "location": "Salle 3", "description": "Ordre du jour"}


def test_timed_event_fields_and_query_parameters():
    calendar, service = make({"items": [TIMED]})
    out = calendar.list_events("2026-11-16T00:00:00", "2026-11-17T00:00:00")
    ev = out["events"][0]
    assert ev == {"title": "Point Atos", "all_day": False, "start": "2026-11-16T14:00:00+01:00",
                  "end": "2026-11-16T15:00:00+01:00", "recurring": False, "location": "Salle 3",
                  "description": "Ordre du jour"}
    call = service.calls[0]
    assert call["calendarId"] == "primary" and call["singleEvents"] is True
    assert call["orderBy"] == "startTime" and call["timeZone"] == "Europe/Paris"
    assert call["timeMin"] == "2026-11-16T00:00:00+01:00"


def test_all_day_event_has_inclusive_end():
    item = {"summary": "Congé", "start": {"date": "2026-11-16"}, "end": {"date": "2026-11-18"}}
    ev = make({"items": [item]})[0].list_events("2026-11-16", "2026-11-18")["events"][0]
    assert ev["all_day"] and ev["start"] == "2026-11-16" and ev["end"] == "2026-11-17"


def test_single_all_day_event_ends_same_day():
    item = {"summary": "Férié", "start": {"date": "2026-11-11"}, "end": {"date": "2026-11-12"}}
    ev = make({"items": [item]})[0].list_events("2026-11-11", "2026-11-11")["events"][0]
    assert ev["start"] == ev["end"] == "2026-11-11"


def test_recurring_event_is_flagged_and_expanded():
    items = [dict(TIMED, recurringEventId="abc", id="abc_1"),
             dict(TIMED, recurringEventId="abc", id="abc_2",
                  start={"dateTime": "2026-11-23T14:00:00+01:00"}, end={"dateTime": "2026-11-23T15:00:00+01:00"})]
    out = make({"items": items})[0].list_events("2026-11-16", "2026-11-30")
    assert out["count"] == 2 and all(e["recurring"] for e in out["events"])


def test_paris_timezone_conversion_and_naive_bounds():
    item = {"summary": "Appel US", "start": {"dateTime": "2026-11-16T08:00:00-05:00"},
            "end": {"dateTime": "2026-11-16T09:00:00-05:00"}}
    calendar, service = make({"items": [item]})
    ev = calendar.list_events("2026-07-01T00:00:00", "2026-07-02T00:00:00")  # heure d'été : +02:00
    assert service.calls[0]["timeMin"] == "2026-07-01T00:00:00+02:00"
    assert ev["events"][0]["start"] == "2026-11-16T14:00:00+01:00"
    utc_bound = calendar.list_events("2026-11-16T12:00:00Z", "2026-11-16T18:00:00Z")
    assert utc_bound["period"]["start"] == "2026-11-16T13:00:00+01:00"


def test_events_sorted_and_cancelled_skipped_and_untitled():
    items = [
        {"summary": "Tard", "start": {"dateTime": "2026-11-16T16:00:00+01:00"}, "end": {"dateTime": "2026-11-16T17:00:00+01:00"}},
        {"status": "cancelled", "summary": "Annulé", "start": {"dateTime": "2026-11-16T09:00:00+01:00"}, "end": {"dateTime": "2026-11-16T10:00:00+01:00"}},
        {"start": {"date": "2026-11-16"}, "end": {"date": "2026-11-17"}},
        {"summary": "Tôt", "start": {"dateTime": "2026-11-16T08:00:00+01:00"}, "end": {"dateTime": "2026-11-16T09:00:00+01:00"}},
    ]
    titles = [e["title"] for e in make({"items": items})[0].list_events("2026-11-16", "2026-11-16")["events"]]
    assert titles == ["(sans titre)", "Tôt", "Tard"]


def test_long_description_truncated_and_truncated_flag():
    out = make({"items": [dict(TIMED, description="x" * 5000)], "nextPageToken": "n"})[0].list_events(
        "2026-11-16", "2026-11-16")
    assert len(out["events"][0]["description"]) == cal.MAX_DESCRIPTION and out["truncated"] is True


@pytest.mark.parametrize("start, end, fragment", [
    ("demain", "2026-11-17", "ISO 8601"),
    ("2026-11-17T10:00:00", "2026-11-17T09:00:00", "postérieure"),
    ("2026-01-01", "2028-01-01", "un an"),
])
def test_invalid_periods_are_rejected_without_calling_google(start, end, fragment):
    calendar, service = make({"items": []})
    with pytest.raises(cal.FunctionError) as e:
        calendar.list_events(start, end)
    assert fragment in str(e.value) and service.calls == []


class HttpError(Exception):
    def __init__(self, status):
        self.resp = type("R", (), {"status": status})()
        super().__init__("secret-token-abc détail interne")


@pytest.mark.parametrize("error, fragment", [
    (HttpError(403), "refusé"), (HttpError(401), "refusé"), (HttpError(500), "HTTP 500"),
    (ConnectionError("boom"), "injoignable")])
def test_api_errors_are_mapped_without_leaking_details(error, fragment):
    with pytest.raises(cal.FunctionError) as e:
        make(error)[0].list_events("2026-11-16", "2026-11-16")
    assert fragment in str(e.value) and "secret-token" not in str(e.value)


# --- OAuth -----------------------------------------------------------------------------
def test_missing_oauth_configuration_gives_clear_message(tmp_path):
    calendar = cal.GoogleCalendar(str(tmp_path / "credentials.json"), str(tmp_path / "token.json"))
    with pytest.raises(cal.FunctionError, match="pas configuré"):
        calendar.list_events("2026-11-16", "2026-11-16")


def test_credentials_without_token_asks_for_authorization(tmp_path):
    (tmp_path / "credentials.json").write_text("{}")
    calendar = cal.GoogleCalendar(str(tmp_path / "credentials.json"), str(tmp_path / "token.json"))
    with pytest.raises(cal.FunctionError, match="python -m app.functions.calendar auth"):
        calendar.list_events("2026-11-16", "2026-11-16")


def test_unreadable_token_is_reported_and_permissions_tightened(tmp_path):
    token = tmp_path / "token.json"
    token.write_text("pas du json")
    token.chmod(0o644)
    calendar = cal.GoogleCalendar(str(tmp_path / "c.json"), str(token))
    with pytest.raises(cal.FunctionError, match="illisible"):
        calendar.list_events("2026-11-16", "2026-11-16")
    assert stat.S_IMODE(os.stat(token).st_mode) == 0o600


def test_refresh_failure_reports_expiry_without_secrets(tmp_path, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    token = tmp_path / "token.json"
    token.write_text("{}")

    class Dead:
        valid, expired, refresh_token = False, True, "r-secret"

        def refresh(self, request):
            raise RefreshError("invalid_grant r-secret")
    monkeypatch.setattr(Credentials, "from_authorized_user_file", classmethod(lambda c, p, s: Dead()))
    calendar = cal.GoogleCalendar(str(tmp_path / "c.json"), str(token))
    with pytest.raises(cal.FunctionError) as e:
        calendar.list_events("2026-11-16", "2026-11-16")
    assert "expiré" in str(e.value) and "r-secret" not in str(e.value)


def test_write_private_uses_restrictive_permissions(tmp_path):
    path = str(tmp_path / "sub" / "token.json")
    cal.write_private(path, "{}")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700


# --- lecture seule ---------------------------------------------------------------------
def test_scope_is_readonly_and_module_never_writes_to_calendar():
    assert cal.SCOPES == ["https://www.googleapis.com/auth/calendar.events.readonly"]
    source = inspect.getsource(cal)
    for verb in (".insert(", ".update(", ".patch(", ".delete(", ".quickAdd(", ".move(", ".import_("):
        assert verb not in source


def test_strict_service_proves_no_write_possible():
    service = StrictService({"items": []})
    for verb in ("insert", "delete", "update", "patch"):
        assert not hasattr(service.events(), verb)


def test_register_exposes_single_read_function():
    registry = FunctionRegistry()
    calendar = cal.register(registry, Settings(db_path=":memory:"), service_factory=lambda: StrictService(
        {"items": [TIMED]}))
    assert [s.name for s in registry.specs()] == ["calendar_list_events"]
    assert registry.get("calendar_list_events").level == READ
    assert registry.execute("calendar_list_events", {"start": "2026-11-16", "end": "2026-11-16"}).ok
    assert not registry.execute("calendar_list_events", {"start": "2026-11-16"}).ok
    assert isinstance(calendar, cal.GoogleCalendar)


def test_app_without_google_config_reports_it_clearly(tmp_path):
    registry = FunctionRegistry()
    cal.register(registry, Settings(db_path=":memory:", google_credentials_path=str(tmp_path / "a.json"),
                                    google_token_path=str(tmp_path / "t.json")))
    result = registry.execute("calendar_list_events", {"start": "2026-11-16", "end": "2026-11-16"})
    assert not result.ok and "pas configuré" in result.error
