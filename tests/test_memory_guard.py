"""Voix/texte : consultation du profil, intentions de mémorisation, filet anti-fausses confirmations."""
import pytest

from app.core.commands import (MEMORY_CLAIM_CORRECTION, MEMORY_INTENT_REPLY, NOT_STORED_NOTICE,
                               claims_memory_write, parse_command)
from app.core.orchestrator import SYSTEM_PROMPT
from app.memory.profile import ProfileStore


class ScriptedLLM:
    def __init__(self, reply="réponse de test"):
        self.reply, self.calls = reply, []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply


def say(client, message, cid=None):
    r = client.post("/chat", json={"message": message, "conversation_id": cid})
    assert r.status_code == 200
    return r.json()


@pytest.fixture
def no_profile_writes(monkeypatch):
    writes = []
    monkeypatch.setattr(ProfileStore, "set", lambda self, k, v: writes.append(("set", k, v)))
    monkeypatch.setattr(ProfileStore, "delete", lambda self, k: writes.append(("delete", k)))
    return writes


# --- 1. prompt ----------------------------------------------------------------

def test_prompt_separates_jarvis_application_from_qwen_text_generation():
    assert "Jarvis possède la mémoire personnelle" in SYSTEM_PROMPT.replace("l'application ", "")
    assert "Toi, tu rédiges du texte" in SYSTEM_PROMPT
    assert "uniquement sur la mémoire personnelle fournie" in SYSTEM_PROMPT
    assert "Les échanges précédents ne font pas partie de cette mémoire" in SYSTEM_PROMPT
    for old in ("aucun pouvoir sur la mémoire", "Tu n'as actuellement aucun outil"):
        assert old not in SYSTEM_PROMPT  # plus de négations qui font nier la mémoire


# --- 2. consultation de la mémoire ----------------------------------------------

@pytest.mark.parametrize("phrase", [
    "Que contient ta mémoire ?", "Que contient ta mémoire", "que contient ta mémoire personnelle ?",
    "Que sais-tu de moi ?", "Que sais tu sur moi ?", "Qu'est-ce que tu sais sur moi ?",
    "Qu'as-tu retenu sur moi ?", "Qu'as tu retenu de moi ?", "Qu'est-ce que tu as retenu sur moi ?",
])
def test_list_command_answers_from_profile_without_llm(make_client, phrase):
    client, llm = make_client(llm=ScriptedLLM())
    say(client, "Retiens que mon employeur est Atos")
    say(client, "Retiens que ma ville est Lyon")
    r = say(client, phrase)
    assert llm.calls == []
    assert r["reply"].startswith("D'après ta mémoire personnelle")
    assert "employeur : Atos" in r["reply"] and "ville : Lyon" in r["reply"]
    assert client.get("/profile").json() == {"employeur": "Atos", "ville": "Lyon"}


def test_list_command_with_empty_profile(make_client):
    client, llm = make_client(llm=ScriptedLLM())
    assert "vide" in say(client, "Que contient ta mémoire ?")["reply"]
    assert llm.calls == []


def test_list_question_about_something_else_still_goes_to_llm(make_client):
    client, llm = make_client(llm=ScriptedLLM())
    say(client, "Que contient ce fichier ?")
    say(client, "Que sais-tu de Kubernetes ?")
    assert len(llm.calls) == 2


def test_list_exchange_is_stored_but_hidden_from_llm(make_client):
    client, llm = make_client(llm=ScriptedLLM())
    cid = say(client, "Retiens que mon employeur est Atos")["conversation_id"]
    say(client, "Que contient ta mémoire ?", cid)
    say(client, "Parle-moi de la mer", cid)
    sent = " ".join(m["content"] for m in llm.calls[0][1:])
    assert "Que contient ta mémoire" not in sent and "D'après ta mémoire" not in sent
    assert len(client.get(f"/conversations/{cid}").json()["messages"]) == 6


# --- 3. intentions de mémorisation ----------------------------------------------

INTENTS = [
    "Enregistre dans ta mémoire que je travaille chez Atos",
    "Un registre que je travaille chez Atos, une compagnie française.",
    "Mémorise que mon employeur est Atos",
    "Rappelle-toi que mon employeur est Atos",
    "N'oublie pas que mon employeur est Atos",
    "Retiens mon employeur",
    "Peux-tu enregistrer que je travaille chez Atos ?",
    "Garde en mémoire que j'habite à Lyon",
    "Souviens-toi de mon employeur",
]


@pytest.mark.parametrize("phrase", INTENTS)
def test_memory_intent_never_writes_and_says_so(make_client, no_profile_writes, phrase):
    client, llm = make_client(llm=ScriptedLLM("Je l'ai enregistré !"))
    r = say(client, phrase)
    assert r["reply"] == MEMORY_INTENT_REPLY
    for accepted in ("Retiens que", "Note que", "Souviens-toi que"):
        assert accepted in r["reply"]
    assert llm.calls == [] and no_profile_writes == []
    assert client.get("/profile").json() == {}


