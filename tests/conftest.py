import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.llm import LLMError
from app.main import create_app


class FakeLLM:
    def __init__(self):
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return "réponse de test"


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def make_client(db_path):
    def _make(llm=None, embedder=None):
        llm = llm or FakeLLM()
        settings = Settings(db_path=db_path, embed_model="")
        return TestClient(create_app(settings, llm=llm, embedder=embedder)), llm
    return _make
