import os

os.environ.setdefault("JARVIS_ENV_FILE", "")  # jamais le vrai .env de la machine
os.environ.setdefault("JARVIS_TRACE", "0")  # pas de fichier de traces dans data/ pendant les tests
os.environ.setdefault("JARVIS_KENSAKU", "0")  # les tests ne lancent jamais le vrai Kensaku

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.function_registry import FunctionRegistry
from app.core.llm import LLMError
from app.core.model_adapter import TEXT_AND_TOOLS, ModelResponse
from app.main import create_app


class FakeLLM:
    capabilities = TEXT_AND_TOOLS

    def __init__(self):
        self.calls = []

    def generate(self, request):
        self.calls.append(request.messages)
        return ModelResponse("réponse de test")


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def make_client(db_path):
    def _make(llm=None, embedder=None, stt=None, tts=None):
        llm = llm or FakeLLM()
        settings = Settings(db_path=db_path, embed_model="")
        return TestClient(create_app(settings, llm=llm, embedder=embedder,
                                     stt=stt, tts=tts, functions=FunctionRegistry())), llm
    return _make
