"""Kensaku MCP : client minimal, fonctions docs_search / docs_read, cycle de vie (faux serveur, aucun index réel)."""
import inspect
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.functions.kensaku as ks
from app.config import Settings
from app.core.function_registry import READ, SAFE_WRITE, FunctionError, FunctionRegistry
from app.core.llm import ToolCall, ToolReply
from app.main import create_app

FAKE = str(Path(__file__).parent / "fake_kensaku_server.py")
ROOT = "/data/docs"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_MARK", str(tmp_path / "died"))

    def make(mode="ok", timeout=5.0):
        monkeypatch.setenv("FAKE_MODE", mode)
        client = ks.KensakuClient([sys.executable, FAKE], timeout)
        make.clients.append(client)
        return client, ks.Kensaku(client)
    make.clients, make.log = [], log
    yield make
    for c in make.clients:
        c.close()


def calls(fake):
    return fake.log.read_text().splitlines() if fake.log.exists() else []


# --- démarrage, protocole ---------------------------------------------------------------
def test_handshake_initialises_session_and_reads_roots(fake):
    client, _ = fake()
    client.start()
    assert client.alive() and client.roots == [ROOT]
    assert calls(fake)[0] == "initialized" and calls(fake)[1].startswith("index_state")


def test_missing_binary_gives_clear_error(tmp_path):
    client = ks.KensakuClient([str(tmp_path / "absent")], 2)
    with pytest.raises(FunctionError, match="indisponible.*introuvable"):
        client.start()
    with pytest.raises(FunctionError, match="indisponible"):
        client.call_tool("search", {"query": "x"})


def test_close_terminates_process(fake):
    client, _ = fake()
    client.start()
    proc = client._proc
    client.close()
    assert proc.poll() is not None and not client.alive()
    client.close()  # idempotent


def test_single_persistent_process_across_calls(fake):
    client, kensaku = fake()
    client.start()
    pid = client._proc.pid
    kensaku.docs_search("a")
    kensaku.docs_search("b")
    assert client._proc.pid == pid and calls(fake).count("initialized") == 1


def test_lazy_start_when_called_before_start(fake):
    _, kensaku = fake()
    assert kensaku.docs_search("a")["count"] == 2


# --- docs_search ------------------------------------------------------------------------
def test_docs_search_returns_paths_scores_types_and_short_snippets(fake):
    _, kensaku = fake()
    out = kensaku.docs_search("mémoire")
    first = out["results"][0]
    assert first == {"path": f"{ROOT}/f0.md", "score": 1.5, "match_type": "Semantic",
                     "snippet": "extrait 0 pour mémoire", "name_match": None}
    assert out["count"] == 2


def test_docs_search_limits_results_and_snippet_size(fake):
    client, kensaku = fake("many")
    out = kensaku.docs_search("x", max_results=50)
    assert out["count"] == 5 and all(len(r["snippet"]) <= 200 for r in out["results"])
    search_call = [c for c in calls(fake) if c.startswith("search")][0]
    assert json.loads(search_call.split(" ", 1)[1])["page_size"] == 10  # reclassement interne


def test_docs_search_enforces_minimum_and_maximum_result_count(fake):
    _, kensaku = fake("many")
    assert kensaku.docs_search("x", max_results=1)["count"] == 3
    assert kensaku.docs_search("x", max_results=0)["count"] == 3
    assert kensaku.docs_search("x", max_results=4)["count"] == 4
    assert kensaku.docs_search("x", max_results=9)["count"] == 5


def test_rerank_prefers_file_name_match_over_tests(fake):
    _, kensaku = fake("rank")
    out = kensaku.docs_search("function registry")["results"]
    assert out[0]["path"].endswith("app/core/function_registry.py") and out[0]["name_match"] == "file"
    assert out[1]["path"].endswith("tests/test_functions.py") and out[1]["name_match"] == "path"
    assert len(out) <= 5


def test_rerank_exact_file_name_wins_and_no_match_keeps_kensaku_order(fake):
    _, kensaku = fake("rank")
    assert kensaku.docs_search("lis function_registry.py")["results"][0]["path"].endswith("function_registry.py")
    out = kensaku.docs_search("météo demain")["results"]
    assert [r["name_match"] for r in out] == [None] * 5
    assert out[0]["path"].endswith("tests/test_functions.py")  # ordre Kensaku conservé


