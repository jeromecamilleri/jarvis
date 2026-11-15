"""Détection déterministe (sans LLM) des commandes de mémoire personnelle."""
import re
import unicodedata
from dataclasses import dataclass

REMEMBER_RE = re.compile(
    r"^\s*(?:retiens|note|souviens-toi)\s+(?:que\s+|qu['’]\s*)(.+?)\s*$",
    re.I | re.S,
)
FORGET_RE = re.compile(
    r"^\s*oublie\s+(?:que\s+|qu['’]\s*)?(.+?)\s*$", re.I | re.S
)

_FACT = (
    r"^\s*(?:mon|ma|mes)\s+(?P<key>[^\W\d_][\w'’ -]*?)\s+(?:est|sont)\s+"
    r"(?P<marker>(?:maintenant|désormais|dorénavant)\s+)?(?P<value>.+?)\s*$"
)
FACT_RE = re.compile(_FACT, re.I | re.S)
# Questions simples portant sur une clé : « Quel est mon employeur ? »
QUESTION_RE = re.compile(
    r"^\s*(?:quel(?:le)?s?\s+(?:est|sont)|c['’]est\s+quoi|qu['’]est-ce\s+que\s+c['’]est)\s+"
    r"(?:mon|ma|mes)\s+(?P<key>[^\W\d_][\w'’ -]*?)\s*\??\s*$",
    re.I,
)
_POSSESSIVE_RE = re.compile(r"^\s*(?:mon|ma|mes)\s+(?P<key>.+?)\s*$", re.I | re.S)

MAX_KEY_WORDS = 5
_TRAILING = " \t\n.!;"


@dataclass(frozen=True)
class Command:
    action: str  # "remember" | "forget" | "recall"
    key: str
    value: str | None = None


def normalize_key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().strip(_TRAILING))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:80]


def _parse_fact(text: str) -> Command | None:
    m = FACT_RE.match(text)
    if not m or len(m.group("key").split()) > MAX_KEY_WORDS:
        return None
    key = normalize_key(m.group("key"))
    value = m.group("value").strip(_TRAILING)
    return Command("remember", key, value) if key and value else None


def parse_command(message: str) -> Command | None:
    """Retourne une commande si le message est une demande explicite, sinon None."""
    m = REMEMBER_RE.match(message)
    if m:
        fact = m.group(1).strip(_TRAILING)
        cmd = _parse_fact(fact)
        if cmd:
            return cmd
        # Phrase non structurable : clé dérivée de la phrase (compatible sprint 1).
        key = normalize_key(fact)
        return Command("remember", key, fact) if key else None

    m = FORGET_RE.match(message)
    if m:
        target = m.group(1).strip(_TRAILING)
        p = _POSSESSIVE_RE.match(target)
        key = normalize_key(p.group("key") if p else target)
        return Command("forget", key) if key else None

    m = QUESTION_RE.match(message)
    if m and len(m.group("key").split()) <= MAX_KEY_WORDS:
        key = normalize_key(m.group("key"))
        return Command("recall", key) if key else None
    return None


def is_personal_statement(message: str) -> bool:
    """Phrase du type « Mon employeur est X » donnée sans commande explicite."""
    return _parse_fact(message.strip()) is not None
