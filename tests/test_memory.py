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
