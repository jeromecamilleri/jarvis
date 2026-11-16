"""Traçabilité : request_id, événements JSONL, corrélation, confidentialité, robustesse, CLI, rotation."""
import json
import logging
import threading

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core import tracing
from app.core.function_registry import FunctionRegistry
from app.core.model_adapter import TEXT, TEXT_AND_TOOLS, LLMError, ModelRequest, ModelResponse
from app.core.model_registry import ModelRegistry, ModelSpec
from app.core.model_router import ModelRouter, NoCompatibleModelError, RoutingRequest
from app.functions import story
from app.main import create_app
from tests.test_story import PRIMER, Continuer, Qwen, story_call


@pytest.fixture
def trace_file(tmp_path):
    path = tmp_path / "logs" / "trace.jsonl"
    assert tracing.configure(str(path), 1_000_000, 3)
    yield path
    tracing.disable()


def events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def names(evts):
    return [e["event"] for e in evts]


def traced_app(db_path, qwen, specialist):
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=qwen, functions=FunctionRegistry())
    app.state.models.register(ModelSpec("mini-llm", tracing.TracedAdapter(specialist, "mini-llm", "specialist"),
                                        "specialist", story.SPECIALTY))
    story.register(app.state.functions, ModelRouter(app.state.models, fallback="error"))
    return TestClient(app)


# --- request_id ---------------------------------------------------------------------------------
def test_request_id_is_generated_unique_and_reset_after_success_and_error():
    seen = []
    for _ in range(2):
        with tracing.request_scope():
            seen.append(tracing.current_request_id())
        assert tracing.current_request_id() is None
    with pytest.raises(ValueError):
        with tracing.request_scope():
            seen.append(tracing.current_request_id())
            raise ValueError("boom")
    assert tracing.current_request_id() is None
    assert len(set(seen)) == 3 and all(seen)


def test_nested_scope_reuses_id_and_threads_do_not_share_it(trace_file):
    with tracing.request_scope():
        outer = tracing.current_request_id()
        with tracing.request_scope():
            assert tracing.current_request_id() == outer
        other = []
        t = threading.Thread(target=lambda: other.append(tracing.current_request_id()))
        t.start()
        t.join()
        assert other == [None]
    assert names(events(trace_file)).count("request.started") == 1


def test_events_outside_a_request_have_null_request_id(trace_file):
    tracing.emit("scheduler.tick", "scheduler")
    assert events(trace_file)[0]["request_id"] is None


# --- événements, durées ---------------------------------------------------------------------------
def test_events_have_core_fields_ordered_seq_and_monotonic_durations(trace_file, monkeypatch):
    ticks = iter([10.0, 10.5, 11.0, 12.0])
    monkeypatch.setattr(tracing.time, "perf_counter", lambda: next(ticks))
    with tracing.request_scope(message_chars=3):
        with tracing.span("adapter", "x"):
            pass
    evts = events(trace_file)
    assert names(evts) == ["request.started", "adapter.started", "adapter.completed", "request.completed"]
    assert [e["seq"] for e in evts] == [1, 2, 3, 4]
    assert len({e["request_id"] for e in evts}) == 1
    assert all({"ts", "seq", "request_id", "event", "component", "status"} <= e.keys() for e in evts)
    assert evts[2]["duration_ms"] == 500.0 and evts[3]["duration_ms"] == 2000.0


def test_span_reraises_the_original_exception_and_traces_failure(trace_file):
    err = RuntimeError("fichier /home/jerome/secret/doc.pdf illisible")
    with pytest.raises(RuntimeError) as exc:
        with tracing.span("adapter", "x"):
            raise err
    assert exc.value is err
    failed = events(trace_file)[-1]
    assert failed["event"] == "adapter.failed" and failed["error"] == "RuntimeError"
    assert "/home/jerome" not in failed["detail"]


# --- TracedAdapter --------------------------------------------------------------------------------
class Plain:
    capabilities = frozenset({TEXT})
    marker = "m"

    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def generate(self, request):
        if self.error:
            raise self.error
        return self.result


def test_traced_adapter_is_transparent(trace_file):
    response = ModelResponse("ok", [], {"role": "assistant", "content": "ok"})
    inner = Plain(response)
    wrapped = tracing.TracedAdapter(inner, "m1", "specialist")
    assert wrapped.capabilities is inner.capabilities and wrapped.marker == "m"
    assert wrapped.generate(ModelRequest(messages=[{"role": "user", "content": "x"}])) is response
    failure = LLMError("panne")
    with pytest.raises(LLMError) as exc:
        tracing.TracedAdapter(Plain(error=failure), "m1", "specialist").generate(ModelRequest(messages=[]))
    assert exc.value is failure
    started = events(trace_file)[0]
    assert (started["model_id"], started["role"], started["adapter"]) == ("m1", "specialist", "Plain")


# --- corrélation ----------------------------------------------------------------------------------
def test_request_correlates_qwen_function_and_specialist(db_path, trace_file):
    client = traced_app(db_path, Qwen([story_call()]), Continuer())
    assert client.post("/chat", json={"message": "Raconte-moi une histoire sur un chevalier."}).status_code == 200
    evts = events(trace_file)
    assert len({e["request_id"] for e in evts}) == 1
    assert [e["seq"] for e in evts] == sorted(e["seq"] for e in evts)
    gens = [(e["model_id"], e["role"]) for e in evts if e["event"] == "model.generation.completed"]
    assert gens == [("qwen", "primary"), ("mini-llm", "specialist"), ("qwen", "primary")]
    order = names(evts)
    assert order.index("function.called") < order.index("intent_guard.accepted") < order.index("function.completed")
    inner = [e for e in evts if e.get("model_id") == "mini-llm"]
    assert inner and all(e["via_function"] == "story_generate" for e in inner)
    assert next(e for e in evts if e["event"] == "model.selected" and e["role"] == "specialist")[
        "reason"] == "specialist_match"
    assert evts[0]["event"] == "request.started" and evts[-1]["event"] == "request.completed"
    assert evts[-1]["path"] == "llm"