def test_rerank_does_not_boost_name_match_with_off_topic_content(fake):
    _, kensaku = fake("rank")
    out = kensaku.docs_search("function registry old")["results"]
    weak = next(r for r in out if r["path"].endswith("function_registry_old.md"))
    assert weak["name_match"] is None  # tous les mots dans le nom, mais score 0.05 face à 1.0 et sans extrait
    assert not out[0]["path"].endswith("function_registry_old.md")


def test_rerank_off_topic_snippet_does_not_unlock_boost_but_relevant_one_does_ignoring_accents(fake):
    _, kensaku = fake("rank")
    def item(name, score, snippet):
        return {"path": f"{ROOT}/docs/{name}", "score": score, "match_type": "Content", "snippet": snippet}
    out = kensaku._rerank("registry memoire", [
        item("top.md", 1.0, ""), item("registry_cuisine.md", 0.05, "Une recette de cuisine"),
        item("registry_memoire.md", 0.05, "La MÉMOIRE du registre")])
    by = {r["path"].rsplit("/", 1)[1]: r["name_match"] for r in out}
    assert by["registry_cuisine.md"] is None  # extrait sans rapport
    assert by["registry_memoire.md"] == "file"  # « MÉMOIRE » ≡ « memoire »


# --- docs_read et validation des chemins ------------------------------------------------
def test_docs_read_after_search(fake):
    _, kensaku = fake()
    path = kensaku.docs_search("x")["results"][0]["path"]
    out = kensaku.docs_read(path, "mot")
    assert out["text"] == f"passage de {path}" and out["line_start"] == 3
    assert out["query_found"] is False and "ABSENT" in out["note"]
    assert kensaku.docs_read(path, "passage")["query_found"] is True
    assert "query_found" not in kensaku.docs_read(path)
    sent = json.loads([c for c in calls(fake) if c.startswith("read_snippet")][0].split(" ", 1)[1])
    assert sent == {"path": path, "max_tokens": 512, "query": "mot"}


def test_docs_read_caps_tokens_and_text(fake):
    _, kensaku = fake("big_snippet")
    path = kensaku.docs_search("x")["results"][0]["path"]
    out = kensaku.docs_read(path, max_tokens=100000)
    assert len(out["text"]) == 512 * 4 and out["truncated"] is True
    sent = json.loads([c for c in calls(fake) if c.startswith("read_snippet")][0].split(" ", 1)[1])
    assert sent["max_tokens"] == 512


@pytest.mark.parametrize("path, fragment", [
    ("f0.md", "absolu"),
    (f"{ROOT}/../etc/passwd", "absolu"),
    (f"{ROOT}//f0.md", "absolu"),
    ("/etc/passwd", "dossiers indexés"),
    (f"{ROOT}-evil/f0.md", "dossiers indexés"),
    (f"{ROOT}/jamais_vu.md", "docs_search"),
])
def test_docs_read_rejects_unsafe_paths_without_calling_kensaku(fake, path, fragment):
    _, kensaku = fake()
    kensaku.docs_search("x")
    with pytest.raises(FunctionError, match=fragment):
        kensaku.docs_read(path)
    assert not [c for c in calls(fake) if c.startswith("read_snippet")]


def test_docs_read_requires_prior_search_in_registry(fake):
    client, kensaku = fake()
    registry = FunctionRegistry()
    ks.register(registry, Settings(kensaku_enabled=True), client)
    refused = registry.execute("docs_read", {"path": f"{ROOT}/f0.md"})
    assert not refused.ok and "docs_search" in refused.error


# --- erreurs ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode, fragment", [
    ("rpc_error", "refusé la requête"), ("is_error", "signalé une erreur"), ("empty_text", "réponse vide"),
    ("bad_json", "mal formée"), ("garbage", "illisible")])
def test_protocol_and_application_errors(fake, mode, fragment):
    _, kensaku = fake(mode)
    with pytest.raises(FunctionError, match=fragment):
        kensaku.docs_search("x")


def test_timeout_is_reported_and_session_reset(fake):
    client, kensaku = fake("hang", timeout=0.5)
    with pytest.raises(FunctionError, match="délai"):
        kensaku.docs_search("x")
    assert not client.alive()


