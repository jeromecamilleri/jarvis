"""Registre et routeur de modèles (faux adaptateurs, sans Ollama) + intégration avec l'orchestrateur."""
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.function_registry import FunctionRegistry, FunctionSpec, Param
from app.core.llm import LLMError, OllamaAdapter
from app.core.model_adapter import TEXT, TEXT_AND_TOOLS, TOOLS, ModelResponse, ToolCall
from app.core.model_registry import ModelRegistry, ModelSpec, UnknownModelError
from app.core.model_router import ModelRouter, NoCompatibleModelError, RoutingRequest
from app.main import create_app


class FakeAdapter:
    def __init__(self, name="fake", capabilities=TEXT_AND_TOOLS, script=None):
        self.name, self.capabilities, self.script, self.requests = name, capabilities, list(script or []), []

    def generate(self, request):
        self.requests.append(request)
        if request.tools is not None and self.script:
            return self.script.pop(0)
        return ModelResponse(f"réponse de {self.name}", [], {"role": "assistant", "content": f"réponse de {self.name}"})


def registry(*specs):
    reg = ModelRegistry()
    for spec in specs:
        reg.register(spec)
    return reg


def qwen(**kw):
    return ModelSpec("qwen", kw.pop("adapter", FakeAdapter("qwen")), **kw)


def sudoku(**kw):
    return ModelSpec("sudoku", kw.pop("adapter", FakeAdapter("sudoku", frozenset({TEXT}))), "specialist", "sudoku", **kw)


# --- registre ---------------------------------------------------------------------------
def test_registry_registers_and_finds_models():
    q, s = qwen(), sudoku()
    reg = registry(q, s)
    assert reg.get("qwen") is q and reg.primary is q and reg.specialists("sudoku") == [s]
    assert reg.specialists("autre") == []


def test_registry_rejects_unknown_duplicate_and_invalid_models():
    reg = registry(qwen())
    with pytest.raises(UnknownModelError):
        reg.get("inconnu")
    with pytest.raises(ValueError, match="déjà enregistré"):
        reg.register(qwen())
    with pytest.raises(ValueError, match="principal existe déjà"):
        reg.register(ModelSpec("autre", FakeAdapter()))
    with pytest.raises(ValueError, match="spécialité"):
        ModelSpec("x", FakeAdapter(), "specialist")
    with pytest.raises(UnknownModelError):
        ModelRegistry().primary


def test_capabilities_are_declared_by_the_adapter_not_guessed():
    assert OllamaAdapter("http://x", "m", 1, 1).capabilities == frozenset({TEXT, TOOLS})
    assert qwen(adapter=FakeAdapter(capabilities=frozenset({TEXT}))).capabilities == frozenset({TEXT})


# --- routeur ----------------------------------------------------------------------------
def test_general_request_goes_to_primary_even_with_specialists_registered():
    q = qwen()
    route = ModelRouter(registry(q, sudoku())).select(RoutingRequest(frozenset({TEXT, TOOLS})))
    assert route.model is q and route.fallback is False


def test_explicit_specialty_selects_the_specialist():
    s = sudoku()
    route = ModelRouter(registry(qwen(), s)).select(RoutingRequest(specialty="sudoku"))
    assert route.model is s and route.fallback is False


def test_required_capabilities_are_checked_and_never_dropped():
    router = ModelRouter(registry(qwen(adapter=FakeAdapter(capabilities=frozenset({TEXT})))))
    assert router.select(RoutingRequest(frozenset({TEXT}))).model.id == "qwen"
    with pytest.raises(NoCompatibleModelError, match="tools"):
        router.select(RoutingRequest(frozenset({TEXT, TOOLS})))


def test_specialist_without_required_capability_falls_back_to_primary(caplog):
    with caplog.at_level(logging.WARNING, logger="jarvis.models"):
        route = ModelRouter(registry(qwen(), sudoku())).select(RoutingRequest(frozenset({TEXT, TOOLS}), "sudoku"))
    assert route.model.id == "qwen" and route.fallback is True
    assert "sudoku" in caplog.text and "repli" in caplog.text


