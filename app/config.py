import os
from dataclasses import dataclass

from app.voice.stt import DEFAULT_HINT


@dataclass(frozen=True)
class Settings:
    ollama_url: str = os.getenv("JARVIS_OLLAMA_URL", "http://localhost:11434")
    model: str = os.getenv("JARVIS_MODEL", "qwen3:8b")
    num_ctx: int = int(os.getenv("JARVIS_NUM_CTX", "8192"))
    timeout: float = float(os.getenv("JARVIS_TIMEOUT", "120"))
    db_path: str = os.getenv("JARVIS_DB_PATH", "data/jarvis.db")
    embed_model: str = os.getenv("JARVIS_EMBED_MODEL", "bge-m3")
    whisper_model: str = os.getenv("JARVIS_WHISPER_MODEL", "small")
    whisper_dir: str = os.getenv("JARVIS_WHISPER_DIR", "data/models/whisper")
    whisper_hint: str = os.getenv("JARVIS_WHISPER_HINT", DEFAULT_HINT)
    piper_voice: str = os.getenv("JARVIS_PIPER_VOICE", "data/models/piper/fr_FR-siwis-medium.onnx")
    audio_player: str = os.getenv("JARVIS_AUDIO_PLAYER", "pw-play")
    episodic_k: int = 5
    reply_reserve: int = 1024  # tokens gardés pour la réponse du modèle
    memory_budget: int = 1500  # tokens max de souvenirs épisodiques injectés
    history_limit: int = 20


def get_settings() -> Settings:
    return Settings()
