def test_history_survives_restart(make_client):
    c1, _ = make_client()
    cid = c1.post("/chat", json={"message": "Bonjour"}).json()["conversation_id"]
    c2, _ = make_client()
    msgs = c2.get(f"/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_profile_fact_is_reinjected_after_restart(make_client):
    c1, _ = make_client()
    c1.post("/chat", json={"message": "Retiens que je travaille sur Kubernetes"})
    c2, llm = make_client()
    c2.post("/chat", json={"message": "Sur quoi je travaille ?"})
    system = llm.calls[0][0]["content"]
    assert "Kubernetes" in system


def test_plain_sentence_not_stored_in_profile(make_client):
    client, _ = make_client()
    client.post("/chat", json={"message": "Je mange une pomme"})
    assert client.get("/profile").json() == {}


def test_history_sent_to_llm(make_client):
    client, llm = make_client()
    cid = client.post("/chat", json={"message": "un"}).json()["conversation_id"]
    client.post("/chat", json={"message": "deux", "conversation_id": cid})
    contents = [m["content"] for m in llm.calls[1]]
    assert "un" in contents and "réponse de test" in contents


def say(client, message, cid=None):
    body = {"message": message}
    if cid:
        body["conversation_id"] = cid
    return client.post("/chat", json=body).json()


def test_structured_key(make_client):
    client, llm = make_client()
    r = say(client, "Retiens que mon employeur est Atos.")
    assert client.get("/profile").json() == {"employeur": "Atos"}
    assert "Atos" in r["reply"] and llm.calls == []


def test_update_replaces_value(make_client):
    client, _ = make_client()
    say(client, "Retiens que mon employeur est Atos.")
    say(client, "Retiens que mon employeur est maintenant Eviden.")
    assert client.get("/profile").json() == {"employeur": "Eviden"}
    say(client, "Note que mon employeur est Capgemini")
    assert client.get("/profile").json() == {"employeur": "Capgemini"}
    say(client, "Souviens-toi que mon employeur est Sopra")
    assert client.get("/profile").json() == {"employeur": "Sopra"}


def test_statement_without_explicit_command_does_not_change_profile(make_client):
    client, llm = make_client()
    say(client, "Retiens que mon employeur est Atos.")
    cid = say(client, "Mon employeur est maintenant Eviden")["conversation_id"]
    assert client.get("/profile").json() == {"employeur": "Atos"}
    assert len(llm.calls) == 1
    msgs = client.get(f"/conversations/{cid}").json()["messages"]
    assert msgs[0]["content"] == "Mon employeur est maintenant Eviden"
    r = say(client, "Mon employeur est maintenant Eviden", cid)
    assert client.get("/profile").json() == {"employeur": "Atos"}


def test_key_is_normalized(make_client):
    client, _ = make_client()
    say(client, "Retiens que ma Ville de naissance est Lyon")
    say(client, "Retiens que mon employeur est Atos")
    assert set(client.get("/profile").json()) == {"ville_de_naissance", "employeur"}


def test_forget(make_client):
    client, llm = make_client()
    say(client, "Retiens que mon employeur est Atos.")
    r = say(client, "Oublie mon employeur.")
    assert "oublié" in r["reply"]
    assert client.get("/profile").json() == {}
    assert llm.calls == []


def test_forget_unknown_is_not_an_error(make_client):
    client, _ = make_client()
    r = client.post("/chat", json={"message": "Oublie mon employeur."})
    assert r.status_code == 200
    assert "aucune information" in r.json()["reply"]


def test_ordinary_conversation_never_touches_profile(make_client):
    client, llm = make_client()
    say(client, "Retiens que mon employeur est Atos.")
    for msg in ["Mon employeur est vraiment pénible", "Je mange une pomme",
                "Ma voiture est rouge", "Tu peux oublier ça ?"]:
        say(client, msg)
    assert client.get("/profile").json() == {"employeur": "Atos"}
    assert len(llm.calls) == 4


def test_profile_persists_and_is_injected_with_key(make_client):
    c1, _ = make_client()
    say(c1, "Retiens que mon employeur est Atos.")
    c2, llm = make_client()
    assert c2.get("/profile").json() == {"employeur": "Atos"}
    say(c2, "Que me conseilles-tu pour ma journée ?")
    assert "employeur : Atos" in llm.calls[0][0]["content"]


def test_commands_are_in_history_not_profile_text(make_client):
    client, _ = make_client()
    cid = say(client, "Retiens que mon employeur est Atos.")["conversation_id"]
    msgs = client.get(f"/conversations/{cid}").json()["messages"]
    assert msgs[0]["content"] == "Retiens que mon employeur est Atos."
    assert client.get("/profile").json() == {"employeur": "Atos"}


def test_free_sentence_still_remembered(make_client):
    client, llm = make_client()
    say(client, "Retiens que je travaille sur Kubernetes")
    say(client, "Sur quoi je travaille ?")
    assert "Kubernetes" in llm.calls[0][0]["content"]
    say(client, "Oublie que je travaille sur Kubernetes")
    assert client.get("/profile").json() == {}


def test_profile_endpoints(make_client):
    client, _ = make_client()
    client.put("/profile", json={"key": "Employeur", "value": "Atos"})
    assert client.get("/profile/employeur").json() == {"key": "employeur", "value": "Atos"}
    assert client.get("/profile/inconnu").status_code == 404
    assert client.delete("/profile/Employeur").status_code == 200
    assert client.delete("/profile/employeur").status_code == 404


def test_legacy_sentence_keys_still_usable(make_client, db_path):
    import sqlite3
    from app.memory.database import connect
    connect(db_path).execute(
        "INSERT INTO profile (key, value) VALUES ('mon employeur est atos.', 'mon employeur est Atos.')"
    ).connection.commit()
    client, llm = make_client()
    say(client, "Bonjour")
    assert "mon employeur est Atos." in llm.calls[0][0]["content"]
    assert client.delete("/profile/mon employeur est atos.").status_code == 200


def test_system_prompt_contains_memory_rules(make_client):
    client, llm = make_client()
    say(client, "Bonjour")
    system = llm.calls[0][0]["content"]
    assert "tu rédiges du texte" in system and "possède la mémoire personnelle" in system
    assert "ne dis jamais que tu l'as fait" in system
    assert "qu'elle n'est pas enregistrée" in system
    assert "Retiens que" in system and "Note que" in system


def test_statement_goes_to_llm_without_profile_write(make_client, monkeypatch):
    from app.memory.profile import ProfileStore
    writes = []
    monkeypatch.setattr(ProfileStore, "set", lambda self, k, v: writes.append((k, v)))
    monkeypatch.setattr(ProfileStore, "delete", lambda self, k: writes.append(k))
    client, llm = make_client()
    r = say(client, "Mon employeur est maintenant Eviden")
    assert llm.calls[0][-1] == {"role": "user", "content": "Mon employeur est maintenant Eviden"}
    assert r["reply"].startswith("réponse de test")
    assert writes == []


def _sent_to_llm(llm):
    return [m["content"] for m in llm.calls[-1]]


def test_command_exchanges_hidden_from_llm_but_kept_in_sqlite(make_client):
    client, llm = make_client()
    cid = say(client, "Retiens que mon employeur est Atos.")["conversation_id"]
    say(client, "Je note que tu es lent, mais ça va", cid)
    say(client, "Oublie mon employeur.", cid)
    say(client, "Bonjour", cid)
    sent = _sent_to_llm(llm)
    assert "Retiens que mon employeur est Atos." not in sent
    assert "Oublie mon employeur." not in sent
    assert not any(c.startswith(("Je note :", "J'ai oublié")) for c in sent[1:])
    assert "Je note que tu es lent, mais ça va" in sent and "Bonjour" in sent
    stored = client.get(f"/conversations/{cid}").json()["messages"]
    assert len(stored) == 8
    assert stored[0]["content"] == "Retiens que mon employeur est Atos."
    assert stored[1]["content"].startswith("Je note :")
    assert stored[5]["content"].startswith("J'ai oublié")


def test_ordinary_exchanges_keep_chronological_order(make_client):
    client, llm = make_client()
    cid = say(client, "un")["conversation_id"]
    say(client, "Note que mon employeur est Atos", cid)
    say(client, "deux", cid)
    say(client, "trois", cid)
    roles = [(m["role"], m["content"]) for m in llm.calls[-1][1:]]
    assert roles == [("user", "un"), ("assistant", "réponse de test"),
                     ("user", "deux"), ("assistant", "réponse de test"),
                     ("user", "trois")]


def test_profile_still_injected_when_commands_hidden(make_client):
    client, llm = make_client()
    cid = say(client, "Souviens-toi que mon employeur est Atos")["conversation_id"]
    say(client, "Que me conseilles-tu pour ma journée ?", cid)
    assert "employeur : Atos" in llm.calls[0][0]["content"]
    assert "Souviens-toi" not in "".join(_sent_to_llm(llm)[1:])


def test_history_limit_counts_only_conversational_messages(make_client):
    client, llm = make_client()
    cid = say(client, "m0")["conversation_id"]
    for i in range(30):
        say(client, f"Retiens que ma clé{i} est v{i}", cid)
    say(client, "fin", cid)
    assert _sent_to_llm(llm)[1:3] == ["m0", "réponse de test"]


def test_existing_db_without_kind_column_is_migrated(tmp_path):
    import sqlite3
    from app.memory.database import connect
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER "
        "NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "INSERT INTO conversations DEFAULT VALUES;"
        "INSERT INTO messages (conversation_id, role, content) VALUES (1, 'user', 'ancien');"
    )
    old.commit()
    old.close()
    row = connect(path).execute("SELECT content, kind FROM messages").fetchone()
    assert (row["content"], row["kind"]) == ("ancien", "chat")


