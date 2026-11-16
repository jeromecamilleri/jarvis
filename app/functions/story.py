"""Fonction `story_generate` : délégation explicite d'une continuation de texte au modèle expérimental mini-llm.

LIMITE À CONNAÎTRE : mini-llm est un petit GPT (17,8 M de paramètres) entraîné à CONTINUER des romans
français. Il ne suit aucune instruction, ne dialogue pas et ne garantit pas un récit cohérent. Il ne
reçoit donc jamais la demande de l'utilisateur, mais une AMORCE littéraire rédigée par Qwen, qu'il prolonge.

Garde-fous : le garde d'intention porte sur le message ORIGINAL de l'utilisateur (ordre direct de raconter
ou écrire une histoire) ; le modèle est choisi par le routeur sur la spécialité `text-continuation`, avec
`fallback="error"` : jamais de repli silencieux sur Qwen.
"""
import re
import unicodedata

from app.core.function_registry import READ, FunctionError, FunctionRegistry, FunctionSpec, Param
from app.core.model_adapter import LLMError, ModelRequest
from app.core.model_router import ModelRouter, RoutingRequest

SPECIALTY = "text-continuation"
MIN_PRIMER, MAX_PRIMER = 20, 800
DEFAULT_LENGTH, MIN_LENGTH, MAX_LENGTH = 120, 20, 200
NOTE = ("Expérience de génération : texte produit par un petit modèle de continuation (mini-llm), sans "
        "consigne ni garantie de cohérence ; il a prolongé l'amorce, il n'a pas suivi la demande.")

_FILLER = {"jarvis", "bonjour", "salut", "ok", "alors", "s", "il", "te", "plait", "stp", "svp", "please",
           "merci", "maintenant"}
_POLITE = (("peux", "tu"), ("pourrais", "tu"), ("tu", "peux"), ("tu", "pourrais"), ("veux", "tu", "bien"),
           ("voudrais", "tu"), ("est", "ce", "que", "tu", "peux"))
_IMPERATIVE = {"raconte", "ecris", "invente", "redige", "imagine"}
_INFINITIVE = {"raconter", "ecrire", "inventer", "rediger", "imaginer"}
_ARTICLES = {"moi", "me", "nous", "une", "un", "la", "le", "des", "cette", "ce", "quelque"}
_ADJECTIVES = {"petite", "courte", "belle", "longue", "jolie", "breve", "drole", "triste", "nouvelle"}
_NOUNS = {"histoire", "histoires", "recit", "recits", "conte", "contes", "fable", "legende", "nouvelle"}
_CONDITIONAL = re.compile(
    r"\b(si (besoin|necessaire|possible|tu veux|tu peux)|au cas ou|eventuellement|peut etre|quand tu veux)\b")
# Marqueurs qui rendent la demande conditionnelle ou différée quand ils OUVRENT une proposition (après le nom,
# ou après une virgule) : « … une histoire si tu en as envie », « …, quand tu auras le temps ».
_MARKERS = {("si",), ("quand",), ("lorsque",), ("lorsqu",), ("des", "que"), ("au", "cas"), ("a", "condition"),
            ("un", "jour"), ("plus", "tard"), ("un", "de", "ces", "jours"), ("eventuellement",), ("peut", "etre")}
_RETRACTION = re.compile(r"\b(non|ne|n|pas|annule|annuler|finalement|laisse tomber|oublie)\b")


def is_explicit_story_request(message: str) -> bool:
    """Vrai seulement pour un ordre direct de raconter/écrire une histoire (« Raconte-moi une histoire sur… »,
    « Peux-tu écrire un récit… ? »). Refus : questions sans formule de politesse, conditionnels, demandes
    précédées d'une négation ou d'une condition, et rétractation dans une phrase suivante. Le sujet de la
    première phrase est libre. Limite : une seconde demande sans négation dans une phrase suivante n'est pas détectée."""
    if not isinstance(message, str):
        return False
    text = "".join(c for c in unicodedata.normalize("NFKD", message.lower()) if not unicodedata.combining(c))
    text = text.replace("\u2019", " ").replace("'", " ")
    question = "?" in text
    if _CONDITIONAL.search(text):
        return False
    sentences = [s for s in re.split(r"[.!;?]+", text) if s.strip()]
    if not sentences:
        return False
    if any(_RETRACTION.search(re.sub(r"-", " ", s)) for s in sentences[1:]):
        return False
    words = re.findall(r"[a-z0-9]+", sentences[0].replace("-", " "))
    while words and words[0] in _FILLER:
        words.pop(0)
    polite = False
    for phrase in _POLITE:
        if tuple(words[:len(phrase)]) == phrase:
            words, polite = words[len(phrase):], True
            break
    if polite and words[:1] in (["me"], ["nous"]):
        words = words[1:]
    if question and not polite:
        return False
    if not words or words[0] not in (_INFINITIVE if polite else _IMPERATIVE):
        return False
    rest = words[1:]
    i = 0
    while i < len(rest) and rest[i] in _ARTICLES and rest[i] not in _NOUNS:
        i += 1
    while i < len(rest) and rest[i] in _ADJECTIVES and rest[i] not in _NOUNS:
        i += 1
    if i >= len(rest) or rest[i] not in _NOUNS:
        return False
    # Structure : le nom ne doit pas être suivi d'une condition, d'une envie ou d'un moment indéterminé, et
    # aucune proposition séparée par une virgule ne doit commencer par un tel marqueur. Une subordonnée
    # enfouie dans le sujet (« …sur un roi qui se demande si… ») reste acceptée ; en cas de doute, refus.
    if _starts_with_marker(rest[i + 1:]):
        return False
    clauses = [re.findall(r"[a-z0-9]+", c) for c in sentences[0].replace("-", " ").split(",")[1:]]
    return not any(_starts_with_marker(c) for c in clauses)


