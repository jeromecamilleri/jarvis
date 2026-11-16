import json

import httpx

from app.core import tracing
from app.core.model_adapter import TEXT_AND_TOOLS, LLMError, ModelRequest, ModelResponse, ToolCall

ToolReply = ModelResponse  # nom historique, conservé pour compatibilité

__all__ = ["LLMError", "ToolCall", "ToolReply", "OllamaAdapter", "OllamaClient"]


class OllamaAdapter:
    """Implémentation de `ModelAdapter` pour Ollama (/api/chat). Seul endroit qui connaît ce protocole."""

    capabilities = TEXT_AND_TOOLS

    def __init__(self, url: str, model: str, num_ctx: int, timeout: float):
        self.url, self.model, self.num_ctx, self.timeout = url, model, num_ctx, timeout

    def generate(self, request: ModelRequest) -> ModelResponse:
        message = self._post(request)
        if request.require_content and "content" not in message:
            raise LLMError("Erreur Ollama : réponse sans contenu")
        calls = []
        if request.tools:
            try:
                for call in message.get("tool_calls") or []:
                    args = call["function"].get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args)
                    calls.append(ToolCall(call["function"]["name"], args))
            except (KeyError, TypeError, ValueError) as e:
                raise LLMError(f"Appel de fonction illisible : {e}") from e
        return ModelResponse(message.get("content") or "", calls, message)

    # Façades de compatibilité
    def chat(self, messages: list[dict]) -> str:
        return self.generate(ModelRequest(messages, require_content=True)).content

    def chat_tools(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        return self.generate(ModelRequest(messages, tools))

    def _post(self, request: ModelRequest) -> dict:
        payload = {
            "model": self.model,
            "messages": request.messages,
            "stream": False,
            "options": {"num_ctx": self.num_ctx, **request.options},
        }
        if request.tools:
            payload["tools"] = request.tools
        try:
            with tracing.span("adapter", "ollama", adapter="OllamaAdapter", model=self.model,
                              tools=len(request.tools or [])) as info:
                r = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
                info["http_status"] = getattr(r, "status_code", None)
                r.raise_for_status()
                body = r.json()
                if isinstance(body, dict):
                    info.update(prompt_tokens=body.get("prompt_eval_count"),
                                completion_tokens=body.get("eval_count"))
                return body["message"]
        except httpx.TimeoutException as e:
            raise LLMError("Délai d'attente Ollama dépassé") from e
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LLMError(f"Erreur Ollama : {e}") from e


OllamaClient = OllamaAdapter  # nom historique, conservé pour compatibilité
