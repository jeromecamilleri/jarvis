"""Compréhension déterministe (sans LLM) des commandes d'alarme et de rappel.

Formulations prises en charge : « dans N secondes/minutes/heures », « demain à HH heures »,
« aujourd'hui à HH heures ». Tout le reste est jugé ambigu : on demande une précision.
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

MAX_DELAY = timedelta(days=366)

_NUMBERS = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7,
    "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "quinze": 15, "vingt": 20,
    "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
}
_NUM = r"\d+|" + "|".join(sorted(_NUMBERS, key=len, reverse=True))
_SECONDS = {"s": 1, "sec": 1, "seconde": 1, "secondes": 1,
            "min": 60, "mn": 60, "minute": 60, "minutes": 60, "mins": 60,
            "h": 3600, "heure": 3600, "heures": 3600}

_TRIGGER = (
    r"(?:(?:mets|met|programme|r[ée]gle|cr[ée]e|lance|ajoute|configure)(?:[-\s]moi)?\s+"
    r"(?:(?:une?|l['’]|des?|d['’]une?)\s*)?(?:alarme|minuteur|minuterie|timer|chrono|rappel|r[ée]veil)"
    r"|pr[ée]viens[-\s]moi|rappelle[-\s]moi|r[ée]veille[-\s]moi)"
)
# Infinitifs : acceptés seulement après « peux-tu… ».
_TRIGGER_INF = (
    r"(?:(?:mettre|programmer|r[ée]gler|cr[ée]er|lancer)\s+(?:(?:une?|l['’])\s*)?"
    r"(?:alarme|minuteur|minuterie|timer|chrono|rappel|r[ée]veil)|(?:me\s+)?(?:rappeler|pr[ée]venir|r[ée]veiller))"
)
_TRIGGER_RE = re.compile(_TRIGGER + "|" + _TRIGGER_INF, re.I)
_POLITE = r"(?:peux|pourrais|veux)[-\s]tu\s+"
# Une vraie demande commence par l'impératif (éventuellement précédé d'une durée) :
# « Comment mettre une alarme sur mon téléphone ? » n'est pas une commande.
_REQUEST_RE = re.compile(
    r"^\s*(?:jarvis\W+)?(?:" + _POLITE + r"(?=" + _TRIGGER + "|" + _TRIGGER_INF + r")"
    r"|(?:(?:dans|demain|aujourd['’]hui)\b[^.?!]{0,40}?[,\s]\s*)?(?=" + _TRIGGER + r"))",
    re.I,
)
_TIMER_WORD_RE = re.compile(r"minuteur|minuterie|timer|chrono", re.I)

_KIND = r"(?:rappels?|alarmes?|minuteurs?|timers?|r[ée]veils?)"
_LIST_RE = re.compile(
    rf"^\s*(?:jarvis\W+)?(?:(?:peux|pourrais)[-\s]tu\s+)?"
    rf"(?:quel(?:le)?s\b[^?]*\b{_KIND}\b|(?:liste|montre|affiche|donne)\b[^?]*\b{_KIND}\b"
    rf"|(?:ai[-\s]je|est[-\s]ce\s+que\s+j['’]ai)\b[^?]*\b{_KIND}\b|mes\s+{_KIND}\s*[?.!]*)",
    re.I,
)
_CANCEL_RE = re.compile(
    rf"^\s*(?:jarvis\W+)?(?:(?:peux|pourrais)[-\s]tu\s+)?(?:annule|supprime|efface|retire|d[ée]sactive)\w*"
    rf"(?:[-\s]moi)?\s+(?P<det>mon|ma|mes|le|la|les|l['’]|tous\s+mes|toutes\s+mes|tous\s+les|toutes\s+les|"
    rf"ce|cette)?\s*{_KIND}\b(?P<rest>.*)$",
    re.I | re.S,
)

_REL_RE = re.compile(
    rf"\b(?P<prep>dans|de|pour)\s+(?P<n>{_NUM})\s*(?P<unit>secondes?|sec|minutes?|mins?|mn|heures?|h)\b",
    re.I,
)
_HALF_HOUR_RE = re.compile(r"\bdans\s+(?:une?\s+)?demi[-\s]heure\b", re.I)
_QUARTER_RE = re.compile(r"\bdans\s+un\s+quart\s+d['’]heure\b", re.I)
_ABS_RE = re.compile(
    r"\b(?P<day>demain|aujourd['’]hui)\b(?:\s+(?:matin|[àa]|vers))*\s*(?P<h>\d{1,2})\s*(?:heures?|h|:)"
    r"(?:\s*(?P<m>\d{2})\b)?(?P<ampm>\s+(?:du\s+(?:matin|soir)|de\s+l['’]apr[eè]s-midi))?",
    re.I,
)
# Ce qui suit une durée et la rendrait plus précise que ce qu'on a compris (« 1 heure 30 »).
_TRAILING_RE = re.compile(r"^\s*(?:et\s+)?(?:demie?\b|quart\b|\d)", re.I)
_AND_HALF_RE = re.compile(r"^\s*et\s+demie?\b", re.I)

DEFAULTS = {"alarm": "Alarme", "timer": "Minuteur terminé", "wake": "Réveil", "reminder": "Rappel"}

CLARIFY_TIME = (
    "Je n'ai pas pu déterminer l'échéance de façon fiable, donc rien n'a été programmé. "
    "Précise une durée (« dans 10 minutes ») ou un jour et une heure (« demain à 7 heures »)."
)


@dataclass(frozen=True)
class ReminderCommand:
    action: str  # "create" | "list" | "cancel" | "clarify"
    due: datetime | None = None
    message: str = ""
    target: int | str | None = None  # cancel : numéro, "all" ou None (« mon rappel »)
    reply: str = ""  # clarify : question à poser


def _clarify(text: str) -> ReminderCommand:
    return ReminderCommand("clarify", reply=text)


def _number(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBERS[token.lower()]


def _clean_message(text: str) -> str:
    text = re.sub(r"^[\s,;:.!?-]+|[\s,;:.!?-]+$", "", text)
    text = re.sub(r"^(?:de|d['’]|que|qu['’]|pour|à|a)\s*(?=\S)", "", text, flags=re.I)
    text = re.sub(r"^(?:l['’]|la |le )?(?:alarme|minuteur|rappel)\b[\s,;:.!?-]*$", "", text, flags=re.I)
    return re.sub(r"^[\s,;:.!?-]+|[\s,;:.!?-]+$", "", text)


def _round_up(due: datetime) -> datetime:
    return due.replace(microsecond=0) + timedelta(seconds=1) if due.microsecond else due


def _relative(text: str, now: datetime, timer: bool):
    """Retourne (échéance, début, fin) ou une ReminderCommand de clarification, ou None."""
    matches = []
    for m in _REL_RE.finditer(text):
        if m.group("prep").lower() == "dans" or (timer and m.group("prep").lower() in ("de", "pour")):
            matches.append(m)
    special = _HALF_HOUR_RE.search(text) or _QUARTER_RE.search(text)
    if len(matches) + (1 if special else 0) > 1:
        return _clarify(CLARIFY_TIME)
    if special:
        delta = timedelta(minutes=30 if "demi" in special.group(0).lower() else 15)
        return now + delta, special.start(), special.end()
    if not matches:
        return None
    m = matches[0]
    n = _number(m.group("n"))
    seconds = _SECONDS[m.group("unit").lower()]
    end = m.end()
    rest = text[end:]
    extra = 0
    if _AND_HALF_RE.match(rest) and seconds == 3600:
        extra = 1800
        end += _AND_HALF_RE.match(rest).end()
    elif _TRAILING_RE.match(rest):
        return _clarify(CLARIFY_TIME)
    total = timedelta(seconds=n * seconds + extra)
    if total <= timedelta(0) or total > MAX_DELAY:
        return _clarify(CLARIFY_TIME)
    return now + total, m.start(), end


def _absolute(text: str, now: datetime, tz: tzinfo):
    m = _ABS_RE.search(text)
    if not m:
        return None
    hour, minute = int(m.group("h")), int(m.group("m") or 0)
    ampm = (m.group("ampm") or "").lower()
    if ampm and ("soir" in ampm or "midi" in ampm) and 1 <= hour < 12:
        hour += 12
    if hour > 23 or minute > 59:
        return _clarify(CLARIFY_TIME)
    day = now.date() + timedelta(days=1 if m.group("day").lower() == "demain" else 0)
    due = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
    if due <= now:
        return _clarify("Cette heure est déjà passée, donc rien n'a été programmé. "
                        "Donne une heure à venir ou une durée (« dans 10 minutes »).")
    return due, m.start(), m.end()


def parse_reminder(message: str, now: datetime, tz: tzinfo) -> ReminderCommand | None:
    """None si le message n'est pas une commande d'alarme/rappel."""
    now = now.astimezone(tz)

    m = _CANCEL_RE.match(message)
    if m:
        rest = m.group("rest")
        number = re.search(r"\b(?:n[°o]?\s*|num[ée]ro\s*)?(\d+)\b", rest)
        det = re.sub(r"\s+", " ", (m.group("det") or "")).lower()
        if det.startswith(("tous", "toutes")) or re.search(r"\b(?:tous|toutes)\b", rest, re.I):
            return ReminderCommand("cancel", target="all")
        return ReminderCommand("cancel", target=int(number.group(1)) if number else None)

    if _LIST_RE.match(message):
        return ReminderCommand("list")

    if not _REQUEST_RE.match(message):
        return None

    trigger = _TRIGGER_RE.search(message)
    timer = bool(_TIMER_WORD_RE.search(trigger.group(0)))
    found = _relative(message, now, timer)
    absolute = _absolute(message, now, tz)
    if isinstance(found, ReminderCommand):
        return found
    if isinstance(absolute, ReminderCommand):
        return absolute
    if found and absolute:
        return _clarify(CLARIFY_TIME)
    found = found or absolute
    if not found:
        return _clarify(CLARIFY_TIME)
    due, start, end = found

    rest = (message[:start] + " " + message[end:])
    trig = _TRIGGER_RE.search(rest)
    if trig:
        rest = rest[:trig.start()] + " " + rest[trig.end():]
    rest = re.sub(r"^\s*(?:jarvis\W+)?(?:(?:peux|pourrais|veux)[-\s]tu\s+)?", "", rest, flags=re.I)
    text = _clean_message(re.sub(r"\s+", " ", rest))
    if not text:
        word = trigger.group(0).lower()
        kind = ("timer" if timer else "wake" if "veil" in word
                else "alarm" if "alarme" in word else "reminder")
        text = DEFAULTS[kind]
    return ReminderCommand("create", due=_round_up(due), message=text)
