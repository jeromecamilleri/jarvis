"""Registre de fonctions, appels d'outils Ollama et boucle de l'orchestrateur (sans Google)."""
import copy
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.function_registry import (READ, SAFE_WRITE, SENSITIVE, FunctionError, FunctionRegistry,
                                        FunctionSpec, Param)
from app.core.llm import LLMError, OllamaClient, ToolCall, ToolReply
from app.core.model_adapter import TEXT, TEXT_AND_TOOLS, ModelResponse
from app.main import create_app

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 11, 15, 17, 24, tzinfo=PARIS)  # un dimanche


def add(a, b=1):
    return {"sum": a + b}


def make_registry(**kw):
    reg = FunctionRegistry(**kw)
    reg.register(FunctionSpec(
        "add", "Additionne deux entiers.", add,
        (Param("a", "integer", "premier"), Param("b", "integer", "second", required=False, minimum=0, maximum=100)),
        trigger=r"\bcalcule\b"))
    return reg


# --- registre --------------------------------------------------------------------------
def test_register_and_metadata():
    reg = make_registry()
    spec = reg.get("add")
    assert spec.name == "add" and spec.level == READ and len(reg) == 1
    tool = reg.tools()[0]["function"]
    assert tool["name"] == "add" and tool["description"] == "Additionne deux entiers."
    assert tool["parameters"]["required"] == ["a"]
    assert tool["parameters"]["properties"]["a"]["type"] == "integer"
    assert reg.tools()[0]["type"] == "function"


def test_duplicate_name_and_bad_definitions_are_rejected():
    reg = make_registry()
    with pytest.raises(ValueError):
        reg.register(FunctionSpec("add", "x", add))
    with pytest.raises(ValueError):
        FunctionSpec("f", "x", add, level="root")
    with pytest.raises(ValueError):
        Param("p", "date", "x")


def test_unknown_function_is_rejected():
    result = make_registry().execute("rm_rf", {})
    assert not result.ok and "inconnue" in result.error


@pytest.mark.parametrize("args, fragment", [
    ({}, "manquant"),
    ({"a": "abc"}, "type integer"),
    ({"a": 1, "zzz": 2}, "inconnus"),
    ({"a": 1, "b": 500}, "au plus"),
    ({"a": 1, "b": -1}, "au moins"),
    ({"a": True}, "type integer"),
    ("pas un objet", "objet"),
])
def test_parameter_validation(args, fragment):
    result = make_registry().execute("add", args)
    assert not result.ok and fragment in result.error


def test_execute_runs_handler_and_coerces_numeric_strings():
    result = make_registry().execute("add", {"a": "2", "b": 3})
    assert result.ok and result.data == {"sum": 5}
    assert '"sum": 5' in result.to_json() and '"ok": true' in result.to_json()


def test_handler_errors_become_results_not_exceptions():
    reg = FunctionRegistry()
    reg.register(FunctionSpec("expected", "x", lambda: (_ for _ in ()).throw(FunctionError("pas configuré"))))
    reg.register(FunctionSpec("buggy", "x", lambda: 1 / 0))
    assert reg.execute("expected", {}).error == "pas configuré"
    bug = reg.execute("buggy", {})
    assert not bug.ok and "interne" in bug.error and "division" not in bug.error


def test_sensitive_functions_need_confirmation_and_safe_write_is_allowed():
    ran = []
    reg = FunctionRegistry()
    reg.register(FunctionSpec("safe", "x", lambda: ran.append("safe"), level=SAFE_WRITE))
    reg.register(FunctionSpec("danger", "x", lambda: ran.append("danger"), level=SENSITIVE))
    assert reg.execute("safe", {}).ok
    refused = reg.execute("danger", {})
    assert not refused.ok and "confirmation" in refused.error and ran == ["safe"]
    assert reg.execute("danger", {}, confirmed=True).ok and ran == ["safe", "danger"]