def test_guard_rejection_is_traced_and_specialist_not_called(db_path, trace_file):
    specialist = Continuer()
    client = traced_app(db_path, Qwen([story_call()]), specialist)
    client.post("/chat", json={"message": "Sais-tu raconter une histoire ?"})
    evts = events(trace_file)
    rejected = next(e for e in evts if e["event"] == "intent_guard.rejected")
    assert rejected["status"] == "rejected" and rejected["cause"] == "guard_false"
    assert next(e for e in evts if e["event"] == "function.failed")["kind"] == "intent_refused"
    assert specialist.requests == [] and "model.selected" not in [
        e["event"] for e in evts if e.get("specialty") == story.SPECIALTY]


def test_adapter_error_is_propagated_and_traced(db_path, trace_file):
    class Broken:
        capabilities = TEXT_AND_TOOLS

        def generate(self, request):
            raise LLMError("modèle indisponible")

    app = create_app(Settings(db_path=db_path, embed_model=""), llm=Broken(), functions=FunctionRegistry())
    r = TestClient(app).post("/chat", json={"message": "Bonjour"})
    assert r.status_code == 502
    evts = names(events(trace_file))
    assert "model.generation.failed" in evts and evts[-1] == "request.failed"


# --- routeur ----------------------------------------------------------------------------------------
def test_router_traces_fallback_reason_and_failure(trace_file):
    models = ModelRegistry()
    models.register(ModelSpec("qwen", Plain()))
    ModelRouter(models, fallback="primary").select(RoutingRequest(frozenset({TEXT}), "text-continuation"))
    with pytest.raises(NoCompatibleModelError):
        ModelRouter(models, fallback="error").select(RoutingRequest(frozenset({TEXT}), "text-continuation"))
    selected, failed = events(trace_file)
    assert selected["event"] == "model.selected" and selected["fallback"] is True
    assert selected["reason"] == "no_specialist_registered" and selected["model_id"] == "qwen"
    assert failed["event"] == "model.selection.failed" and failed["reason"] == "no_specialist_registered"


# --- confidentialité --------------------------------------------------------------------------------
def test_no_sensitive_content_in_trace(db_path, trace_file):
    secret = "SECRET-MESSAGE-4242"

    class Leaky(Continuer):
        def generate(self, request):
            raise LLMError("échec sur /home/jerome/docs/confidentiel.pdf SECRET-ERREUR")

    client = traced_app(db_path, Qwen([story_call(primer=PRIMER + " AMORCE-PRIVEE")]), Leaky())
    client.post("/chat", json={"message": f"Raconte-moi une histoire sur {secret}"})
    raw = trace_file.read_text(encoding="utf-8")
    for forbidden in (secret, "AMORCE-PRIVEE", "/home/jerome", "confidentiel.pdf", "Voici une expérience"):
        assert forbidden not in raw


def test_function_arguments_text_is_never_recorded(trace_file):
    fns = FunctionRegistry()
    from app.core.function_registry import FunctionSpec, Param
    fns.register(FunctionSpec(name="docs_demo", description="d", handler=lambda query, k=1: {"ok": 1},
                              params=(Param("query", "string", "q"), Param("k", "integer", "k", required=False)),
                              log_args=("query", "k")))
    assert fns.execute("docs_demo", {"query": "texte-sensible", "k": 3}).ok
    raw = trace_file.read_text(encoding="utf-8")
    assert "texte-sensible" not in raw and '"k": 3' in raw


# --- robustesse -------------------------------------------------------------------------------------
def test_unwritable_path_disables_tracing_without_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert tracing.configure(str(blocker / "sub" / "trace.jsonl")) is False
    tracing.emit("a", "b")
    with tracing.request_scope():
        pass


def test_logger_failures_never_break_a_request(db_path, trace_file, monkeypatch):
    def boom(*a, **k):
        raise OSError("disque plein")

    monkeypatch.setattr(tracing._logger, "info", boom)
    client = traced_app(db_path, Qwen([story_call()]), Continuer())
    r = client.post("/chat", json={"message": "Raconte-moi une histoire sur la mer."})
    assert r.status_code == 200 and r.json()["reply"] == "Voici une expérience de génération."


def test_handler_write_error_disables_tracing(trace_file, caplog):
    tracing._handler.handleError(logging.makeLogRecord({}))
    assert not tracing.enabled()
    tracing.emit("a", "b")


# --- rotation et CLI ----------------------------------------------------------------------------------
def test_rotation_and_cli_read_rotated_files(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"
    assert tracing.configure(str(path), max_bytes=600, backups=50)
    try:
        with tracing.request_scope(message_chars=1):
            rid = tracing.current_request_id()
            for i in range(12):
                tracing.emit("model.selected", "router", model_id=f"m{i}", role="primary", reason="no_specialty")
    finally:
        tracing.disable()
    assert (tmp_path / "trace.jsonl.1").exists()
    assert tracing.main(["--path", str(path), "show", rid[:6]]) == 0
    out = capsys.readouterr().out
    assert "request.started" in out and "request.completed" in out and "model_id=m0" in out and "model_id=m11" in out
    assert tracing.main(["--path", str(path), "last"]) == 0
    assert rid in capsys.readouterr().out
    assert tracing.main(["--path", str(path), "show", "inconnu"]) == 1
