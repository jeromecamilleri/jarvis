"""Détection simple (règles explicites) des questions qui renvoient au passé."""
import re
import unicodedata

_PATTERNS = [
    r"\b(?:avons|avions|avez|avait|avaient)[- ]nous\b",
    r"\bce que (?:nous|on) (?:avons|avions|a|avait)\b",
    r"\bqu'est-ce (?:que |qu')?(?:nous|on) (?:avons|avions|a|avait)\b",
    r"\b(?:te souviens|souviens[- ]tu|te rappelles|rappelle[- ]moi)\b",
    r"\b(?:j'avais|je t'avais|je t'ai) (?:dit|parle|mentionne|evoque)\b",
    r"\b(?:la |une )?semaine (?:derniere|passee)\b",
    r"\b(?:le )?mois (?:dernier|passe)\b",
    r"\bl'autre jour\b",
    r"\bhier\b",
    r"\bauparavant\b",
    r"\b(?:anciennes?|precedentes?) conversations?\b",
    r"\bconversations? (?:precedentes?|passees?)\b",
]
_RE = re.compile("|".join(_PATTERNS))


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    return "".join(c for c in text if not unicodedata.combining(c))


def is_past_reference(message: str) -> bool:
    return _RE.search(_fold(message)) is not None