@pytest.mark.parametrize("disabled", [False, True], ids=["absent", "désactivé"])
def test_missing_or_disabled_specialist_falls_back_with_warning(caplog, disabled):
    reg = registry(qwen(), *([sudoku(enabled=False)] if disabled else []))
    with caplog.at_level(logging.WARNING, logger="jarvis.models"):
        route = ModelRouter(reg).select(RoutingRequest(specialty="sudoku"))
    assert route.model.id == "qwen" and route.fallback and "repli" in caplog.text


def test_error_fallback_mode_refuses_instead_of_falling_back():
    with pytest.raises(NoCompatibleModelError, match="sudoku"):
        ModelRouter(registry(qwen()), fallback="error").select(RoutingRequest(specialty="sudoku"))
    with pytest.raises(ValueError):
        ModelRouter(registry(qwen()), fallback="peut-être")


def test_disabled_primary_is_an_explicit_error_even_for_fallback():
    router = ModelRouter(registry(qwen(enabled=False), sudoku(enabled=False)))
    for request in (RoutingRequest(), RoutingRequest(specialty="sudoku")):
        with pytest.raises(NoCompatibleModelError, match="désactivé"):
            router.select(request)


# --- intégration orchestrateur ---------------------------------------------------------
def add(a):
    return {"sum": a + 1}


def functions():
    reg = FunctionRegistry()
    reg.register(FunctionSpec("add", "Ajoute 1.", add, (Param("a", "integer", "n"),)))
    return reg


def call(name="add", **args):
    return ModelResponse("", [ToolCall(name, args)], {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": args}}]})


class CountingRouter(ModelRouter):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.requests = []

    def select(self, request):
        self.requests.append(request)
        return super().select(request)


def build(db_path, primary, specialist=None, fns=None):
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=primary,
                     functions=functions() if fns is None else fns)
    reg = app.state.models
    if specialist:
        reg.register(specialist)
    router = CountingRouter(reg)
    app.state.orchestrator.router = router
    return TestClient(app), router


def test_qwen_is_registered_as_primary_at_startup(db_path):
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=FakeAdapter("qwen"))
    assert app.state.models.primary.id == "qwen" and app.state.models.primary.adapter.name == "qwen"


def test_one_model_serves_the_whole_turn_including_tool_rounds_and_final_answer(db_path):
    q = FakeAdapter("qwen", script=[call(a=1), call(a=2), call(a=3), call(a=4)])
    s = FakeAdapter("sudoku", frozenset({TEXT, TOOLS}))
    client, router = build(db_path, q, ModelSpec("sudoku", s, "specialist", "sudoku"))
    reply = client.post("/chat", json={"message": "Calcule 1"}).json()["reply"]
    assert reply == "réponse de qwen" and len(q.requests) >= 4 and s.requests == []
    assert len(router.requests) == 1 and router.requests[0].required == frozenset({TEXT, TOOLS})
    assert router.requests[0].specialty is None


def test_text_turn_without_functions_requires_text_only(db_path):
    q = FakeAdapter("qwen")
    client, router = build(db_path, q, fns=FunctionRegistry())
    assert client.post("/chat", json={"message": "Bonjour"}).json()["reply"] == "réponse de qwen"
    assert [r.required for r in router.requests] == [frozenset({TEXT})] and q.requests[0].tools is None


def test_primary_without_tools_fails_explicitly_instead_of_dropping_functions(db_path):
    q = FakeAdapter("qwen", frozenset({TEXT}))
    client, _ = build(db_path, q)
    r = client.post("/chat", json={"message": "Bonjour"})
    assert r.status_code == 502 and "tools" in r.json()["detail"] and q.requests == []


def test_commands_do_not_need_a_model(db_path):
    q = FakeAdapter("qwen")
    client, router = build(db_path, q)
    assert client.post("/chat", json={"message": "Retiens que mon employeur est Atos"}).status_code == 200
    assert router.requests == [] and q.requests == []


def test_llm_error_from_adapter_is_still_a_502(db_path):
    class Broken(FakeAdapter):
        def generate(self, request):
            raise LLMError("timeout")
    client, _ = build(db_path, Broken("qwen"))
    assert client.post("/chat", json={"message": "Bonjour"}).status_code == 502
