"""Budget de contexte : estimation prudente des tokens et troncature.

Aucun tokenizer n'est disponible hors d'Ollama : on suppose 2,5 caractères par token
(plus pessimiste que le français courant, ~3,5) et on ajoute un surcoût par message.
"""
import math

CHARS_PER_TOKEN = 2.5
MESSAGE_OVERHEAD = 8


def _body_tokens(text: str) -> float:
    """Lettres ~2,5 car./token ; chiffres, symboles et non-ASCII ~1 token chacun."""
    letters = sum(c.isalpha() and c.isascii() for c in text)
    spaces = sum(c.isspace() for c in text)
    others = len(text) - letters - spaces
    return letters / CHARS_PER_TOKEN + others + spaces / 4


def estimate_tokens(text: str) -> int:
    return math.ceil(_body_tokens(text)) + MESSAGE_OVERHEAD


def truncate_middle(text: str, max_tokens: int) -> str:
    """Garde le début et la fin d'un texte trop long, avec un marqueur explicite."""
    density = max(_body_tokens(text) / max(len(text), 1), 1 / CHARS_PER_TOKEN)
    max_chars = max(int((max_tokens - MESSAGE_OVERHEAD) / density), 0)
    if len(text) <= max_chars:
        return text
    marker = f"\n[… {len(text) - max_chars} caractères omis …]\n"
    keep = max(max_chars - len(marker), 0)
    head = int(keep * 0.6)
    tail = keep - head
    return text[:head] + marker + (text[len(text) - tail:] if tail else "")
