import sqlite3

import pytest

from app.core.llm import LLMError
from app.core.triggers import is_past_reference
from app.memory.database import connect
from app.memory.episodic import EpisodicMemory, fts_query

CONCEPTS = [("kubernetes", "k8s", "cluster"), ("modele", "qwen", "llm"), ("pomme", "fruit")]


class FakeEmbedder:
    """Un axe par concept : « k8s » est proche de « Kubernetes » sans mot commun."""

    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise LLMError("ollama down")
        out = []
        for t in texts:
            low = t.lower()
            out.append([1.0 if any(w in low for w in group) else 0.0 for group in CONCEPTS])
        return out


def say(client, message, cid=None):
    body = {"message": message}
    if cid:
        body["conversation_id"] = cid
    return client.post("/chat", json=body).json()


def system_of(llm):
    return llm.calls[-1][0]["content"]


# --- déclencheur
@pytest.mark.parametrize("msg", [
    "Qu'avons-nous décidé la semaine dernière concernant Kubernetes ?",
    "Quel modèle avions-nous choisi pour notre assistant ?",
    "Tu te souviens de ce que je t'avais dit ?",
    "On a décidé quoi hier ?",
])
def test_past_reference_detected(msg):
    assert is_past_reference(msg)


@pytest.mark.parametrize("msg", ["Bonjour", "Quelle est la capitale de la France ?",
                                 "Quel est mon employeur ?", "Retiens que mon employeur est Atos"])
def test_ordinary_messages_not_past_reference(msg):
    assert not is_past_reference(msg)


def test_fts_query_drops_stopwords_and_quotes():
    q = fts_query("Qu'avons-nous décidé concernant Kubernetes ?")
    assert '"kubernet"*' in q and "avons" not in q and 'nous' not in q
    assert fts_query("le la ?") is None


# --- service via l'API
def test_old_exchange_found_with_citation_even_outside_history_window(make_client):
    client, llm = make_client()
    cid = say(client, "Nous choisissons Kubernetes pour l'orchestration du projet")["conversation_id"]
    for i in range(25):  # fait sortir l'échange de la fenêtre de 20 messages
        say(client, f"bavardage numéro {i}", cid)
    r = say(client, "Qu'avons-nous décidé concernant Kubernetes ?", cid)
    system = system_of(llm)
    assert "Souvenirs de conversations passées" in system
    assert f"[conversation {cid}," in system and "orchestration du projet" in system
    assert f"Sources : conversation {cid} (" in r["reply"]


def test_recent_messages_are_not_duplicated_as_memories(make_client):
    client, llm = make_client()
    cid = say(client, "Nous utilisons Kubernetes")["conversation_id"]
    say(client, "Qu'avons-nous dit sur Kubernetes ?", cid)
    assert "Nous utilisons Kubernetes" not in system_of(llm).split("Souvenirs")[-1]


def test_other_conversation_is_found_and_cited(make_client):
    client, llm = make_client()
    old = say(client, "Le modèle choisi est Qwen 3 8B")["conversation_id"]
    new = say(client, "Quel modèle avions-nous choisi ?")
    assert new["conversation_id"] != old
    assert "Qwen 3 8B" in system_of(llm)
    assert f"conversation {old}" in new["reply"]


def test_no_memory_found_is_stated_without_sources(make_client):
    client, llm = make_client()
    r = say(client, "Qu'avons-nous décidé concernant Kubernetes ?")
    assert "aucun souvenir pertinent" in system_of(llm)
    assert "Sources" not in r["reply"]


def test_ordinary_message_does_not_search_episodic_memory(make_client):
    client, llm = make_client()
    say(client, "Nous utilisons Kubernetes")
    say(client, "Parle-moi de Kubernetes")
    assert "Souvenirs" not in system_of(llm) and "aucun souvenir" not in system_of(llm)


def test_commands_and_recall_questions_not_indexed(make_client):
    client, _ = make_client()
    say(client, "Retiens que mon employeur est Atos")
    say(client, "Quel est mon employeur ?")
    say(client, "Qu'avons-nous décidé hier ?")
    assert client.get("/memory/search", params={"q": "employeur Atos"}).json() == []
    assert client.get("/memory/search", params={"q": "décidé hier"}).json() == []


def test_statement_with_past_wording_is_indexed(make_client):
    client, _ = make_client()
    say(client, "Pour l'assistant, nous avons choisi le modèle Qwen 3 8B.")
    hits = client.get("/memory/search", params={"q": "modèle assistant"}).json()
    assert len(hits) == 1


def test_episodic_is_separate_from_profile(make_client):
    client, llm = make_client()
    say(client, "Nous utilisons Kubernetes")
    say(client, "Qu'avons-nous décidé concernant Kubernetes ?")
    assert client.get("/profile").json() == {}


def test_episodic_memory_survives_restart(make_client):
    c1, _ = make_client()
    say(c1, "Nous utilisons Kubernetes")
    c2, llm = make_client()
    say(c2, "Qu'avons-nous décidé concernant Kubernetes ?")
    assert "Nous utilisons Kubernetes" in system_of(llm)


def test_profile_still_injected_with_episodic(make_client):
    client, llm = make_client()
    say(client, "Retiens que mon employeur est Atos")
    say(client, "Nous utilisons Kubernetes")
    say(client, "Qu'avons-nous décidé concernant Kubernetes ?")
    assert "employeur : Atos" in system_of(llm) and "Nous utilisons Kubernetes" in system_of(llm)


# --- embeddings
def test_semantic_search_finds_reformulation(make_client):
    client, _ = make_client(embedder=FakeEmbedder())
    say(client, "Nous déployons sur Kubernetes")
    say(client, "J'aime manger une pomme")
    hits = client.get("/memory/search", params={"q": "ton avis sur k8s ?"}).json()
    assert len(hits) == 1 and "Kubernetes" in hits[0]["text"]


def test_semantic_threshold_rejects_unrelated(make_client):
    client, _ = make_client(embedder=FakeEmbedder())
    say(client, "Nous déployons sur Kubernetes")
    assert client.get("/memory/search", params={"q": "quel temps fait-il"}).json() == []


def test_embedder_failure_falls_back_to_fts_and_retries(make_client):
    emb = FakeEmbedder(fail=True)
    client, _ = make_client(embedder=emb)
    say(client, "Nous déployons sur Kubernetes")
    assert client.get("/memory/search", params={"q": "Kubernetes"}).json()
    emb.fail = False
    assert client.get("/memory/search", params={"q": "k8s"}).json()


# --- données existantes
def test_backfill_indexes_existing_exchanges_without_touching_them(tmp_path):
    path = str(tmp_path / "old.db")
    conn = connect(path)
    conn.execute("INSERT INTO conversations DEFAULT VALUES")
    rows = [("user", "Nous utilisons Kubernetes", "chat"), ("assistant", "Noté", "chat"),
            ("user", "Retiens que mon employeur est Atos", "command"),
            ("assistant", "Je note : employeur = Atos.", "command")]
    for role, content, kind in rows:
        conn.execute("INSERT INTO messages (conversation_id, role, content, kind) "
                     "VALUES (1, ?, ?, ?)", (role, content, kind))
    conn.commit()
    mem = EpisodicMemory(conn)
    assert mem.backfill() == 1 and mem.backfill() == 0
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 4
    assert mem.search("Kubernetes")[0]["conversation_id"] == 1
    assert mem.search("Atos") == []
