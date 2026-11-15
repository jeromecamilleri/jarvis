import httpx
import pytest

from app.core.llm import LLMError, OllamaClient


def test_timeout_raises_llmerror(monkeypatch):
    def boom(*a, **k):
        raise httpx.ReadTimeout("t")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(LLMError):
        OllamaClient("http://x", "m", 8192, 1).chat([])


def test_connection_error_raises_llmerror(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("c")
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(LLMError):
        OllamaClient("http://x", "m", 8192, 1).chat([])
