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


# « Que contient ta mémoire ? », « Que sais-tu de moi ? », « Qu'as-tu retenu sur moi ? »
# (appliqué au texte sans accents ni ponctuation, voir _fold).
LIST_RE = re.compile(
    r"^(?:que|qu est ce que|quoi)\s+(?:contient|content)\s+(?:ta|ma|ton|mon)\s+(?:memoire|profil)(?:\s+personnelle)?$"
    r"|^(?:que\s+sais\s+tu|qu est ce que tu sais|qu as tu\s+(?:retenu|memorise|note)"
    r"|qu est ce que tu as\s+(?:retenu|memorise|note))\s+(?:de|sur|a propos de)\s+moi$"
)
# Demandes de mémorisation formulées hors des trois commandes acceptées : jamais d'écriture.
_INTENT_START = (
    r"(?:(?:jarvis\s+)?(?:peux tu|pourrais tu|veux tu|s il te plait)\s+)*"
    r"(?:enregistre|enregistrer|en registre|un registre|memorise|memoriser|rappelle toi|n oublie pas|"
    r"garde en memoire|sauvegarde|retiens|retenir|souviens toi|ajoute a ta memoire)\b"
)
INTENT_RE = re.compile(
    rf"^{_INTENT_START}|\b(?:enregistre|enregistrer|memorise|memoriser|retenir|sauvegarde|ajoute)\b.*\bta memoire\b"
)
# Qwen affirme avoir écrit dans la mémoire (le profil n'est modifié que par une commande).
CLAIM_RE = re.compile(
    r"\b(?:je l ai|je les ai|j ai)\s+(?:bien\s+|deja\s+)?(?:enregistre|memorise|note|retenu|sauvegarde|ajoute|"
    r"mis a jour|modifie|oublie|supprime|efface)"
    r"|\bje (?:note|retiens|memorise|enregistre|sauvegarde)\b"
    r"|\bje (?:vais|viens de)\s+(?:le |la |l )?(?:noter|retenir|enregistrer|memoriser|sauvegarder|mettre a jour)"
    r"|\b(?:c est|est|a ete|ont ete)\s+(?:bien\s+|desormais\s+)?(?:note|notee|enregistre|enregistree|memorise|memorisee|"
    r"sauvegarde|sauvegardee)\b"
    r"|\bje m en souviendrai\b|\bbien (?:note|enregistre|memorise)\b"
)
# Qwen affirme avoir programmé une alarme ou un rappel (seul le service de rappels le fait).
REMINDER_CLAIM_RE = re.compile(
    r"\b(?:j ai|je l ai|je viens de)\s+(?:bien\s+|deja\s+)?(?:programme|regle|cree|mis|configure|ajoute|enregistre|lance)"
    r"\s+(?:une?|le|la|l|ton|votre|un)?\s*(?:alarme|rappel|minuteur|timer|reveil)"
    r"|\b(?:alarme|rappel|minuteur|timer|reveil)\s+(?:est\s+|a ete\s+)?(?:bien\s+)?(?:programme|regle|cree|configure|enregistre|lance)e?\b"
    r"|\bje (?:te|vous) (?:rappellerai|previendrai|reveillerai|previens)\b"
    r"|\bje (?:te|vous) (?:rappelle|previens|reveille) (?:dans|demain|a)\b"
    r"|\bje (?:vais|viens de) (?:te|vous) (?:rappeler|prevenir|reveiller)\b"
)


@dataclass(frozen=True)
class Command:
    action: str  # "remember" | "forget" | "recall" | "list" | "memory_intent"
    key: str
    value: str | None = None


def normalize_key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().strip(_TRAILING))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:80]


def _fold(text: str) -> str:
    """Minuscules, sans accents ni ponctuation : « Qu'as-tu retenu ? » -> « qu as tu retenu »."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


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

    folded = _fold(message)
    if LIST_RE.match(folded):
        return Command("list", "")
    if INTENT_RE.search(folded):
        return Command("memory_intent", "")
    return None


def claims_memory_write(reply: str) -> bool:
    """Vrai si la réponse du LLM affirme avoir enregistré, modifié ou oublié une information."""
    return CLAIM_RE.search(_fold(reply)) is not None


def claims_reminder_set(reply: str) -> bool:
    """Vrai si la réponse du LLM affirme avoir programmé une alarme ou un rappel."""
    return REMINDER_CLAIM_RE.search(_fold(reply)) is not None


def is_personal_statement(message: str) -> bool:
    """Phrase du type « Mon employeur est X » donnée sans commande explicite."""
    return _parse_fact(message.strip()) is not None


NOT_STORED_NOTICE = (
    "\n\n(Rien n'a été enregistré dans ta mémoire personnelle. "
    "Pour l'enregistrer, écris « Retiens que… ».)"
)

MEMORY_INTENT_REPLY = (
    "Rien n'a été enregistré dans ta mémoire personnelle. Pour enregistrer une information, "
    "dis ou écris « Retiens que… », « Note que… » ou « Souviens-toi que… »."
)

MEMORY_CLAIM_CORRECTION = (
    "\n\nCorrection : rien n'a été enregistré, modifié ni supprimé dans ta mémoire personnelle. "
    "Seules les commandes « Retiens que… », « Note que… », « Souviens-toi que… » et « Oublie… » "
    "modifient le profil."
)

REMINDER_CLAIM_CORRECTION = (
    "\n\nCorrection : aucun rappel ni aucune alarme n'a été programmé par cette réponse. "
    "Pour en créer un, dis par exemple « Rappelle-moi dans 10 minutes de… » ou « Mets une alarme dans une minute »."
)
