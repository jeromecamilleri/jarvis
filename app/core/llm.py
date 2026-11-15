import json
from dataclasses import dataclass, field

import httpx


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class ToolReply:
    """Réponse de /api/chat avec outils : texte final, ou demandes d'exécution de fonctions."""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    message: dict = field(default_factory=dict)  # message brut, à renvoyer tel quel dans l'historique


class OllamaClient:
    def __init__(self, url: str, model: str, num_ctx: int, timeout: float):
        self.url, self.model, self.num_ctx, self.timeout = url, model, num_ctx, timeout

    def chat(self, messages: list[dict]) -> str:
        message = self._post(messages)
        if "content" not in message:
            raise LLMError("Erreur Ollama : réponse sans contenu")
        return message["content"]

    def chat_tools(self, messages: list[dict], tools: list[dict]) -> ToolReply:
        """Comme chat(), en autorisant le modèle à demander des fonctions (tool calls)."""
        message = self._post(messages, tools)
        calls = []
        try:
            for call in message.get("tool_calls") or []:
                args = call["function"].get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                calls.append(ToolCall(call["function"]["name"], args))
        except (KeyError, TypeError, ValueError) as e:
            raise LLMError(f"Appel de fonction illisible : {e}") from e
        return ToolReply(message.get("content") or "", calls, message)

    def _post(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": self.num_ctx},
        }
        if tools:
            payload["tools"] = tools
        try:
            r = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["message"]
        except httpx.TimeoutException as e:
            raise LLMError("Délai d'attente Ollama dépassé") from e
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LLMError(f"Erreur Ollama : {e}") from e
