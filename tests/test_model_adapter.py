"""Contrat ModelAdapter et OllamaAdapter (sans Ollama réel : httpx.post est simulé)."""
import httpx
import pytest

from app.core.llm import OllamaAdapter, OllamaClient, ToolReply
from app.core.model_adapter import LLMError, ModelAdapter, ModelRequest, ModelResponse, ToolCall

MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"},
        {"role": "tool", "tool_name": "add", "content": "{}"}]


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture
def post(monkeypatch):
    """Simule httpx.post ; retourne (réponse à servir, requêtes reçues)."""
    state = {"body": {"message": {"role": "assistant", "content": "salut"}}, "seen": []}

    def fake(url, json, timeout):
        state["seen"].append((url, json, timeout))
        return FakeResponse(state["body"])
    monkeypatch.setattr(httpx, "post", fake)
    return state


def adapter():
    return OllamaAdapter("http://x", "qwen", 8192, 7)


def test_adapter_respects_the_common_contract_and_keeps_legacy_names():
    adapter_: ModelAdapter = adapter()  # le Protocol n'exige que generate()
    assert callable(adapter_.generate) and OllamaClient is OllamaAdapter and ToolReply is ModelResponse


def test_payload_is_exactly_the_historical_one(post):
    adapter().generate(ModelRequest(MSGS))
    url, payload, timeout = post["seen"][0]
    assert url == "http://x/api/chat" and timeout == 7
    assert payload == {"model": "qwen", "messages": MSGS, "stream": False, "options": {"num_ctx": 8192}}


def test_tools_and_extra_options_are_transmitted(post):
    adapter().generate(ModelRequest(MSGS, [{"t": 1}], {"temperature": 0.2}))
    payload = post["seen"][0][1]
    assert payload["tools"] == [{"t": 1}] and payload["options"] == {"num_ctx": 8192, "temperature": 0.2}


def test_text_response(post):
    out = adapter().generate(ModelRequest(MSGS, [{"t": 1}]))
    assert out.content == "salut" and out.tool_calls == [] and out.message == post["body"]["message"]


def test_function_call_is_normalized_and_raw_message_kept(post):
    post["body"] = {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "function": {"index": 0, "name": "add", "arguments": {"a": 1}}},
        {"function": {"name": "add", "arguments": '{"a": 2}'}}]}}
    out = adapter().generate(ModelRequest(MSGS, [{"t": 1}]))
    assert out.tool_calls == [ToolCall("add", {"a": 1}), ToolCall("add", {"a": 2})]
    assert out.message == post["body"]["message"]


def test_unreadable_function_call_is_an_llm_error(post):
    post["body"] = {"message": {"content": "", "tool_calls": [{"function": {"arguments": {}}}]}}
    with pytest.raises(LLMError, match="illisible"):
        adapter().generate(ModelRequest(MSGS, [{"t": 1}]))


def test_tool_calls_are_ignored_for_plain_text_requests(post):
    post["body"] = {"message": {"content": "ok", "tool_calls": [{"function": {"arguments": {}}}]}}
    out = adapter().generate(ModelRequest(MSGS, require_content=True))
    assert out.content == "ok" and out.tool_calls == []


def test_missing_content_is_an_error_only_when_required(post):
    post["body"] = {"message": {"role": "assistant"}}
    with pytest.raises(LLMError, match="sans contenu"):
        adapter().generate(ModelRequest(MSGS, require_content=True))
    assert adapter().generate(ModelRequest(MSGS, [{"t": 1}])).content == ""


@pytest.mark.parametrize("error,message", [
    (httpx.ReadTimeout("t"), "Délai d'attente"), (httpx.ConnectError("c"), "Erreur Ollama")])
def test_ollama_errors_become_llm_errors(monkeypatch, error, message):
    def boom(*a, **k):
        raise error
    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(LLMError, match=message):
        adapter().generate(ModelRequest(MSGS))


def test_malformed_body_is_an_llm_error(post):
    post["body"] = {"pas_de_message": 1}
    with pytest.raises(LLMError):
        adapter().generate(ModelRequest(MSGS))


def test_legacy_facades_delegate_to_generate(post):
    assert adapter().chat(MSGS) == "salut" and "tools" not in post["seen"][0][1]
    assert adapter().chat_tools(MSGS, [{"t": 1}]).content == "salut" and post["seen"][1][1]["tools"] == [{"t": 1}]
