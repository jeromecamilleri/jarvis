import httpx

from app.core.llm import LLMError


class OllamaEmbedder:
    def __init__(self, url: str, model: str, timeout: float = 60):
        self.url, self.model, self.timeout = url, model, timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            r = httpx.post(f"{self.url}/api/embed",
                           json={"model": self.model, "input": texts}, timeout=self.timeout)
            r.raise_for_status()
            vectors = r.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LLMError(f"Erreur embeddings : {e}") from e
        if len(vectors) != len(texts):
            raise LLMError("Nombre d'embeddings inattendu")
        return vectors