def test_question_answered_from_profile_without_llm(make_client):
    client, llm = make_client()
    say(client, "Retiens que mon employeur est Atos")
    r = say(client, "Quel est mon employeur ?")
    assert "Atos" in r["reply"]
    assert llm.calls == []


def test_question_on_empty_profile_says_not_memorized(make_client):
    client, llm = make_client()
    r = say(client, "Quel est mon employeur ?")
    assert "aucune information mémorisée" in r["reply"]
    assert llm.calls == []


def test_profile_is_source_of_truth_over_history(make_client):
    client, llm = make_client()
    cid = say(client, "Retiens que mon employeur est Atos")["conversation_id"]
    r = say(client, "Mon employeur est maintenant Eviden", cid)
    assert client.get("/profile").json() == {"employeur": "Atos"}
    assert "Rien n'a été enregistré" in r["reply"]
    assert len(llm.calls) == 1
    r = say(client, "Quel est mon employeur ?", cid)
    assert "Atos" in r["reply"] and "Eviden" not in r["reply"]
    assert len(llm.calls) == 1


def test_not_stored_notice_only_on_personal_statements(make_client):
    client, _ = make_client()
    assert "Rien n'a été enregistré" not in say(client, "Bonjour")["reply"]
    assert "Rien n'a été enregistré" not in say(client, "Quelle heure est-il ?")["reply"]


def test_general_question_still_uses_llm(make_client):
    client, llm = make_client()
    r = say(client, "Quelle est la capitale de la France ?")
    assert r["reply"] == "réponse de test" and len(llm.calls) == 1


def test_recall_after_update_and_forget(make_client):
    client, _ = make_client()
    say(client, "Retiens que mon employeur est Atos")
    say(client, "Note que mon employeur est Eviden")
    assert "Eviden" in say(client, "Quel est mon employeur ?")["reply"]
    say(client, "Oublie mon employeur")
    assert "aucune information mémorisée" in say(client, "Quel est mon employeur ?")["reply"]


def test_recall_exchange_kept_in_sqlite_but_hidden_from_llm(make_client):
    client, llm = make_client()
    cid = say(client, "Retiens que mon employeur est Atos")["conversation_id"]
    say(client, "Quel est mon employeur ?", cid)
    say(client, "Bonjour", cid)
    assert len(client.get(f"/conversations/{cid}").json()["messages"]) == 6
    assert not any("mémoire personnelle" in c for c in _sent_to_llm(llm)[1:])
