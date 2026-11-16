"""Traçabilité des exécutions : événements JSONL structurés, corrélés par `request_id`.

Module autonome (bibliothèque standard uniquement, aucun import de `app/`) : tous les composants peuvent
l'importer sans dépendance circulaire.

  - `request_scope()` pose un identifiant unique de requête dans un `ContextVar` et le réinitialise
    TOUJOURS à la sortie (succès ou exception) : aucun identifiant ne fuit vers une autre requête.
  - `emit()` ne lève jamais d'exception ; sans `configure()` (ou si l'écriture échoue), il ne fait rien.
  - `span()` mesure une durée avec `time.perf_counter()` (horloge monotone) et relance l'exception d'origine.
  - Un événement = une ligne JSON : ts, seq, request_id, event, component, status, duration_ms, + données.
    `seq` croît dans une requête (ordre fiable même à la même milliseconde) ; hors requête (planificateur),
    `request_id` est null et `seq` est un compteur global.

Confidentialité : jamais de message, prompt, amorce, réponse, résultat de fonction ni chemin de document.
Seuls des noms, tailles, compteurs, codes et durées sont enregistrés ; les messages d'erreur sont tronqués
(200 caractères) et les chemins absolus masqués.

Limite : un seul processus Uvicorn (la rotation du fichier n'est pas sûre avec plusieurs processus).

CLI (lecture des fichiers, rotations comprises) :
    python -m app.core.tracing show <request_id> [--path data/logs/trace.jsonl]
    python -m app.core.tracing last [-n 10]
"""
import argparse
import contextvars
import itertools
import json
import logging
import logging.handlers
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = "data/logs/trace.jsonl"
MAX_DETAIL = 200

_logger = logging.getLogger("jarvis.trace")
_logger.propagate = False
_handler: logging.Handler | None = None
_global_seq = itertools.count(1)
_PATH = re.compile(r"(?:/[\w.@+~-]+){2,}")


class _Request:
    __slots__ = ("id", "seq", "extra")

    def __init__(self):
        self.id, self.seq, self.extra = uuid.uuid4().hex[:12], itertools.count(1), {}


_request: contextvars.ContextVar[_Request | None] = contextvars.ContextVar("jarvis_request", default=None)
_function: contextvars.ContextVar[str | None] = contextvars.ContextVar("jarvis_function", default=None)


class _SafeHandler(logging.handlers.RotatingFileHandler):
    def handleError(self, record):  # noqa: N802 - une erreur d'écriture désactive les traces, sans bruit
        disable()
        logging.getLogger("jarvis").warning("Traçabilité désactivée : écriture impossible.")


# --- configuration -----------------------------------------------------------------------------
def configure(path: str = DEFAULT_PATH, max_bytes: int = 5_000_000, backups: int = 5) -> bool:
    """Active l'écriture JSONL avec rotation. Retourne False (traces désactivées) si le fichier est inutilisable."""
    global _handler
    disable()
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handler = _SafeHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - jamais bloquant
        logging.getLogger("jarvis").warning("Traçabilité indisponible (%s) : traces désactivées.", type(e).__name__)
        return False
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _handler = handler
    return True


def disable() -> None:
    global _handler
    handler, _handler = _handler, None
    if handler is not None:
        _logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001
            pass


def enabled() -> bool:
    return _handler is not None


# --- événements --------------------------------------------------------------------------------
def current_request_id() -> str | None:
    req = _request.get()
    return req.id if req else None


def safe_detail(value) -> str:
    """Texte d'erreur tronqué, sur une ligne, sans chemin absolu."""
    text = _PATH.sub("<chemin>", " ".join(str(value).split()))
    return text[:MAX_DETAIL]