def test_memory_intent_is_kept_in_history_but_hidden_from_llm(make_client):
    client, llm = make_client(llm=ScriptedLLM())
    cid = say(client, "Enregistre que je travaille chez Atos")["conversation_id"]
    say(client, "Bonjour", cid)
    assert len(client.get(f"/conversations/{cid}").json()["messages"]) == 4
    sent = " ".join(m["content"] for m in llm.calls[0][1:])
    assert "Enregistre" not in sent and "Rien n'a été enregistré" not in sent


@pytest.mark.parametrize("phrase", [
    "Comment enregistrer un fichier PDF ?", "Je dois retenir ce mot de passe",
    "La mémoire de mon ordinateur est pleine", "Comment mémoriser un poème ?",
    "Il faut que je note ce rendez-vous", "Bonjour Jarvis, comment ça va ?",
])
def test_ordinary_sentences_are_not_memory_intents(make_client, phrase):
    client, llm = make_client(llm=ScriptedLLM())
    r = say(client, phrase)
    assert len(llm.calls) == 1 and MEMORY_INTENT_REPLY not in r["reply"]


def test_explicit_commands_still_take_precedence():
    assert parse_command("Retiens que mon employeur est Atos").action == "remember"
    assert parse_command("Oublie mon employeur").action == "forget"
    assert parse_command("Quel est mon employeur ?").action == "recall"
    assert parse_command("Enregistre que mon employeur est Atos").action == "memory_intent"


# --- 4. filet de sécurité ----------------------------------------------------------

CLAIMS = [
    "Retiens que tu travailles chez Atos. Je n’ai pas accès à cette information, mais je l’ai enregistrée selon ta demande.",
    "C'est noté !", "Je note : tu travailles chez Atos.", "J'ai bien mémorisé ton employeur.",
    "Je m'en souviendrai.", "Je vais le retenir.", "Ton employeur a été enregistré.",
    "J'ai mis à jour ton profil.", "J'ai oublié cette information.",
]
NO_CLAIMS = [
    "Je ne peux pas enregistrer d'informations.", "Je n'ai pas enregistré cette information.",
    "Bonjour ! Je vais bien, merci.", "Pour l'enregistrer, dis « Retiens que… ».",
    "Un conteneur Docker est une unité isolée.",
]


@pytest.mark.parametrize("reply", CLAIMS)
def test_false_confirmation_from_llm_is_corrected_deterministically(make_client, no_profile_writes, reply):
    client, llm = make_client(llm=ScriptedLLM(reply))
    r = say(client, "Parle-moi de moi")
    assert r["reply"] == reply + MEMORY_CLAIM_CORRECTION
    assert no_profile_writes == [] and client.get("/profile").json() == {}


@pytest.mark.parametrize("reply", NO_CLAIMS)
def test_honest_replies_are_left_untouched(make_client, reply):
    assert not claims_memory_write(reply)
    client, _ = make_client(llm=ScriptedLLM(reply))
    assert say(client, "Bonjour")["reply"] == reply


def test_personal_statement_gets_only_one_notice(make_client):
    client, _ = make_client(llm=ScriptedLLM("C'est noté !"))
    r = say(client, "Mon employeur est maintenant Eviden")["reply"]
    assert r.endswith(NOT_STORED_NOTICE) and MEMORY_CLAIM_CORRECTION not in r


def test_real_confirmation_after_explicit_command_is_not_corrected(make_client):
    client, llm = make_client(llm=ScriptedLLM())
    r = say(client, "Retiens que mon employeur est Atos")
    assert r["reply"] == "Je note : employeur = Atos." and llm.calls == []


# --- historique ≠ profil -----------------------------------------------------------

def test_history_only_information_is_never_presented_as_profile(make_client):
    client, llm = make_client(llm=ScriptedLLM())
    cid = say(client, "Je travaille chez Atos, une compagnie française.")["conversation_id"]
    assert client.get("/profile").json() == {}
    assert "vide" in say(client, "Que contient ta mémoire ?", cid)["reply"]
    assert "aucune information mémorisée" in say(client, "Quel est mon employeur ?", cid)["reply"]
    assert len(llm.calls) == 1  # seul le message ordinaire est parti vers le LLM
    say(client, "Qui suis-je ?", cid)
    system = llm.calls[1][0]["content"]
    assert "Atos" not in system and "Mémoire personnelle déjà enregistrée" not in system
    assert "Les échanges précédents ne font pas partie de cette mémoire" in system


def test_profile_is_injected_in_system_prompt_only(make_client):
    client, llm = make_client(llm=ScriptedLLM())
    cid = say(client, "Retiens que mon employeur est Atos")["conversation_id"]
    say(client, "Bonjour", cid)
    assert "employeur : Atos" in llm.calls[0][0]["content"]
    assert all("Atos" not in m["content"] for m in llm.calls[0][1:])
