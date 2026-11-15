import os

from fastapi import FastAPI

from app.api.chat import router
from app.config import Settings, get_settings
from app.core.llm import OllamaClient
from app.core.orchestrator import Orchestrator
from app.memory.conversations import ConversationStore
from app.memory.database import connect
from app.memory.profile import ProfileStore


def create_app(settings: Settings | None = None, llm=None) -> FastAPI:
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
    app.state.orchestrator = Orchestrator(
        llm, app.state.conversations, app.state.profile, settings.history_limit)
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok", "model": settings.model}

    return app


app = create_app()