def emit(event: str, component: str, status: str = "ok", duration_ms: float | None = None, **data) -> None:
    """Écrit un événement. Ne lève jamais d'exception."""
    try:
        if _handler is None:
            return
        req = _request.get()
        record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  "seq": next(req.seq) if req else next(_global_seq),
                  "request_id": req.id if req else None, "event": event, "component": component,
                  "status": status}
        if duration_ms is not None:
            record["duration_ms"] = round(duration_ms, 2)
        via = _function.get()
        if via and "via_function" not in data:
            record["via_function"] = via
        for key, value in data.items():
            if key not in record and value is not None:
                record[key] = value
        _logger.info(json.dumps(record, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 - la traçabilité ne doit jamais faire échouer une requête
        pass


@contextmanager
def span(prefix: str, component: str, **data):
    """Émet `<prefix>.started`, puis `.completed` ou `.failed` (avec durée). Le bloc peut compléter `info`.
    L'exception éventuelle est relancée telle quelle."""
    start = time.perf_counter()
    info: dict = {}
    emit(f"{prefix}.started", component, "started", **data)
    try:
        yield info
    except BaseException as e:
        emit(f"{prefix}.failed", component, "error", (time.perf_counter() - start) * 1000,
             **{**data, **info, "error": type(e).__name__, "detail": safe_detail(e)})
        raise
    emit(f"{prefix}.completed", component, "ok", (time.perf_counter() - start) * 1000, **{**data, **info})


@contextmanager
def request_scope(**data):
    """Une requête Jarvis : pose le request_id, trace request.started/completed/failed, réinitialise toujours.
    Imbriqué dans une requête existante, réutilise son identifiant sans rien émettre."""
    if _request.get() is not None:
        yield
        return
    req = _Request()
    token = _request.set(req)
    try:
        start = time.perf_counter()
        emit("request.started", "orchestrator", "started", **data)
        try:
            yield
        except BaseException as e:
            emit("request.failed", "orchestrator", "error", (time.perf_counter() - start) * 1000,
                 **{**data, **req.extra, "error": type(e).__name__, "detail": safe_detail(e)})
            raise
        emit("request.completed", "orchestrator", "ok", (time.perf_counter() - start) * 1000,
             **{**data, **req.extra})
    finally:
        _request.reset(token)


def annotate(**data) -> None:
    """Ajoute des données à l'événement de fin de la requête courante (sans effet hors requête)."""
    req = _request.get()
    if req is not None:
        req.extra.update(data)


@contextmanager
def function_scope(name: str):
    """Pendant l'exécution du handler d'une fonction, les événements portent `via_function`."""
    token = _function.set(name)
    try:
        yield
    finally:
        _function.reset(token)


class TracedAdapter:
    """Enveloppe transparente d'un `ModelAdapter` : mêmes capacités, mêmes résultats, mêmes exceptions.
    Trace `model.generation.*` avec model_id, role et adapter (nom de la classe). Les autres attributs sont délégués."""

    def __init__(self, adapter, model_id: str, role: str):
        self.adapter, self.model_id, self.role = adapter, model_id, role

    @property
    def capabilities(self):
        return self.adapter.capabilities

    def __getattr__(self, name):
        return getattr(self.adapter, name)

    def generate(self, request):
        with span("model.generation", "model", model_id=self.model_id, role=self.role,
                  adapter=type(self.adapter).__name__, **_request_shape(request)) as info:
            reply = self.adapter.generate(request)
            try:
                info.update(tool_calls=len(reply.tool_calls), reply_chars=len(reply.content))
            except Exception:  # noqa: BLE001 - jamais d'effet sur le résultat
                pass
            return reply


def _request_shape(request) -> dict:
    try:
        return {"messages": len(request.messages), "tools": len(request.tools or [])}
    except Exception:  # noqa: BLE001
        return {}


# --- lecture (CLI) -----------------------------------------------------------------------------
def read_events(path: str) -> list[dict]:
    """Événements du fichier et de ses rotations (`.N` le plus ancien d'abord), lignes illisibles ignorées."""
    base = Path(path)
    rotated = sorted((p for p in base.parent.glob(base.name + ".*") if p.suffix[1:].isdigit()),
                     key=lambda p: int(p.suffix[1:]), reverse=True)
    events = []
    for file in [*rotated, base]:
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


_SHOWN = ("model_id", "role", "adapter", "name", "via_function", "reason", "fallback", "specialty", "cause",
          "kind", "path", "tool", "error")


def format_timeline(events: list[dict]) -> str:
    events = sorted(events, key=lambda e: e.get("seq", 0))
    if not events:
        return "Aucun événement."
    t0 = datetime.fromisoformat(events[0]["ts"])
    rows = []
    for e in events:
        offset = (datetime.fromisoformat(e["ts"]) - t0).total_seconds() * 1000
        duration = f"{e['duration_ms']:.0f} ms" if "duration_ms" in e else ""
        details = " ".join(f"{k}={e[k]}" for k in _SHOWN if e.get(k) not in (None, ""))
        rows.append(f"+{offset:7.0f} ms  #{e.get('seq', '?'):<3} {e['event']:<28} {e['status']:<9} "
                    f"{duration:>9}  {details}".rstrip())
    return f"Requête {events[0].get('request_id')}\n" + "\n".join(rows)


def format_last(events: list[dict], count: int) -> str:
    requests: dict[str, dict] = {}
    for e in sorted(events, key=lambda e: (e["ts"], e.get("seq", 0))):
        rid = e.get("request_id")
        if not rid:
            continue
        r = requests.setdefault(rid, {"ts": e["ts"], "status": "en cours", "duration": "", "path": "",
                                      "models": [], "functions": []})
        if e["event"] in ("request.completed", "request.failed"):
            r["status"] = "ok" if e["event"] == "request.completed" else "erreur"
            r["duration"] = f"{e.get('duration_ms', 0):.0f} ms"
            r["path"] = e.get("path", "")
        elif e["event"] == "model.selected" and e.get("model_id") not in r["models"]:
            r["models"].append(e.get("model_id"))
        elif e["event"] == "function.called" and e.get("name") not in r["functions"]:
            r["functions"].append(e.get("name"))
    if not requests:
        return "Aucune requête."
    return "\n".join(
        f"{rid}  {r['ts'][:19]}  {r['status']:<8} {r['duration']:>9}  {r['path']:<8} "
        f"modèles={','.join(r['models']) or '-'} fonctions={','.join(r['functions']) or '-'}"
        for rid, r in list(requests.items())[-count:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.core.tracing", description="Lecture des traces Jarvis.")
    parser.add_argument("--path", default=os.getenv("JARVIS_TRACE_PATH", DEFAULT_PATH))
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="chronologie d'une requête")
    show.add_argument("request_id")
    last = sub.add_parser("last", help="dernières requêtes")
    last.add_argument("-n", type=int, default=10)
    args = parser.parse_args(argv)
    events = read_events(args.path)
    if args.command == "show":
        events = [e for e in events if str(e.get("request_id") or "").startswith(args.request_id)]
        print(format_timeline(events))
        return 0 if events else 1
    print(format_last(events, args.n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
