import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    ollama_url: str = os.getenv("JARVIS_OLLAMA_URL", "http://localhost:11434")
    model: str = os.getenv("JARVIS_MODEL", "qwen3:8b")
    num_ctx: int = int(os.getenv("JARVIS_NUM_CTX", "8192"))
    timeout: float = float(os.getenv("JARVIS_TIMEOUT", "120"))
    db_path: str = os.getenv("JARVIS_DB_PATH", "data/jarvis.db")
    history_limit: int = 20


def get_settings() -> Settings:
    return Settings()
