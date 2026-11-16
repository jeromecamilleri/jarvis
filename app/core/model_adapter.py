"""Contrat commun entre l'orchestrateur et un modèle de langage, indépendant du fournisseur (Ollama, etc.).

L'orchestrateur construit un `ModelRequest` (messages structurés, outils disponibles) et reçoit un
`ModelResponse` (texte, demandes d'appel de fonctions, message brut à remettre dans l'historique).
Toute erreur du fournisseur est remontée sous forme de `LLMError`.

Limite actuelle du contrat : les messages échangés (historique, appels de fonctions, résultats d'outils) et
les descriptions de fonctions (`ModelRequest.tools`) sont encore au format Ollama / OpenAI
(`{"type": "function", "function": {...}}`, résultat d'outil `{"role": "tool", "tool_name": ..., "content": ...}`).
Un futur adaptateur doit soit accepter ce format, soit le convertir en interne.
"""
from dataclasses import dataclass, field
from typing import Protocol


TEXT, TOOLS = "text", "tools"  # capacités déclarables par un adaptateur
TEXT_AND_TOOLS = frozenset({TEXT, TOOLS})


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class ModelRequest:
    messages: list[dict]
    # Messages structurés (rôles system/user/assistant/tool), au format Ollama pour l'instant : un message
    # de résultat d'outil est `{"role": "tool", "tool_name": <nom>, "content": <JSON>}`. Ce format doit
    # être celui que l'adaptateur concerné attend (ou qu'il convertit lui-même).
    # Fonctions proposées au modèle, au format Ollama actuel ; None = réponse purement textuelle.
    tools: list[dict] | None = None
    options: dict = field(default_factory=dict)  # paramètres de génération en plus de ceux de l'adaptateur
    require_content: bool = False  # une réponse sans texte est une erreur (réponse finale à l'utilisateur)


@dataclass
class ModelResponse:
    """Texte final, ou demandes d'exécution de fonctions.

    `message` est le message brut du modèle, normalisé par l'adaptateur : l'orchestrateur le remet tel quel
    dans l'historique, il doit donc être directement réinjectable dans `ModelRequest.messages`.
    `tool_calls` contient les appels de fonctions extraits de ce même message (vide si le modèle répond
    en texte)."""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    message: dict = field(default_factory=dict)


class ModelAdapter(Protocol):
    capabilities: frozenset[str]  # déclarées explicitement par l'adaptateur (TEXT, TOOLS)

    def generate(self, request: ModelRequest) -> ModelResponse: ...
