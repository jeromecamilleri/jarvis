from app.core.llm import LLMError


def test_chat_answers(make_client):
    client, _ = make_client()
    r = client.post("/chat", json={"message": "Bonjour"})
    assert r.status_code == 200
    assert r.json()["reply"] == "réponse de test"


def test_ollama_error_gives_502(make_client):
    class Broken:
        def chat(self, messages):
            raise LLMError("timeout")
    client, _ = make_client(Broken())
    assert client.post("/chat", json={"message": "x"}).status_code == 502