def test_late_and_notification_messages_are_ignored(fake):
    _, kensaku = fake("noisy")
    assert kensaku.docs_search("x")["count"] == 2


def test_process_death_restarts_once_then_succeeds(fake):
    client, kensaku = fake("die_once")
    client.start()
    assert kensaku.docs_search("x")["count"] == 2
    assert calls(fake).count("initialized") == 2


def test_process_death_twice_gives_error_without_retry_loop(fake):
    client, kensaku = fake("die_always")
    client.start()
    with pytest.raises(FunctionError, match="arrêté"):
        kensaku.docs_search("x")
    assert calls(fake).count("initialized") == 2  # démarrage initial + une seule relance


# --- sécurité du module ---------------------------------------------------------------
def test_only_read_tools_are_ever_called():
    source = inspect.getsource(ks)
    assert "regex_search" not in source and "find_symbol" not in source
    assert "shell=True" not in source


def test_register_exposes_functions_with_right_levels_and_respects_disabled():
    registry = FunctionRegistry()
    assert ks.register(registry, Settings(kensaku_enabled=False)) is None and len(registry) == 0
    client = ks.register(registry, Settings(kensaku_enabled=True, kensaku_cli_path="~/x/kensaku-cli"))
    assert {s.name: s.level for s in registry.specs()} == {
        "docs_search": READ, "docs_read": READ, "docs_index_state": READ, "docs_reindex": SAFE_WRITE}
    assert client.command == [os.path.expanduser("~/x/kensaku-cli"), "mcp"] and "~" not in client.command[0]


# --- intégration Jarvis ----------------------------------------------------------------
class ScriptedLLM:
    def __init__(self, script):
        self.script, self.seen = list(script), []

    def chat_tools(self, messages, tools):
        self.seen.append(json.loads(json.dumps(messages)))
        return self.script.pop(0)

    def chat(self, messages):
        return "réponse forcée"


def tool_call(name, **args):
    return ToolReply("", [ToolCall(name, args)], {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": args}}]})


def test_orchestrator_searches_reads_and_keeps_results_out_of_memory(fake, db_path):
    client, _ = fake()
    registry = FunctionRegistry()
    ks.register(registry, Settings(kensaku_enabled=True), client)
    llm = ScriptedLLM([
        tool_call("docs_search", query="mémoire"),
        tool_call("docs_read", path=f"{ROOT}/f0.md", query="mémoire"),
        ToolReply("La mémoire est décrite dans f0.md.", [], {"role": "assistant", "content": "La mémoire est décrite dans f0.md."})])
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=llm, functions=registry)
    r = TestClient(app).post("/chat", json={"message": "Où est décrite la mémoire ?"})
    assert r.json()["reply"] == "La mémoire est décrite dans f0.md."
    tool_messages = [m for round_ in llm.seen for m in round_ if m["role"] == "tool"]
    assert any("extrait 0 pour mémoire" in m["content"] for m in tool_messages)
    assert any("passage de /data/docs/f0.md" in m["content"] for m in tool_messages)
    db = sqlite3.connect(db_path)
    stored = " ".join(str(v) for t in ("messages", "chunks") for row in db.execute(f"SELECT * FROM {t}") for v in row)
    assert "extrait 0 pour" not in stored and "passage de /data" not in stored


def test_jarvis_lifespan_starts_and_closes_kensaku_process(tmp_path, db_path, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "ok")
    settings = Settings(db_path=db_path, embed_model="", kensaku_enabled=True, kensaku_cli_path=FAKE,
                        google_credentials_path=str(tmp_path / "a"), google_token_path=str(tmp_path / "b"))
    app = create_app(settings, llm=ScriptedLLM([]))
    client = app.state.kensaku
    with TestClient(app) as http:
        assert client.alive() and http.get("/health").status_code == 200
        proc = client._proc
    assert proc.poll() is not None and not client.alive()


def test_jarvis_starts_normally_without_kensaku(tmp_path, db_path, make_client):
    settings = Settings(db_path=db_path, embed_model="", kensaku_enabled=True,
                        kensaku_cli_path=str(tmp_path / "absent"),
                        google_credentials_path=str(tmp_path / "a"), google_token_path=str(tmp_path / "b"))
    app = create_app(settings)
    with TestClient(app) as http:
        assert http.get("/health").status_code == 200
        assert not app.state.kensaku.alive()
        result = app.state.functions.execute("docs_search", {"query": "x"})
        assert not result.ok and "indisponible" in result.error


# --- garde-fou documentaire, journal, exclusion de la mémoire épisodique ----------------
def final(text):
    return ToolReply(text, [], {"role": "assistant", "content": text})


def ask(fake, db_path, script, message, mode="ok"):
    client, _ = fake(mode)
    registry = FunctionRegistry()
    ks.register(registry, Settings(kensaku_enabled=True), client)
    llm = ScriptedLLM(script)
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=llm, functions=registry)
    reply = TestClient(app).post("/chat", json={"message": message}).json()["reply"]
    db = sqlite3.connect(db_path)
    chunks = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    kinds = {row[0] for row in db.execute("SELECT kind FROM messages")}
    return reply, llm, chunks, kinds


