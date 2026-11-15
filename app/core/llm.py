import httpx


class LLMError(Exception):
    pass


class OllamaClient:
    def __init__(self, url: str, model: str, num_ctx: int, timeout: float):
        self.url, self.model, self.num_ctx, self.timeout = url, model, num_ctx, timeout

    def chat(self, messages: list[dict]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_ctx": self.num_ctx},
        }
        try:
            r = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r.json()["message"]["content"]
        except httpx.TimeoutException as e:
            raise LLMError("Délai d'attente Ollama dépassé") from e
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LLMError(f"Erreur Ollama : {e}") from e