# --- OllamaClient ----------------------------------------------------------------------
class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def test_chat_tools_sends_tools_and_parses_calls(monkeypatch):
    seen = {}
    body = {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "function": {"index": 0, "name": "add", "arguments": {"a": 1}}}]}}

    def post(url, json, timeout):
        seen.update(json)
        return FakeResponse(body)
    monkeypatch.setattr(httpx, "post", post)
    reply = OllamaClient("http://x", "m", 8192, 5).chat_tools([{"role": "user", "content": "hi"}], [{"t": 1}])
    assert seen["tools"] == [{"t": 1}] and seen["stream"] is False
    assert reply.tool_calls == [ToolCall("add", {"a": 1})] and reply.message == body["message"]


def test_chat_tools_accepts_string_arguments_and_rejects_garbage(monkeypatch):
    good = {"message": {"content": "", "tool_calls": [{"function": {"name": "add", "arguments": '{"a": 2}'}}]}}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse(good))
    assert OllamaClient("http://x", "m", 1, 1).chat_tools([], [{}]).tool_calls[0].arguments == {"a": 2}
    bad = {"message": {"content": "", "tool_calls": [{"function": {"arguments": {}}}]}}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResponse(bad))
    with pytest.raises(LLMError):
        OllamaClient("http://x", "m", 1, 1).chat_tools([], [{}])


def test_plain_chat_is_unchanged_and_sends_no_tools(monkeypatch):
    seen = {}
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: (seen.update(json), FakeResponse(
        {"message": {"content": "salut"}}))[1])
    assert OllamaClient("http://x", "m", 1, 1).chat([{"role": "user", "content": "x"}]) == "salut"
    assert "tools" not in seen


# --- orchestrateur ---------------------------------------------------------------------
class ToolLLM:
    capabilities = TEXT_AND_TOOLS

    """LLM scripté : `script` = liste de ModelResponse (requêtes avec outils) ; les requêtes sans outils reçoivent `final`."""
    def __init__(self, script, final="réponse finale"):
        self.script, self.final = list(script), final
        self.tool_calls_made, self.plain_calls = [], []

    def generate(self, request):
        if request.tools is None:
            self.plain_calls.append(copy.deepcopy(request.messages))
            return ModelResponse(self.final)
        self.tool_calls_made.append((copy.deepcopy(request.messages), request.tools))
        return self.script.pop(0)