def test_guard_no_warning_when_file_really_read_and_turn_stays_out_of_episodic_memory(fake, db_path):
    reply, _, chunks, kinds = ask(fake, db_path, [
        tool_call("docs_search", query="function registry"),
        tool_call("docs_read", path=f"{ROOT}/f0.md", query="passage"),
        final("D'après le texte lu : ...")], "Lis le fichier function registry et explique-moi ses niveaux")
    assert reply == "D'après le texte lu : ..." and chunks == 0 and kinds == {"document"}


def test_episodic_backfill_skips_documentary_turns(fake, db_path):
    ask(fake, db_path, [tool_call("docs_search", query="x"), final("ok")], "Lis le fichier x.md")
    app = create_app(Settings(db_path=db_path, embed_model=""), llm=ScriptedLLM([]))  # redémarrage : rattrapage
    assert app.state.episodic.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0


def test_normal_turn_is_still_indexed(fake, db_path):
    reply, _, chunks, kinds = ask(fake, db_path, [final("Bonjour !")], "Bonjour")
    assert reply == "Bonjour !" and chunks >= 1 and kinds == {"chat"}


def test_guard_warns_and_retries_once_when_llm_answers_without_tools(fake, db_path):
    reply, llm, chunks, kinds = ask(fake, db_path, [final("J'ai lu le fichier, il contient X."), final("Toujours de mémoire.")],
                                "Lis le fichier function registry et explique-le")
    assert reply.startswith("Toujours de mémoire.") and reply.endswith(ks_notice())
    assert any("docs_search" in m["content"] for m in llm.seen[1] if m["role"] == "system")
    assert chunks == 0 and kinds == {"document"}  # demande documentaire : jamais en mémoire épisodique


def ks_notice():
    from app.core.orchestrator import DOC_NOTICE
    return DOC_NOTICE


def test_guard_warns_when_read_fails_or_is_empty(fake, db_path):
    reply, *_ = ask(fake, db_path, [tool_call("docs_read", path=f"{ROOT}/jamais_vu.md"), final("Voici son contenu.")],
                    "Ouvre le fichier notes.md")
    assert reply.endswith(ks_notice())


def test_guard_warns_on_empty_read_text(fake, db_path):
    reply, *_ = ask(fake, db_path, [
        tool_call("docs_search", query="x"), tool_call("docs_read", path=f"{ROOT}/f0.md"), final("Contenu.")],
        "Explique le fichier f0.md", mode="empty_read")
    assert reply.endswith(ks_notice())


def test_guard_warns_when_wrong_file_is_read(fake, db_path):
    reply, *_ = ask(fake, db_path, [
        tool_call("docs_search", query="function_registry.py"), tool_call("docs_read", path=f"{ROOT}/f1.md"),
        final("Voici.")], "Lis le fichier function_registry.py")
    assert reply.endswith(ks_notice())


@pytest.mark.parametrize("message", ["Bonjour", "J'ai perdu un fichier hier", "Lis-moi un poème sur la mer",
                                     "Où est mon fichier de vacances ?"])
def test_guard_does_not_trigger_on_unrelated_messages(fake, db_path, message):
    reply, llm, *_ = ask(fake, db_path, [final("Réponse.")], message)
    assert reply == "Réponse." and len(llm.seen) == 1