def _starts_with_marker(words: list[str]) -> bool:
    return any(tuple(words[:len(m)]) == m for m in _MARKERS)


def register(registry: FunctionRegistry, router: ModelRouter) -> None:
    """`router` doit utiliser `fallback="error"` : sans modèle `text-continuation`, la fonction échoue."""

    def story_generate(primer: str, length: int = DEFAULT_LENGTH) -> dict:
        primer = (primer or "").strip()
        if not MIN_PRIMER <= len(primer) <= MAX_PRIMER:
            raise FunctionError(f"L'amorce doit faire entre {MIN_PRIMER} et {MAX_PRIMER} caractères.")
        try:
            adapter = router.select(RoutingRequest(specialty=SPECIALTY)).model.adapter
            reply = adapter.generate(ModelRequest([{"role": "user", "content": primer}],
                                                  options={"max_tokens": length}))
        except LLMError as e:
            raise FunctionError(f"Génération impossible : {e}") from e
        return {"amorce": primer, "suite": reply.content, "note": NOTE}

    registry.register(FunctionSpec(
        name="story_generate",
        description=("Fait prolonger une AMORCE littéraire par un petit modèle expérimental de continuation de texte "
                     "(mini-llm). Ce modèle ne comprend AUCUNE instruction : il ne fait que poursuivre le texte "
                     "reçu. Trois cas distincts. (1) DEMANDE DIRECTE de création (« Raconte-moi une histoire "
                     "sur… », « Écris une histoire fantastique sur… ») : appelle cette fonction. (2) QUESTION sur "
                     "la capacité (« Sais-tu raconter des histoires ? », « Es-tu capable d'écrire une histoire ? ») : "
                     "réponds à la question, n'appelle PAS la fonction et ne raconte aucune histoire. (3) "
                     "INTERDICTION (« Ne me raconte pas d'histoire ») ou demande conditionnelle ou différée (« si "
                     "tu en as envie », « un jour », « quand tu auras le temps ») : n'appelle PAS la fonction, "
                     "respecte la demande et ne raconte pas d'histoire. Le paramètre `primer` n'est PAS la "
                     "demande de l'utilisateur : rédige toi-même, en français et au passé, un début de récit de "
                     "2 à 3 phrases inspiré du sujet demandé (ex. « Au cœur d'une forêt oubliée vivait un jeune "
                     "chevalier qui rêvait des étoiles. Un soir, une étrange lumière apparut au-dessus des "
                     "montagnes… »). Le résultat contient `amorce` et `suite` : présente-le comme une "
                     "expérimentation de génération, sans garantie de cohérence ; ne prétends jamais que le "
                     "modèle a suivi la demande ; tu peux le commenter. Si la fonction renvoie une erreur "
                     "(spécialiste indisponible), explique simplement que la génération spécialisée est "
                     "indisponible, sans prétendre avoir utilisé mini-llm. Un contrôle côté Jarvis refuse les "
                     "appels sans demande explicite."),
        params=(Param("primer", "string", f"Amorce littéraire de {MIN_PRIMER} à {MAX_PRIMER} caractères."),
                Param("length", "integer", f"Longueur de la suite en tokens ({MIN_LENGTH} à {MAX_LENGTH}).",
                      required=False, minimum=MIN_LENGTH, maximum=MAX_LENGTH)),
        handler=story_generate, level=READ, intent_guard=is_explicit_story_request,
        intent_refusal=("Génération NON exécutée, et ce n'est pas une panne : le message de l'utilisateur n'était pas "
                        "un ordre direct de raconter une histoire (question, négation, condition ou demande "
                        "différée). Réponds-lui naturellement, sans mentionner de fonction ni d'erreur, et sans "
                        "écrire d'histoire : réponds à sa question, ou respecte son refus, ou invite-le à "
                        "formuler une demande directe (« Raconte-moi une histoire sur… »)."),
        log_args=("length",)))
