"""Délégation story_generate -> mini-llm (modèle de continuation) : garde, fonction, adaptateur, intégration.
Aucun test ne charge torch ni le vrai modèle, sauf test_real_minillm (ignoré si mini-llm est absent)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.function_registry import FunctionRegistry
from app.core.minillm_adapter import MiniLLMAdapter
from app.core.model_adapter import TEXT, TEXT_AND_TOOLS, LLMError, ModelRequest, ModelResponse, ToolCall
from app.core.model_registry import ModelRegistry, ModelSpec
from app.core.model_router import ModelRouter
from app.functions import story
from app.main import create_app

PRIMER = "Au cœur d'une forêt oubliée vivait un jeune chevalier qui rêvait des étoiles."


class Continuer:
    capabilities = frozenset({TEXT})

    def __init__(self, text=" Une lumière apparut."):
        self.text, self.requests = text, []

    def generate(self, request):
        self.requests.append(request)
        return ModelResponse(self.text, [], {"role": "assistant", "content": self.text})


class Qwen:
    capabilities = TEXT_AND_TOOLS

    def __init__(self, script):
        self.script, self.requests = list(script), []

    def generate(self, request):
        self.requests.append(request)
        if request.tools and self.script:
            return self.script.pop(0)
        return ModelResponse("Voici une expérience de génération.", [], {"role": "assistant", "content": "ok"})


def story_call(primer=PRIMER, **extra):
    args = {"primer": primer, **extra}
    return ModelResponse("", [ToolCall("story_generate", args)], {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "story_generate", "arguments": args}}]})


def build_registry(specialist=None, enabled=True):
    models = ModelRegistry()
    models.register(ModelSpec("qwen", Qwen([])))
    if specialist is not None:
        models.register(ModelSpec("mini-llm", specialist, "specialist", story.SPECIALTY, enabled=enabled))
    fns = FunctionRegistry()
    story.register(fns, ModelRouter(models, fallback="error"))
    return fns


# --- garde d'intention ---------------------------------------------------------------------
@pytest.mark.parametrize("message", [
    "Raconte-moi une histoire sur un chevalier qui découvre une planète.",
    "raconte moi une petite histoire de dragon",
    "Jarvis, écris une histoire de pirates",
    "Invente un conte pour enfants sur un renard",
    "Peux-tu me raconter une histoire sur la mer ?",
    "S'il te plaît, rédige un récit sur un phare",
    "Raconte-moi une histoire sur un chevalier qui découvre une planète inconnue",
    "Écris une histoire fantastique sur une cité engloutie",
    "Raconte-moi une histoire sur un marin qui se demande si la mer est vivante.",
    "Raconte-moi une histoire sur un jour de pluie à Paris.",
    "Raconte-moi une histoire de pirates, pour mes enfants, avant le dîner.",
    "Raconte-moi une histoire sur un roi qui ne trouve pas son trône.",
])
def test_guard_accepts_direct_orders(message):
    assert story.is_explicit_story_request(message)


@pytest.mark.parametrize("message", [
    "Est-ce que tu sais raconter une histoire ?",
    "Pourquoi raconter une histoire ?",
    "Raconte-moi une histoire ?",
    "Ne me raconte pas d'histoire.",
    "Je ne veux pas que tu racontes une histoire.",
    "Si tu veux, raconte-moi une histoire.",
    "Raconte-moi une histoire si besoin.",
    "Si besoin, raconte une histoire.",
    "Raconte une histoire. Finalement non, laisse tomber.",
    "Raconte-moi une histoire. Non, ne la raconte pas.",
    "Raconte-moi une histoire si tu en as envie",
    "Si le cœur t'en dit, raconte-moi une histoire",
    "Quand tu auras le temps, raconte-moi une histoire",
    "Tu pourrais me raconter une histoire un jour ?",
    "Sais-tu raconter des histoires ?",
    "Es-tu capable d'écrire une histoire ?",
    "Ne m'écris surtout pas d'histoire",
    "Raconte-moi une histoire sur la mer, quand tu veux.",
    "Raconte-moi une histoire, lorsque tu auras fini.",
    "Tu peux me raconter une histoire plus tard ?",
    "Raconte-moi une histoire un de ces jours",
    "Quelle est ta meilleure histoire ?",
    "Résume-moi cette histoire",
    "Raconte-moi ta journée",
    "Quelque chose à propos d'une histoire",
    "",
])
def test_guard_refuses_questions_negations_conditionals_and_other_requests(message):
    assert not story.is_explicit_story_request(message)


def test_guard_refuses_non_string():
    assert not story.is_explicit_story_request(None)


# --- fonction via FunctionRegistry -----------------------------------------------------------
def test_function_returns_primer_continuation_and_experimental_note():
    spec = Continuer()
    fns = build_registry(spec)
    result = fns.execute("story_generate", {"primer": PRIMER, "length": 50}, user_message="Raconte-moi une histoire")
    assert result.ok and result.data["amorce"] == PRIMER and result.data["suite"] == " Une lumière apparut."
    assert "pas suivi la demande" in result.data["note"]
    request = spec.requests[0]
    assert request.messages == [{"role": "user", "content": PRIMER}] and request.options == {"max_tokens": 50}
    assert request.tools is None


def test_function_is_refused_before_the_model_without_explicit_intent():
    spec = Continuer()
    fns = build_registry(spec)
    for message in (None, "Sais-tu raconter une histoire ?", "Ne raconte pas d'histoire"):
        result = fns.execute("story_generate", {"primer": PRIMER}, user_message=message)
        assert not result.ok and "NON exécutée" in result.error
    assert spec.requests == []


@pytest.mark.parametrize("args", [{"primer": "trop court"}, {"primer": "x" * 801}, {"primer": PRIMER, "length": 5},
                                  {"primer": PRIMER, "length": 500}, {}])
def test_function_validates_primer_and_length(args):
    spec = Continuer()
    result = build_registry(spec).execute("story_generate", args, user_message="Raconte une histoire")
    assert not result.ok and spec.requests == []


@pytest.mark.parametrize("kind", ["absent", "disabled"])
def test_missing_or_disabled_model_is_an_explicit_error_without_fallback_to_qwen(kind):
    fns = build_registry(None if kind == "absent" else Continuer(), enabled=kind != "disabled")
    result = fns.execute("story_generate", {"primer": PRIMER}, user_message="Raconte une histoire")
    assert not result.ok and "text-continuation" in result.error


def test_adapter_error_becomes_a_function_error():
    class Broken(Continuer):
        def generate(self, request):
            raise LLMError("boom")

    result = build_registry(Broken()).execute("story_generate", {"primer": PRIMER}, user_message="Raconte une histoire")
    assert not result.ok and "boom" in result.error


# --- adaptateur (sous-processus simulé) --------------------------------------------------------
@pytest.fixture
def adapter(tmp_path):
    files = [tmp_path / n for n in ("python", "bridge.py", "model.pt", "bpe.json")]
    for f in files:
        f.write_text("x")
    return MiniLLMAdapter(str(tmp_path), *(str(f) for f in files), timeout=5)


def fake_run(monkeypatch, stdout="", returncode=0, stderr="", raises=None):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        if raises:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_adapter_declares_text_only_and_rejects_tools(adapter):
    assert adapter.capabilities == frozenset({TEXT})
    with pytest.raises(LLMError, match="appels de fonctions"):
        adapter.generate(ModelRequest([{"role": "user", "content": "x"}], tools=[{"type": "function"}]))


def test_adapter_sends_last_user_message_and_generation_parameters(adapter, monkeypatch):
    calls = fake_run(monkeypatch, json.dumps({"text": " la suite"}))
    reply = adapter.generate(ModelRequest(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "premier"},
         {"role": "assistant", "content": "a"}, {"role": "user", "content": "amorce"}],
        options={"max_tokens": 30, "temperature": 0.5, "top_k": 10, "seed": 7, "ignored": 1}))
    cmd, kw = calls[0]
    payload = json.loads(kw["input"])
    assert cmd == [adapter.python, adapter.bridge] and kw["timeout"] == 5
    assert payload["prompt"] == "amorce" and payload["max_tokens"] == 30 and payload["temperature"] == 0.5
    assert payload["top_k"] == 10 and payload["seed"] == 7 and "ignored" not in payload
    assert payload["checkpoint"] == adapter.checkpoint
    assert reply.content == " la suite" and reply.tool_calls == [] and reply.message["role"] == "assistant"


def test_adapter_defaults(adapter, monkeypatch):
    calls = fake_run(monkeypatch, json.dumps({"text": "t"}))
    adapter.generate(ModelRequest([{"role": "user", "content": "amorce"}]))
    payload = json.loads(calls[0][1]["input"])
    assert payload["max_tokens"] == 120 and payload["temperature"] == 0.8 and payload["top_k"] == 40


@pytest.mark.parametrize("options", [{"max_tokens": 0}, {"max_tokens": 9999}, {"temperature": 0},
                                     {"temperature": "chaud"}])
def test_adapter_rejects_invalid_options(adapter, monkeypatch, options):
    calls = fake_run(monkeypatch, json.dumps({"text": "t"}))
    with pytest.raises(LLMError):
        adapter.generate(ModelRequest([{"role": "user", "content": "amorce"}], options=options))
    assert calls == []


def test_adapter_missing_files_and_empty_prompt(adapter, tmp_path, monkeypatch):
    calls = fake_run(monkeypatch, json.dumps({"text": "t"}))
    with pytest.raises(LLMError, match="amorce vide"):
        adapter.generate(ModelRequest([{"role": "assistant", "content": "x"}]))
    Path(adapter.checkpoint).unlink()
    assert adapter.missing_files() == [adapter.checkpoint]
    with pytest.raises(LLMError, match="introuvable"):
        adapter.generate(ModelRequest([{"role": "user", "content": "amorce"}]))
    assert calls == []


@pytest.mark.parametrize("kwargs, message", [
    ({"raises": subprocess.TimeoutExpired("x", 5)}, "délai dépassé"),
    ({"raises": OSError("nope")}, "lancement impossible"),
    ({"stdout": json.dumps({"error": "KeyError: poids"}), "returncode": 1}, "KeyError: poids"),
    ({"stdout": "pas du json", "returncode": 0}, "échec"),
    ({"stdout": "", "returncode": 2, "stderr": "Traceback torch"}, "Traceback torch"),
    ({"stdout": json.dumps({"text": "  "})}, "vide"),
])
def test_adapter_subprocess_failures_raise_llm_error(adapter, monkeypatch, kwargs, message):
    fake_run(monkeypatch, **kwargs)
    with pytest.raises(LLMError, match=message):
        adapter.generate(ModelRequest([{"role": "user", "content": "amorce"}]))


# --- câblage et intégration orchestrateur ------------------------------------------------------
def test_main_registers_minillm_as_disabled_specialist_when_files_are_missing(db_path, tmp_path):
    settings = Settings(db_path=db_path, embed_model="", minillm_dir=str(tmp_path / "absent"))
    app = create_app(settings, llm=Qwen([]))
    spec = app.state.models.get("mini-llm")
    assert spec.role == "specialist" and spec.specialty == "text-continuation" and not spec.enabled
    assert app.state.models.primary.id == "qwen" and spec.capabilities == frozenset({TEXT})
    result = app.state.functions.execute("story_generate", {"primer": PRIMER}, user_message="Raconte une histoire")
    assert not result.ok and "text-continuation" in result.error  # modèle désactivé : erreur, pas de repli sur Qwen


def test_main_does_not_register_minillm_by_default(db_path):
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=Qwen([]))
    assert [m for m in ("mini-llm",) if m in app.state.models._models] == []


def test_default_functions_include_story_generate_only_when_configured(db_path, tmp_path):
    on = create_app(Settings(db_path=db_path, embed_model="", minillm_dir=str(tmp_path)), llm=Qwen([]))
    off = create_app(Settings(db_path=db_path + "2", embed_model=""), llm=Qwen([]))
    assert on.state.functions.get("story_generate") is not None and off.state.functions.get("story_generate") is None


def chat_app(db_path, qwen, specialist):
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=qwen, functions=FunctionRegistry())
    app.state.models.register(ModelSpec("mini-llm", specialist, "specialist", story.SPECIALTY))
    story.register(app.state.functions, ModelRouter(app.state.models, fallback="error"))
    return TestClient(app)


def test_orchestrator_delegates_and_qwen_presents_the_result(db_path):
    qwen = Qwen([story_call()])
    specialist = Continuer()
    client = chat_app(db_path, qwen, specialist)
    reply = client.post("/chat", json={"message": "Raconte-moi une histoire sur un chevalier."}).json()["reply"]
    assert reply == "Voici une expérience de génération."
    assert specialist.requests[0].messages[0]["content"] == PRIMER
    tool_msg = next(m for r in qwen.requests for m in r.messages if m.get("role") == "tool")
    data = json.loads(tool_msg["content"])
    assert data["ok"] and data["result"]["suite"] == " Une lumière apparut."
    assert all(r.tools is not None or r.require_content for r in qwen.requests)  # un seul modèle : Qwen


def test_orchestrator_refuses_delegation_on_a_question(db_path):
    qwen = Qwen([story_call()])
    specialist = Continuer()
    client = chat_app(db_path, qwen, specialist)
    client.post("/chat", json={"message": "Sais-tu raconter une histoire ?"})
    assert specialist.requests == []
    tool_msg = next(m for r in qwen.requests for m in r.messages if m.get("role") == "tool")
    assert not json.loads(tool_msg["content"])["ok"]


def test_ordinary_conversation_never_touches_the_specialist(db_path):
    qwen, specialist = Qwen([]), Continuer()
    client = chat_app(db_path, qwen, specialist)
    assert client.post("/chat", json={"message": "Bonjour"}).status_code == 200
    assert specialist.requests == []


# --- intégration réelle (nécessite le projet mini-llm voisin) ----------------------------------------
MINILLM = Path(__file__).resolve().parents[2] / "mini-llm"


@pytest.mark.skipif(not (MINILLM / "models/checkpoints/livres_P4_theatre_meilleur.pt").is_file()
                    or not (MINILLM / ".venv/bin/python").is_file(), reason="mini-llm absent")
def test_real_minillm():
    adapter = MiniLLMAdapter(
        str(MINILLM), str(MINILLM / ".venv/bin/python"), str(MINILLM / "models/checkpoints/livres_P4_theatre_meilleur.pt"),
        str(MINILLM / "datasets/tokenized/bpe_4000.json"), str(Path(__file__).resolve().parents[1] / "tools/minillm_bridge.py"),
        timeout=120)
    options = {"max_tokens": 30, "seed": 0}
    reply = adapter.generate(ModelRequest([{"role": "user", "content": "Il était une fois"}], options=options))
    again = adapter.generate(ModelRequest([{"role": "user", "content": "Il était une fois"}], options=options))
    assert reply.content.strip() and reply.content == again.content  # graine fixe : déterministe
    assert not reply.content.startswith("Il était une fois")  # suite seule, amorce non répétée
    with pytest.raises(LLMError):
        MiniLLMAdapter(str(MINILLM), sys.executable, "absent.pt", "absent.json", adapter.bridge).generate(
            ModelRequest([{"role": "user", "content": "x"}]))


# --- description, refus, configuration au démarrage ------------------------------------------------------
def test_description_and_refusal_distinguish_capability_creation_and_prohibition():
    spec = build_registry(Continuer()).get("story_generate")
    for text in ("DEMANDE DIRECTE", "QUESTION", "INTERDICTION", "n'appelle PAS", "indisponible"):
        assert text in spec.description
    assert "pas une panne" in spec.intent_refusal and "sans mentionner de fonction ni d'erreur" in spec.intent_refusal


def minillm_logs(caplog, tmp_path, **files):
    base = tmp_path / "ml"
    (base / "models/checkpoints").mkdir(parents=True, exist_ok=True)
    (base / "datasets/tokenized").mkdir(parents=True, exist_ok=True)
    (base / ".venv/bin").mkdir(parents=True, exist_ok=True)
    for name, path in {"python": ".venv/bin/python", "ckpt": "models/checkpoints/livres_P4_theatre_meilleur.pt",
                       "tok": "datasets/tokenized/bpe_4000.json"}.items():
        if files.get(name, True):
            (base / path).write_text("x")
    settings = Settings(minillm_dir=str(base), minillm_python="", db_path=str(tmp_path / "t.db"), embed_model="")
    with caplog.at_level("INFO", logger="jarvis"):
        create_app(settings, llm=Qwen([]))
    return [r for r in caplog.records if "mini-llm" in r.getMessage()]


def test_startup_log_not_configured(db_path, caplog):
    with caplog.at_level("INFO", logger="jarvis"):
        app = create_app(Settings(db_path=db_path, embed_model="", minillm_dir=""), llm=Qwen([]))
    assert app.state.functions.get("story_generate") is None
    [rec] = [r for r in caplog.records if "mini-llm" in r.getMessage()]
    assert rec.levelname == "INFO" and "non configuré" in rec.getMessage() and "JARVIS_MINILLM_DIR" in rec.getMessage()


def test_startup_log_directory_missing(tmp_path, caplog):
    settings = Settings(minillm_dir=str(tmp_path / "absent"), db_path=str(tmp_path / "t.db"), embed_model="")
    with caplog.at_level("INFO", logger="jarvis"):
        create_app(settings, llm=Qwen([]))
    [rec] = [r for r in caplog.records if "mini-llm" in r.getMessage()]
    assert rec.levelname == "WARNING" and "répertoire" in rec.getMessage() and str(tmp_path) not in rec.getMessage()


def test_startup_log_distinguishes_missing_checkpoint_python_and_ready(tmp_path, caplog):
    [ready] = minillm_logs(caplog, tmp_path)
    assert ready.levelname == "INFO" and "activé" in ready.getMessage() and "livres_P4_theatre_meilleur.pt" in ready.getMessage()
    caplog.clear()
    [no_ckpt] = minillm_logs(caplog, tmp_path / "a", ckpt=False)
    assert no_ckpt.levelname == "WARNING" and "checkpoint introuvable" in no_ckpt.getMessage()
    caplog.clear()
    [no_python] = minillm_logs(caplog, tmp_path / "b", python=False)
    assert "environnement Python introuvable" in no_python.getMessage() and "checkpoint" not in no_python.getMessage()
    assert str(tmp_path) not in no_python.getMessage()


def test_without_minillm_dir_the_tool_is_absent_and_conversation_unchanged(db_path):
    qwen = Qwen([])
    client = TestClient(create_app(Settings(db_path=db_path, embed_model="", minillm_dir=""), llm=qwen))
    assert client.post("/chat", json={"message": "Raconte-moi une histoire sur la mer."}).status_code == 200
    assert not any(t["function"]["name"] == "story_generate" for r in qwen.requests for t in (r.tools or []))


@pytest.mark.parametrize("message", ["Sais-tu raconter des histoires ?", "Ne me raconte pas d'histoire",
                                     "Raconte-moi une histoire si tu en as envie"])
def test_orchestrator_refuses_capability_negative_and_conditional_requests(db_path, message):
    qwen, specialist = Qwen([story_call()]), Continuer()
    chat_app(db_path, qwen, specialist).post("/chat", json={"message": message})
    assert specialist.requests == []
    tool_msg = next(m for r in qwen.requests for m in r.messages if m.get("role") == "tool")
    assert not json.loads(tool_msg["content"])["ok"]
