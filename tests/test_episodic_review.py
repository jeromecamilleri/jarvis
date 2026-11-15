"""Revue du sprint 3 : concurrence SQLite, budget de contexte, découpage, pertinence, migrations."""
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.budget import estimate_tokens
from app.main import create_app
from app.memory.database import connect
from app.memory.episodic import EpisodicMemory, split_text
from tests.conftest import FakeLLM
from tests.test_episodic import FakeEmbedder


def say(client, message, cid=None):
    body = {"message": message}
    if cid:
        body["conversation_id"] = cid
    return client.post("/chat", json=body)


def prompt_tokens(messages):
    return sum(estimate_tokens(m["content"]) for m in messages)


def _mem(tmp_path, embedder=None):
    db = connect(str(tmp_path / "m.db"))
    db.execute("INSERT INTO conversations DEFAULT VALUES")
    db.commit()
    return db, EpisodicMemory(db, embedder)


def _exchange(db, user, reply):
    u = db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (1,'user',?)",
                   (user,)).lastrowid
    a = db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (1,'assistant',?)",
                   (reply,)).lastrowid
    db.commit()
    return u, a


# ---------------------------------------------------------------- 1. SQLite
def test_concurrent_chats_do_not_fail(make_client, db_path):
    client, _ = make_client()
    shared = say(client, "Nous utilisons Kubernetes").json()["conversation_id"]
    statuses = []

    def worker(i):
        try:
            statuses.append(say(client, f"Nous utilisons sujet{i}",
                                shared if i % 2 else None).status_code)
        except Exception as e:  # noqa: BLE001
            statuses.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert statuses == [200] * 40
    db = sqlite3.connect(db_path)
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 82
    assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 41
    assert db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 41
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_concurrent_profile_and_history_writes(make_client):
    client, _ = make_client()
    errors = []

    def worker(i):
        try:
            assert say(client, f"Retiens que ma clé{i} est v{i}").status_code == 200
            assert say(client, f"bonjour {i}").status_code == 200
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(client.get("/profile").json()) == 20


def test_two_database_handles_can_write_to_the_same_file(tmp_path):
    path = str(tmp_path / "shared.db")
    a, b = connect(path), connect(path)
    errors = []

    def worker(db):
        try:
            for _ in range(50):
                db.execute("INSERT INTO conversations DEFAULT VALUES")
                db.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=worker, args=(db,)) for db in (a, b, a, b)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert a.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 200


def test_foreign_keys_enabled_on_every_thread_connection(tmp_path):
    db = connect(str(tmp_path / "fk.db"))
    results = []

    def check():
        results.append(db.execute("PRAGMA foreign_keys").fetchone()[0])
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO messages (conversation_id, role, content) VALUES (999,'user','x')")
        db.rollback()

    check()
    t = threading.Thread(target=check)
    t.start()
    t.join()
    assert results == [1, 1]


def test_failed_write_is_rolled_back_and_leaves_no_partial_chunks(tmp_path, monkeypatch):
    db, mem = _mem(tmp_path)
    u, a = _exchange(db, "x" * 2000, "ok")
    monkeypatch.setattr(EpisodicMemory, "_pieces",
                        staticmethod(lambda *_: [("bon", "Utilisateur : bon"), (None, "mauvais")]))
    with pytest.raises(sqlite3.IntegrityError):
        mem.index_exchange(1, u, a, "x" * 2000, "ok")
    assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 0


# ---------------------------------------------------------------- 2. contexte
def _client(db_path, **settings):
    llm = FakeLLM()
    cfg = Settings(db_path=db_path, embed_model="", **settings)
    return TestClient(create_app(cfg, llm=llm)), llm, cfg


def test_60000_char_message_fits_context_and_keeps_system_prompt(db_path):
    client, llm, cfg = _client(db_path)
    client.post("/chat", json={"message": "Retiens que mon employeur est Atos"})
    big = "début " + "apiVersion v1 kind Pod " * 2600 + " fin"
    r = say(client, big).json()
    sent = llm.calls[-1]
    assert prompt_tokens(sent) <= cfg.num_ctx - cfg.reply_reserve
    system = sent[0]["content"]
    assert "tu rédiges du texte" in system and "employeur : Atos" in system
    assert sent[-1]["content"].startswith("début") and sent[-1]["content"].endswith("fin")
    assert "caractères omis" in sent[-1]["content"]
    assert "très long" in r["reply"]
    stored = client.get(f"/conversations/{r['conversation_id']}").json()["messages"]
    assert stored[0]["content"] == big  # SQLite garde le message complet


