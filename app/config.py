import os
from dataclasses import dataclass
from pathlib import Path

from app.voice.stt import DEFAULT_HINT


def load_env_file(path: str | Path | None = None) -> list[str]:
    """Charge un fichier `.env` dans `os.environ` SANS écraser les variables déjà définies (même vides).

    Chemin : `JARVIS_ENV_FILE` (vide = chargement désactivé), sinon `.env` à la racine du projet.
    Syntaxe volontairement minimale : lignes `CLE=valeur`, `export` facultatif, commentaires `#` en début de
    ligne, guillemets simples ou doubles autour de la valeur. Aucune expansion de variables (`$X`), aucune
    substitution de commande, aucune exécution de code : la valeur est le texte littéral. Fichier absent ou
    illisible : sans effet. Retourne les noms de variables chargées (jamais leurs valeurs)."""
    if path is None:
        configured = os.environ.get("JARVIS_ENV_FILE")
        if configured is not None and not configured.strip():
            return []
        path = configured or Path(__file__).resolve().parents[1] / ".env"
    try:
        lines = Path(path).expanduser().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    loaded = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.removeprefix("export ").strip(), value.strip()
        if not key.isidentifier() or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value
        loaded.append(key)
    return loaded


load_env_file()


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
    timezone: str = os.getenv("JARVIS_TIMEZONE", "Europe/Paris")
    google_credentials_path: str = os.getenv("JARVIS_GOOGLE_CREDENTIALS", "data/google_calendar/credentials.json")
    google_token_path: str = os.getenv("JARVIS_GOOGLE_TOKEN", "data/google_calendar/token.json")
    kensaku_enabled: bool = os.getenv("JARVIS_KENSAKU", "1").lower() not in ("0", "false", "no", "")
    kensaku_cli_path: str = os.getenv("JARVIS_KENSAKU_CLI", "~/.local/bin/kensaku-cli")
    kensaku_timeout: float = float(os.getenv("JARVIS_KENSAKU_TIMEOUT", "15"))
    minillm_dir: str = os.getenv("JARVIS_MINILLM_DIR", "")  # vide = mini-llm désactivé
    minillm_python: str = os.getenv("JARVIS_MINILLM_PYTHON", "")  # défaut : <dir>/.venv/bin/python
    minillm_checkpoint: str = os.getenv("JARVIS_MINILLM_CHECKPOINT", "models/checkpoints/livres_P4_theatre_meilleur.pt")
    minillm_tokenizer: str = os.getenv("JARVIS_MINILLM_TOKENIZER", "datasets/tokenized/bpe_4000.json")
    minillm_timeout: float = float(os.getenv("JARVIS_MINILLM_TIMEOUT", "60"))
    trace_enabled: bool = os.getenv("JARVIS_TRACE", "1").lower() not in ("0", "false", "no", "")
    trace_path: str = os.getenv("JARVIS_TRACE_PATH", "data/logs/trace.jsonl")
    trace_max_bytes: int = int(os.getenv("JARVIS_TRACE_MAX_BYTES", "5000000"))
    trace_backups: int = int(os.getenv("JARVIS_TRACE_BACKUPS", "5"))
    max_tool_rounds: int = int(os.getenv("JARVIS_MAX_TOOL_ROUNDS", "3"))
    episodic_k: int = 5
    reply_reserve: int = 1024  # tokens gardés pour la réponse du modèle
    memory_budget: int = 1500  # tokens max de souvenirs épisodiques injectés
    history_limit: int = 20


def get_settings() -> Settings:
    return Settings()