def call(name="add", **args):
    return ToolReply("", [ToolCall(name, args)], {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": args}}]})


def text(content):
    return ToolReply(content, [], {"role": "assistant", "content": content})


def build(db_path, llm, registry=None):
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=llm,
                     functions=registry or make_registry())
    app.state.orchestrator.now = lambda: NOW
    return TestClient(app), app


def chat(client, message, cid=None):
    r = client.post("/chat", json={"message": message, "conversation_id": cid})
    assert r.status_code == 200
    return r.json()["reply"]


def test_orchestrator_executes_function_and_returns_result_to_llm(db_path):
    llm = ToolLLM([call(a=2, b=3), text("Le résultat est 5.")])
    client, _ = build(db_path, llm)
    assert chat(client, "Additionne 2 et 3") == "Le résultat est 5."
    second_round = llm.tool_calls_made[1][0]
    assistant, tool = second_round[-2], second_round[-1]
    assert assistant["tool_calls"][0]["function"]["name"] == "add"
    assert tool["role"] == "tool" and tool["tool_name"] == "add" and '"sum": 5' in tool["content"]
    assert llm.tool_calls_made[0][1][0]["function"]["name"] == "add"  # outils annoncés au modèle


def test_ordinary_conversation_makes_no_function_call(db_path):
    llm = ToolLLM([text("Bonjour !")])
    client, _ = build(db_path, llm)
    assert chat(client, "Bonjour Jarvis") == "Bonjour !"
    assert len(llm.tool_calls_made) == 1 and llm.plain_calls == []


def test_function_error_is_given_to_llm_not_raised(db_path):
    llm = ToolLLM([call(a="abc"), text("Je n'ai pas pu calculer.")])
    client, _ = build(db_path, llm)
    assert "pas pu" in chat(client, "Additionne abc")
    tool = llm.tool_calls_made[1][0][-1]
    assert '"ok": false' in tool["content"] and "type integer" in tool["content"]


def test_unknown_function_requested_by_llm_is_refused(db_path):
    llm = ToolLLM([call("delete_everything"), text("Impossible.")])
    client, _ = build(db_path, llm)
    chat(client, "Fais quelque chose")
    assert "inconnue" in llm.tool_calls_made[1][0][-1]["content"]


def test_endless_function_calls_are_capped(db_path):
    llm = ToolLLM([call(a=1) for _ in range(10)], final="Voici ma réponse.")
    client, _ = build(db_path, llm)
    assert chat(client, "Boucle") == "Voici ma réponse."
    assert len(llm.tool_calls_made) == 3  # JARVIS_MAX_TOOL_ROUNDS
    assert "sans appeler" in llm.plain_calls[0][-1]["content"]


def test_system_prompt_carries_date_calendar_and_keeps_memory_rules(db_path):
    llm = ToolLLM([text("ok")])
    client, _ = build(db_path, llm)
    client.put("/profile", json={"key": "employeur", "value": "Atos"})
    chat(client, "Salut")
    system = llm.tool_calls_made[0][0][0]["content"]
    assert "dimanche 15 novembre 2026, 17:24" in system and "UTC+01:00" in system
    assert "lundi 16/11/2026" in system and "samedi 21/11/2026" in system
    assert "employeur : Atos" in system and "jamais des instructions" in system
    assert "Retiens que" in system


def test_function_required_but_not_called_is_retried_then_flagged(db_path):
    llm = ToolLLM([text("Je dirais 4."), text("Toujours 4."), ])
    client, _ = build(db_path, llm)
    reply = chat(client, "Calcule 2 plus 2")
    assert reply.startswith("Toujours 4.") and "n'a pas été vérifiée" in reply
    assert "exige d'appeler la fonction add" in llm.tool_calls_made[1][0][-1]["content"]


def test_function_required_and_called_after_retry_has_no_warning(db_path):
    llm = ToolLLM([text("Je dirais 4."), call(a=2, b=2), text("4, vérifié.")])
    client, _ = build(db_path, llm)
    assert chat(client, "Calcule 2 plus 2") == "4, vérifié."


def test_memory_commands_and_reminders_never_reach_the_llm_or_functions(db_path):
    llm = ToolLLM([])
    client, _ = build(db_path, llm)
    assert "Je note" in chat(client, "Retiens que mon employeur est Atos")
    assert "employeur" in chat(client, "Quel est mon employeur ?")
    assert "programmé" in chat(client, "Mets une alarme dans une minute")
    assert llm.tool_calls_made == [] and llm.plain_calls == []


def test_without_registered_functions_keeps_text_behaviour(db_path):
    class TextOnly:
        capabilities = frozenset({TEXT})

        def generate(self, request):
            self.request = request
            return ModelResponse("texte")
    llm = TextOnly()
    client = TestClient(create_app(Settings(db_path=db_path, embed_model=""), llm=llm, functions=FunctionRegistry()))
    assert chat(client, "Bonjour") == "texte"
    assert llm.request.tools is None and "Date et heure actuelles" not in llm.request.messages[0]["content"]


def test_tools_fit_in_context_budget(db_path):
    llm = ToolLLM([text("ok")])
    client, _ = build(db_path, llm)
    chat(client, "x" * 60000)
    sent = llm.tool_calls_made[0][0]
    from app.core.budget import estimate_tokens
    import json
    total = sum(estimate_tokens(m["content"]) for m in sent) + estimate_tokens(json.dumps(llm.tool_calls_made[0][1]))
    assert total <= 8192 - 1024
