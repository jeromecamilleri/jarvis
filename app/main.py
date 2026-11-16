import os
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.chat import router
from app.api.reminders import router as reminders_router
from app.api.voice import router as voice_router
from app.config import Settings, get_settings
from app.core.embeddings import OllamaEmbedder
from app.core.function_registry import FunctionRegistry
from app.core.llm import OllamaAdapter
from app.core import tracing
from app.core.minillm_adapter import MiniLLMAdapter
from app.core.model_registry import SPECIALIST, ModelRegistry, ModelSpec
from app.core.model_router import ModelRouter
from app.core.orchestrator import Orchestrator
from app.core.reminders import AudioNotifier, ReminderService, utc_now
from app.functions import calendar as google_calendar
from app.functions import kensaku
from app.functions import story
from app.memory.conversations import ConversationStore
from app.memory.database import connect
from app.memory.episodic import EpisodicMemory
from app.memory.profile import ProfileStore
from app.memory.reminders import ReminderStore
from app.voice.stt import WhisperSTT
from app.voice.tts import PiperTTS


def log_minillm_status(adapter: MiniLLMAdapter, missing: list[str]) -> None:
    """Journalise l'état de mini-llm au démarrage (noms de fichiers seulement, jamais de contenu)."""
    log = logging.getLogger("jarvis")
    names = {adapter.python: "environnement Python", adapter.bridge: "script pont (tools/minillm_bridge.py)",
             adapter.checkpoint: "checkpoint", adapter.tokenizer: "tokenizer"}
    if not Path(adapter.minillm_dir).is_dir():
        log.warning("mini-llm DÉSACTIVÉ : répertoire JARVIS_MINILLM_DIR introuvable (%s) ; la fonction "
                    "story_generate répondra qu'elle est indisponible.", Path(adapter.minillm_dir).name)
        return
    if not missing:
        log.info("mini-llm activé : spécialité %s, checkpoint %s.", story.SPECIALTY, Path(adapter.checkpoint).name)
        return
    log.warning("mini-llm DÉSACTIVÉ (configuration incomplète) : %s ; la fonction story_generate "
                "répondra qu'elle est indisponible.", " ; ".join(
                    f"{names.get(p, 'fichier')} introuvable ({Path(p).name})" for p in missing))


def _minillm_spec(settings: Settings) -> ModelSpec:
    base = Path(settings.minillm_dir).expanduser()
    resolve = lambda p: str(Path(p).expanduser() if Path(p).expanduser().is_absolute() else base / p)  # noqa: E731
    adapter = MiniLLMAdapter(
        str(base), settings.minillm_python or str(base / ".venv" / "bin" / "python"),
        resolve(settings.minillm_checkpoint), resolve(settings.minillm_tokenizer),
        str(Path(__file__).resolve().parents[1] / "tools" / "minillm_bridge.py"), settings.minillm_timeout)
    missing = adapter.missing_files()
    log_minillm_status(adapter, missing)
    return ModelSpec("mini-llm", tracing.TracedAdapter(adapter, "mini-llm", "specialist"), role=SPECIALIST, specialty=story.SPECIALTY, enabled=not missing)


def create_app(settings: Settings | None = None, llm=None, embedder=None, stt=None, tts=None,
               notifier=None, clock=None, tz=None, functions: FunctionRegistry | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.trace_enabled:
        tracing.configure(settings.trace_path, settings.trace_max_bytes, settings.trace_backups)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.reminders.start()
        client = app.state.kensaku
        if client is not None:  # Kensaku indisponible : Jarvis démarre quand même
            try:
                await asyncio.to_thread(client.start)
            except Exception as e:  # noqa: BLE001
                logging.getLogger("jarvis").warning("Kensaku non démarré : %s", e)
        try:
            yield
        finally:
            if client is not None:
                await asyncio.to_thread(client.close)
            app.state.reminders.shutdown()

    app = FastAPI(title="Jarvis", lifespan=lifespan)
    app.state.kensaku = None
    parent = os.path.dirname(settings.db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = connect(settings.db_path)
    app.state.conversations = ConversationStore(conn)
    app.state.profile = ProfileStore(conn)
    llm = llm or OllamaAdapter(settings.ollama_url, settings.model,
                               settings.num_ctx, settings.timeout)
    if embedder is None and settings.embed_model:
        embedder = OllamaEmbedder(settings.ollama_url, settings.embed_model)
    app.state.reminder_store = ReminderStore(conn)
    app.state.episodic = EpisodicMemory(conn, embedder)
    app.state.episodic.backfill()
    app.state.stt = stt or WhisperSTT(settings.whisper_model, settings.whisper_dir,
                                          settings.whisper_hint)
    app.state.tts = tts or PiperTTS(settings.piper_voice)
    app.state.reminders = ReminderService(
        app.state.reminder_store,
        notifier or AudioNotifier(app.state.tts, settings.audio_player),
        clock or utc_now, tz)
    default_functions = functions is None
    if functions is None:  # fonctions enregistrées explicitement au démarrage
        functions = FunctionRegistry()
        google_calendar.register(functions, settings)
        app.state.kensaku = kensaku.register(functions, settings)
    app.state.functions = functions
    app.state.models = ModelRegistry()
    app.state.models.register(ModelSpec("qwen", tracing.TracedAdapter(llm, "qwen", "primary")))  # modèle principal
    if settings.minillm_dir:  # spécialiste expérimental, désactivé tant que ses fichiers manquent
        app.state.models.register(_minillm_spec(settings))
        if default_functions:  # délégation explicite : pas de repli silencieux sur Qwen
            story.register(functions, ModelRouter(app.state.models, fallback="error"))
    else:
        logging.getLogger("jarvis").info(
            "mini-llm non configuré (JARVIS_MINILLM_DIR absent ou vide) : spécialiste et story_generate non enregistrés.")
    app.state.orchestrator = Orchestrator(
        ModelRouter(app.state.models), app.state.conversations, app.state.profile, settings.history_limit,
        episodic=app.state.episodic, episodic_k=settings.episodic_k,
        num_ctx=settings.num_ctx, reply_reserve=settings.reply_reserve,
        memory_budget=settings.memory_budget, reminders=app.state.reminders,
        functions=functions, max_tool_rounds=settings.max_tool_rounds, timezone=settings.timezone)
    app.include_router(router)
    app.include_router(voice_router)
    app.include_router(reminders_router)

    @app.get("/health")
    def health():
        return {"status": "ok", "model": settings.model}

    return app



def __getattr__(name: str):
    # `uvicorn app.main:app` : l'application n'est créée qu'à la demande, pas à l'import.
    if name == "app":
        global app
        app = create_app()
        return app
    raise AttributeError(name)
