import os

from fastapi import FastAPI

from app.api.chat import router
from app.api.voice import router as voice_router
from app.config import Settings, get_settings
from app.core.embeddings import OllamaEmbedder
from app.core.llm import OllamaClient
from app.core.orchestrator import Orchestrator
from app.memory.conversations import ConversationStore
from app.memory.database import connect
from app.memory.episodic import EpisodicMemory
from app.memory.profile import ProfileStore
from app.voice.stt import WhisperSTT
from app.voice.tts import PiperTTS


def create_app(settings: Settings | None = None, llm=None, embedder=None, stt=None, tts=None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Jarvis")
    parent = os.path.dirname(settings.db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = connect(settings.db_path)
    app.state.conversations = ConversationStore(conn)
    app.state.profile = ProfileStore(conn)
    llm = llm or OllamaClient(settings.ollama_url, settings.model,
                              settings.num_ctx, settings.timeout)
    if embedder is None and settings.embed_model:
        embedder = OllamaEmbedder(settings.ollama_url, settings.embed_model)
    app.state.episodic = EpisodicMemory(conn, embedder)
    app.state.episodic.backfill()
    app.state.orchestrator = Orchestrator(
        llm, app.state.conversations, app.state.profile, settings.history_limit,
        episodic=app.state.episodic, episodic_k=settings.episodic_k,
        num_ctx=settings.num_ctx, reply_reserve=settings.reply_reserve,
        memory_budget=settings.memory_budget)
    app.state.stt = stt or WhisperSTT(settings.whisper_model, settings.whisper_dir,
                                          settings.whisper_hint)
    app.state.tts = tts or PiperTTS(settings.piper_voice)
    app.include_router(router)
    app.include_router(voice_router)

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