def test_recall_after_60000_char_message_fits_context(db_path):
    client, llm, cfg = _client(db_path)
    big = "Configuration Kubernetes : " + "apiVersion v1 kind Pod " * 2600
    cid = say(client, big).json()["conversation_id"]
    say(client, "Nous utilisons Kubernetes 1.30 en production", cid)
    for i in range(10):
        say(client, f"Autre conversation sur Kubernetes numéro {i} " + "détail " * 100)
    r = say(client, "Qu'avons-nous décidé concernant Kubernetes ?", cid).json()
    sent = llm.calls[-1]
    assert prompt_tokens(sent) <= cfg.num_ctx - cfg.reply_reserve
    assert "Souvenirs de conversations passées" in sent[0]["content"]
    assert "Sources : conversation" in r["reply"]


def test_memory_section_has_its_own_budget_and_sources_match_injected(db_path):
    client, llm, cfg = _client(db_path, memory_budget=600)
    for i in range(8):
        say(client, f"Décision {i} sur Kubernetes : " + "justification détaillée " * 30)
    r = say(client, "Qu'avons-nous décidé concernant Kubernetes ?").json()
    system = llm.calls[-1][0]["content"]
    block = system.split("Souvenirs de conversations passées")[1]
    assert estimate_tokens(block) <= 600 + 50
    injected = block.count("[conversation ")
    assert 1 <= injected < 5
    assert r["reply"].split("Sources :")[1].count("conversation ") == injected


def test_small_context_still_fits(db_path):
    client, llm, cfg = _client(db_path, num_ctx=2048)
    cid = say(client, "Retiens que mon employeur est Atos").json()["conversation_id"]
    for i in range(15):
        say(client, f"Message ordinaire {i} " + "texte " * 150, cid)
    say(client, "Qu'avons-nous décidé hier à propos du texte ?", cid)
    for call in llm.calls:
        assert prompt_tokens(call) <= cfg.num_ctx - cfg.reply_reserve
    assert "employeur : Atos" in llm.calls[-1][0]["content"]


def test_recent_history_kept_in_order_when_trimmed(db_path):
    client, llm, cfg = _client(db_path, num_ctx=2048)
    cid = say(client, "premier " + "mot " * 200).json()["conversation_id"]
    for i in range(6):
        say(client, f"suite{i} " + "mot " * 200, cid)
    sent = llm.calls[-1][1:]
    contents = [m["content"] for m in sent]
    assert contents[-1].startswith("suite5")
    assert not any(c.startswith("premier") for c in contents)


# ---------------------------------------------------------------- découpage
def test_split_text_respects_size_and_covers_the_text():
    text = " ".join(f"mot{i}" for i in range(2000))
    parts = split_text(text, 800, 100)
    assert all(len(p) <= 800 for p in parts) and len(parts) > 5
    assert parts[0].startswith("mot0") and parts[-1].endswith("mot1999")
    assert split_text("court") == ["court"] and split_text("  ") == []


def test_passage_deep_in_a_huge_message_is_found_with_a_small_excerpt(make_client):
    client, llm = make_client()
    big = ("remplissage banal " * 1500) + " le code secret est zanzibar " + ("autre banal " * 1500)
    say(client, big)
    hits = client.get("/memory/search", params={"q": "zanzibar"}).json()
    assert len(hits) == 1 and "zanzibar" in hits[0]["text"]
    assert len(hits[0]["display"]) < 1000 and hits[0]["part"] > 0


def test_short_exchange_stays_a_single_chunk(make_client, db_path):
    client, _ = make_client()
    say(client, "Nous utilisons Kubernetes")
    assert connect(db_path).execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1


# ---------------------------------------------------------------- 3. recherche
def test_labels_are_not_indexed_but_are_displayed(make_client):
    client, _ = make_client()
    say(client, "Nous utilisons Kubernetes")
    for q in ("Jarvis", "Utilisateur", "utilisateur jarvis"):
        assert client.get("/memory/search", params={"q": q}).json() == []
    hit = client.get("/memory/search", params={"q": "Kubernetes"}).json()[0]
    assert "Utilisateur" not in hit["text"] and "Jarvis" not in hit["text"]
    assert hit["display"].startswith("Utilisateur : Nous utilisons Kubernetes\nJarvis : ")


def test_embeddings_filter_fts_noise_without_two_word_rule(make_client):
    client, _ = make_client(embedder=FakeEmbedder())
    say(client, "Nous utilisons Kubernetes pour le projet")
    say(client, "J'aime la pizza du projet familial")
    noise = client.get("/memory/search", params={"q": "projet de voiture"}).json()
    assert noise == []
    # Un seul mot distinctif suffit s'il est confirmé par les embeddings.
    one_word = client.get("/memory/search", params={"q": "Kubernetes"}).json()
    assert len(one_word) == 1 and "Kubernetes" in one_word[0]["text"]


def test_fts_candidate_kept_when_no_embedding_available(make_client):
    emb = FakeEmbedder(fail=True)
    client, _ = make_client(embedder=emb)
    say(client, "Nous utilisons Kubernetes pour le projet")
    assert client.get("/memory/search", params={"q": "projet"}).json()


