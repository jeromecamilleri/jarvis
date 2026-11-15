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
    def _make(llm=None):
        llm = llm or FakeLLM()
        return TestClient(create_app(Settings(db_path=db_path), llm=llm)), llm
    return _make