def test_function_calls_are_logged_without_document_content(fake, db_path, caplog):
    caplog.set_level("INFO")
    ask(fake, db_path, [tool_call("docs_search", query="mémoire"),
                        tool_call("docs_read", path=f"{ROOT}/f0.md", query="mot"), final("ok")], "Lis le fichier f0.md")
    logs = "\n".join(r.getMessage() for r in caplog.records)
    assert "fonction docs_search" in logs and "fonction docs_read" in logs
    assert "statut=ok" in logs and "durée=" in logs and "mémoire" in logs and "f0.md" in logs
    assert "passage de" not in logs and "extrait 0" not in logs


def test_guard_compares_full_path_when_given_but_only_name_when_name_given(fake, db_path):
    wrong = [tool_call("docs_search", query="x"), tool_call("docs_read", path=f"{ROOT}/f1.md"), final("Voici.")]
    reply, *_ = ask(fake, db_path, wrong, "Lis le fichier other/f1.md")
    assert reply.endswith(ks_notice())  # même nom, autre chemin
    right = [tool_call("docs_search", query="x"), tool_call("docs_read", path=f"{ROOT}/f1.md"), final("Voici.")]
    assert ask(fake, db_path, right, f"Lis le fichier {ROOT}/f1.md")[0] == "Voici."
    assert ask(fake, db_path, right, "Lis le fichier f1.md")[0] == "Voici."


# --- état de l'index et réindexation explicite ------------------------------------------
def tools_called(fake):
    return [c.split(" ", 1)[0] for c in calls(fake)]


def test_docs_index_state_returns_only_useful_fields(fake):
    _, kensaku = fake()
    out = kensaku.docs_index_state()
    assert out["phase"] == "idle" and out["is_indexing"] is False and out["files_indexed"] == 3
    assert set(out) == set(ks.STATE_FIELDS) and "secret_internal" not in out


def test_docs_reindex_checks_state_then_starts(fake):
    _, kensaku = fake()
    assert kensaku.docs_reindex() == {"started": True, "already_running": False, "message": "Réindexation démarrée."}
    tools = tools_called(fake)
    assert tools[-2:] == ["index_state", "reindex"]
    assert json.loads(calls(fake)[-1].split(" ", 1)[1]) == {}


def test_docs_reindex_does_nothing_when_already_indexing(fake):
    _, kensaku = fake("indexing")
    out = kensaku.docs_reindex()
    assert out["started"] is False and out["already_running"] is True and "déjà en cours" in out["message"]
    assert "reindex" not in tools_called(fake)


@pytest.mark.parametrize("mode", ["reindex_refused", "reindex_bad"])
def test_docs_reindex_not_started_or_malformed_is_an_error(fake, mode):
    _, kensaku = fake(mode)
    with pytest.raises(FunctionError, match="pas démarré"):
        kensaku.docs_reindex()


@pytest.mark.parametrize("mode", ["rpc_error", "is_error", "empty_text", "bad_json", "garbage"])
def test_reindex_mcp_errors_become_function_errors(fake, mode):
    _, kensaku = fake(mode)
    kensaku.client.roots = [ROOT]
    with pytest.raises(FunctionError):
        kensaku.docs_reindex()


def test_index_state_mcp_error_becomes_function_error(fake):
    client, kensaku = fake("state_error")
    client.start()
    for call in (kensaku.docs_index_state, kensaku.docs_reindex):
        with pytest.raises(FunctionError, match="refusé"):
            call()
    assert "reindex" not in tools_called(fake)


def test_search_and_read_never_trigger_reindex(fake):
    _, kensaku = fake()
    path = kensaku.docs_search("x")["results"][0]["path"]
    kensaku.docs_read(path, "mot")
    assert "reindex" not in tools_called(fake)


def test_reindex_is_not_called_at_startup(fake):
    client, _ = fake()
    client.start()
    assert "reindex" not in tools_called(fake)


def test_reindex_through_registry_runs_at_safe_write_level(fake):
    client, _ = fake()
    registry = FunctionRegistry()
    ks.register(registry, Settings(kensaku_enabled=True), client)
    assert registry.execute("docs_reindex", {}).data["started"] is True
    assert registry.execute("docs_index_state", {}).data["files"] == 3