# ---------------------------------------------------------------- migrations
def test_sprint2_database_gets_new_tables_and_is_backfilled(tmp_path):
    path = str(tmp_path / "s2.db")
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER "
        "NOT NULL REFERENCES conversations(id), role TEXT NOT NULL, content TEXT NOT NULL, "
        "kind TEXT NOT NULL DEFAULT 'chat', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE profile (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "INSERT INTO conversations DEFAULT VALUES;"
        "INSERT INTO messages (conversation_id, role, content) VALUES (1,'user','Nous utilisons Kubernetes');"
        "INSERT INTO messages (conversation_id, role, content) VALUES (1,'assistant','ok');"
        "INSERT INTO profile (key, value) VALUES ('employeur','Atos');")
    old.commit()
    old.close()
    db = connect(path)
    assert EpisodicMemory(db).backfill() == 1
    assert db.execute("SELECT value FROM profile WHERE key='employeur'").fetchone()[0] == "Atos"
    assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2


def test_old_episodic_index_is_rebuilt_without_touching_messages(tmp_path):
    path = str(tmp_path / "s3old.db")
    db = connect(path)
    db.execute("INSERT INTO conversations DEFAULT VALUES")
    _exchange(db, "Nous utilisons Kubernetes", "ok")
    db.executescript(  # schéma de la première version du sprint 3
        "DROP TABLE chunks; DROP TABLE chunks_fts;"
        "CREATE TABLE chunks (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, "
        "first_message_id INTEGER NOT NULL UNIQUE, last_message_id INTEGER NOT NULL, "
        "created_at TEXT NOT NULL, text TEXT NOT NULL, embedding BLOB);"
        "CREATE VIRTUAL TABLE chunks_fts USING fts5(text);"
        "INSERT INTO chunks (conversation_id, first_message_id, last_message_id, created_at, text) "
        "VALUES (1, 1, 2, '2026-11-15 10:00:00', 'Utilisateur : Nous utilisons Kubernetes');")
    db.commit()
    db2 = connect(path)
    cols = [r["name"] for r in db2.execute("PRAGMA table_info(chunks)")]
    assert "display" in cols and "part" in cols
    assert db2.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert EpisodicMemory(db2).backfill() == 1
    assert db2.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2


def test_connect_twice_is_idempotent_and_keeps_chunks(tmp_path):
    path = str(tmp_path / "again.db")
    db = connect(path)
    db.execute("INSERT INTO conversations DEFAULT VALUES")
    u, a = _exchange(db, "Nous utilisons Kubernetes", "ok")
    EpisodicMemory(db).index_exchange(1, u, a, "Nous utilisons Kubernetes", "ok")
    assert connect(path).execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1


# ---------------------------------------------------------------- indexation (déjà robuste)
def test_indexing_is_idempotent(tmp_path):
    db, mem = _mem(tmp_path)
    u, a = _exchange(db, "Nous utilisons Kubernetes", "ok")
    mem.index_exchange(1, u, a, "Nous utilisons Kubernetes", "ok")
    mem.index_exchange(1, u, a, "Nous utilisons Kubernetes", "ok")
    assert db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 1


def test_backfill_repairs_exchange_missed_before_a_crash(tmp_path):
    db, mem = _mem(tmp_path)
    u1, a1 = _exchange(db, "Premier sujet Kubernetes", "ok")
    mem.index_exchange(1, u1, a1, "Premier sujet Kubernetes", "ok")
    _exchange(db, "Second sujet Ubuntu", "ok")
    assert mem.backfill() == 1
    assert mem.search("Ubuntu")


def test_special_characters_in_query_do_not_break_fts(tmp_path):
    db, mem = _mem(tmp_path)
    u, a = _exchange(db, "Nous utilisons Kubernetes", "ok")
    mem.index_exchange(1, u, a, "Nous utilisons Kubernetes", "ok")
    for q in ['"', "kubernetes OR (", "*", "NEAR(", "a' OR 1=1 --"]:
        assert isinstance(mem.search(q), list)


@pytest.mark.xfail(strict=True, reason="travail ultérieur n°4 : date UTC de SQLite, pas l'heure locale")
def test_cited_date_uses_local_time(make_client, db_path):
    client, llm = make_client()
    say(client, "Nous utilisons Kubernetes")
    db = connect(db_path)
    db.execute("UPDATE messages SET created_at = '2026-11-14 23:30:00'")
    db.execute("UPDATE chunks SET created_at = '2026-11-14 23:30:00'")
    db.commit()
    client2, _ = make_client()
    r = say(client2, "Qu'avons-nous décidé concernant Kubernetes ?").json()["reply"]
    assert "2026-11-15" in r
