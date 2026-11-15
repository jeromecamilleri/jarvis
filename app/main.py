import os

from fastapi import FastAPI

from app.api.chat import router
from app.config import Settings, get_settings
from app.core.embeddings import OllamaEmbedder
from app.core.llm import OllamaClient
from app.core.orchestrator import Orchestrator
from app.memory.conversations import ConversationStore
from app.memory.database import connect
from app.memory.episodic import EpisodicMemory
from app.memory.profile import ProfileStore


def create_app(settings: Settings | None = None, llm=None, embedder=None) -> FastAPI:
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
    app.include_router(router)

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
