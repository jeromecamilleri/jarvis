"""Registre central des fonctions que Jarvis peut exécuter à la demande du LLM.

Contrat commun d'une fonction :
  - un nom unique, une description lisible par le LLM, des paramètres typés (`Param`) ;
  - un niveau d'autorisation : `read` (consultation), `safe_write` (écriture sans gravité),
    `sensitive` (confirmation explicite requise) ;
  - un `handler` Python `handler(**params) -> dict | list | str | int | float | bool | None`
    (résultat sérialisable en JSON) qui lève `FunctionError(message)` pour une erreur attendue
    (non configuré, refus de l'API…) : le message est transmis au LLM tel quel.

Le LLM ne fait que *demander* l'exécution : `execute()` vérifie le nom, valide les paramètres et
le niveau d'autorisation, puis appelle le handler. Les fonctions sont enregistrées explicitement
au démarrage (`app/main.py`) ; il n'y a ni chargement dynamique ni découverte automatique.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("jarvis.functions")

READ, SAFE_WRITE, SENSITIVE = "read", "safe_write", "sensitive"
LEVELS = (READ, SAFE_WRITE, SENSITIVE)
_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool}


class FunctionError(Exception):
    """Erreur attendue d'une fonction ; le message est destiné au LLM (et à l'utilisateur)."""


@dataclass(frozen=True)
class Param:
    name: str
    type: str  # "string" | "integer" | "number" | "boolean"
    description: str
    required: bool = True
    enum: tuple | None = None
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self):
        if self.type not in _TYPES:
            raise ValueError(f"type de paramètre inconnu : {self.type}")


@dataclass(frozen=True)
class FunctionSpec:
    name: str
    description: str
    handler: Callable[..., Any]
    params: tuple[Param, ...] = ()
    level: str = READ
    # Motif (regex, insensible à la casse) d'un message utilisateur qui exige cette fonction.
    # Sert uniquement de garde-fou si le LLM répond sans l'appeler.
    trigger: str | None = None
    # Rôle documentaire : "search" (trouve des fichiers) ou "read" (lit un passage). Permet à
    # l'orchestrateur de vérifier les réponses documentaires sans connaître les noms des fonctions.
    document: str | None = None
    # Paramètres qui peuvent figurer dans le journal (jamais le contenu des résultats).
    log_args: tuple[str, ...] = ()
    # Garde d'intention : reçoit le message ORIGINAL de l'utilisateur et doit retourner True pour
    # autoriser l'exécution. Sans message (ou garde faux), la fonction est refusée avant son handler.
    intent_guard: Callable[[str], bool] | None = None
    intent_refusal: str = ""

    def __post_init__(self):
        if self.level not in LEVELS:
            raise ValueError(f"niveau d'autorisation inconnu : {self.level}")
        if self.document not in (None, "search", "read"):
            raise ValueError(f"rôle documentaire inconnu : {self.document}")

    def to_ollama_tool(self) -> dict:
        properties = {}
        for p in self.params:
            prop: dict = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = list(p.enum)
            properties[p.name] = prop
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": properties,
                           "required": [p.name for p in self.params if p.required]}}}


@dataclass
class FunctionResult:
    ok: bool
    data: Any = None
    error: str = ""
    name: str = ""
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        payload = {"ok": True, "result": self.data} if self.ok else {"ok": False, "error": self.error}
        return json.dumps(payload, ensure_ascii=False, default=str)


class FunctionRegistry:
    def __init__(self, allowed_levels: tuple[str, ...] = (READ, SAFE_WRITE)):
        # `sensitive` n'est jamais exécutable sur simple demande du LLM : il faudrait une confirmation.
        self.allowed_levels = allowed_levels
        self._functions: dict[str, FunctionSpec] = {}

    def register(self, spec: FunctionSpec) -> None:
        if spec.name in self._functions:
            raise ValueError(f"fonction déjà enregistrée : {spec.name}")
        self._functions[spec.name] = spec

    def get(self, name: str) -> FunctionSpec | None:
        return self._functions.get(name)

    def specs(self) -> list[FunctionSpec]:
        return list(self._functions.values())

    def __len__(self) -> int:
        return len(self._functions)

    def tools(self) -> list[dict]:
        """Descriptions au format outils d'Ollama."""
        return [s.to_ollama_tool() for s in self._functions.values()]

    def validate(self, spec: FunctionSpec, arguments: Any) -> dict:
        """Retourne les paramètres validés ; lève FunctionError sinon."""
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise FunctionError("Les paramètres doivent être un objet JSON.")
        known = {p.name: p for p in spec.params}
        unknown = sorted(set(arguments) - set(known))
        if unknown:
            raise FunctionError(f"Paramètres inconnus : {', '.join(unknown)}.")
        clean = {}
        for p in spec.params:
            if p.name not in arguments or arguments[p.name] is None:
                if p.required:
                    raise FunctionError(f"Paramètre obligatoire manquant : {p.name}.")
                continue
            clean[p.name] = self._coerce(p, arguments[p.name])
        return clean

    @staticmethod
    def _coerce(p: Param, value: Any) -> Any:
        if p.type in ("integer", "number") and isinstance(value, str):
            try:  # les petits modèles envoient parfois « 5 » au lieu de 5
                value = int(value) if p.type == "integer" else float(value)
            except ValueError:
                pass
        if isinstance(value, bool) != (p.type == "boolean"):
            raise FunctionError(f"Le paramètre {p.name} doit être de type {p.type}.")
        if not isinstance(value, _TYPES[p.type]):
            raise FunctionError(f"Le paramètre {p.name} doit être de type {p.type}.")
        if p.enum and value not in p.enum:
            raise FunctionError(f"Le paramètre {p.name} doit valoir l'une de ces valeurs : {', '.join(map(str, p.enum))}.")
        if p.minimum is not None and value < p.minimum:
            raise FunctionError(f"Le paramètre {p.name} doit être au moins {p.minimum:g}.")
        if p.maximum is not None and value > p.maximum:
            raise FunctionError(f"Le paramètre {p.name} doit être au plus {p.maximum:g}.")
        return value

    def execute(self, name: str, arguments: Any, confirmed: bool = False,
                user_message: str | None = None) -> FunctionResult:
        """Exécute une fonction enregistrée. Ne lève jamais : les échecs deviennent `ok=False`.
        Journal INFO : nom, arguments autorisés (`log_args`), statut, durée ; jamais le résultat."""
        start = time.perf_counter()
        result = self._execute(name, arguments, confirmed, user_message)
        spec = self._functions.get(name)
        shown = {}
        if spec and isinstance(arguments, dict):
            shown = {k: str(arguments[k])[:80] for k in spec.log_args if k in arguments}
        log.info("fonction %s args=%s statut=%s durée=%d ms", name, json.dumps(shown, ensure_ascii=False),
                 "ok" if result.ok else "erreur", (time.perf_counter() - start) * 1000)
        return result

    def _execute(self, name: str, arguments: Any, confirmed: bool,
                 user_message: str | None = None) -> FunctionResult:
        spec = self._functions.get(name)
        if spec is None:
            return FunctionResult(False, error=f"Fonction inconnue : {name}.", name=name)
        if spec.level not in self.allowed_levels and not (spec.level == SENSITIVE and confirmed):
            return FunctionResult(False, name=name, error=(
                f"La fonction {name} (niveau « {spec.level} ») exige une confirmation explicite "
                "de l'utilisateur ; elle n'a pas été exécutée."))
        if spec.intent_guard is not None:
            try:
                allowed = bool(user_message) and spec.intent_guard(user_message) is True
            except Exception:  # noqa: BLE001 - garde défaillant = refus
                log.exception("Erreur dans le garde d'intention de %s", name)
                allowed = False
            if not allowed:
                return FunctionResult(False, name=name, error=spec.intent_refusal or (
                    f"La fonction {name} n'a pas été exécutée : la demande explicite de l'utilisateur "
                    "n'est pas établie. Demande-lui de confirmer."))
        try:
            clean = self.validate(spec, arguments)
            return FunctionResult(True, spec.handler(**clean), name=name)
        except FunctionError as e:
            return FunctionResult(False, error=str(e), name=name)
        except Exception:  # noqa: BLE001 - un bug de fonction ne doit pas faire échouer la conversation
            log.exception("Erreur inattendue dans la fonction %s", name)
            return FunctionResult(False, error=f"Erreur interne dans la fonction {name}.", name=name)
