import io
import os
import wave

import numpy as np
import pytest

from app.config import Settings
from app.voice.stt import (VoiceError, WhisperSTT, clean_transcript, decode_audio,
                           normalize)
from app.voice.tts import PiperTTS, speakable


class FakeSTT:
    def __init__(self, text="bonjour"):
        self.text = text
        self.calls = 0

    def transcribe(self, data):
        self.calls += 1
        return self.text


class FakeTTS:
    def __init__(self):
        self.texts = []

    def synthesize(self, text):
        self.texts.append(text)
        return b"RIFFfake"


def _wav_bytes(samples, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((samples * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


def _post_audio(client, **data):
    return client.post("/voice/chat", files={"audio": ("v.webm", b"x", "audio/webm")}, data=data)


# --- texte lu ---------------------------------------------------------------

def test_speakable_removes_sources_but_keeps_the_whole_answer():
    long_answer = " ".join(f"Phrase numéro {i}." for i in range(400))
    text = long_answer + "\n\nSources : conversation 3 (2026-11-15 10:00)"
    spoken = speakable(text)
    assert "Sources" not in spoken and "conversation 3" not in spoken
    assert spoken == long_answer  # aucune troncature


def test_speakable_keeps_sources_word_inside_a_sentence():
    assert speakable("Les Sources : à vérifier") == "Les Sources : à vérifier"


def test_speakable_strips_markdown():
    spoken = speakable("## Titre\n- **gras** et `code`\n[lien](http://x.y) fin")
    assert spoken == "Titre gras et code lien fin"


def test_speakable_keeps_not_stored_notice():
    from app.core.commands import NOT_STORED_NOTICE
    assert "conservée" in speakable("Ok." + NOT_STORED_NOTICE + "\n\nSources : conversation 1 (d)").lower() \
        or NOT_STORED_NOTICE.strip()[:10] in speakable("Ok." + NOT_STORED_NOTICE)


# --- STT ----------------------------------------------------------------------

def test_clean_transcript_drops_whisper_hallucinations():
    assert clean_transcript("Sous-titrage Société Radio-Canada") == ""
    assert clean_transcript(" . ") == ""
    assert clean_transcript("  Quel est   mon employeur ? ") == "Quel est mon employeur ?"


def test_decode_audio_and_normalize_with_ffmpeg():
    tone = 0.01 * np.sin(np.linspace(0, 440 * 2 * np.pi, 16000)).astype(np.float32)
    audio = decode_audio(_wav_bytes(tone))
    assert abs(len(audio) - 16000) < 200
    assert abs(np.abs(normalize(audio)).max() - 0.5) < 1e-3


def test_decode_audio_rejects_garbage():
    with pytest.raises(VoiceError):
        decode_audio(b"ceci n'est pas de l'audio")


def test_stt_ignores_too_short_audio_without_loading_model():
    stt = WhisperSTT()
    assert stt.transcribe(_wav_bytes(np.zeros(800, dtype=np.float32))) == ""
    assert stt._model is None


# --- TTS ----------------------------------------------------------------------

def test_piper_receives_text_without_sources(monkeypatch):
    seen = {}

    class FakeVoice:
        def synthesize_wav(self, text, wav):
            seen["text"] = text
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\0\0" * 100)

    tts = PiperTTS()
    monkeypatch.setattr(tts, "_load", lambda: FakeVoice())
    wav = tts.synthesize("Bonjour.\n\nSources : conversation 2 (2026-01-01)")
    assert seen["text"] == "Bonjour."
    assert wav[:4] == b"RIFF"
    with pytest.raises(VoiceError):
        tts.synthesize("\n\nSources : conversation 2 (2026-01-01)")


# --- API vocale ---------------------------------------------------------------

def test_ui_is_served(make_client):
    client, _ = make_client(stt=FakeSTT(), tts=FakeTTS())
    r = client.get("/ui")
    assert r.status_code == 200 and "Jarvis" in r.text


def test_ask_page_is_served(make_client):
    client, _ = make_client()
    r = client.get("/ask")
    assert r.status_code == 200 and "/chat" in r.text and "Send" in r.text


def test_voice_chat_goes_through_orchestrator_and_memory(make_client):
    stt = FakeSTT("Retiens que mon employeur est Atos")
    client, llm = make_client(stt=stt, tts=FakeTTS())
    r = _post_audio(client).json()
    assert r["transcript"] == "Retiens que mon employeur est Atos"
    assert "Atos" in r["reply"] and llm.calls == []          # commande déterministe
    assert client.get("/profile").json() == {"employeur": "Atos"}

    stt.text = "Quel est mon employeur ?"
    r2 = _post_audio(client, conversation_id=str(r["conversation_id"])).json()
    assert "Atos" in r2["reply"] and llm.calls == []          # réponse issue du profil

    stt.text = "Parle-moi de la mer"
    r3 = _post_audio(client, conversation_id=str(r["conversation_id"])).json()
    assert r3["reply"].startswith("réponse de test") and len(llm.calls) == 1
    msgs = client.get(f"/conversations/{r['conversation_id']}").json()["messages"]
    assert len(msgs) == 6                                     # l'historique garde tout


def test_voice_ordinary_sentence_does_not_modify_profile(make_client):
    client, llm = make_client(stt=FakeSTT("Mon employeur est maintenant Eviden"), tts=FakeTTS())
    r = _post_audio(client).json()
    assert len(llm.calls) == 1
    assert llm.calls[0][-1]["content"] == "Mon employeur est maintenant Eviden"
    assert client.get("/profile").json() == {}
    assert "rien n'a été enregistré" in r["reply"].lower()


def test_voice_empty_transcript_does_nothing(make_client):
    client, llm = make_client(stt=FakeSTT(""), tts=FakeTTS())
    r = _post_audio(client).json()
    assert r["transcript"] == "" and r["reply"] == "" and llm.calls == []
    assert client.get("/conversations/1").status_code == 404


def test_voice_chat_unreadable_audio_is_422(make_client):
    client, _ = make_client(tts=FakeTTS())   # vrai STT : ffmpeg échoue avant tout chargement du modèle
    r = client.post("/voice/chat", files={"audio": ("v.webm", b"pas de l'audio", "audio/webm")})
    assert r.status_code == 422


def test_voice_speak_returns_wav(make_client):
    tts = FakeTTS()
    client, _ = make_client(stt=FakeSTT(), tts=tts)
    r = client.post("/voice/speak", json={"text": "Bonjour"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert tts.texts == ["Bonjour"]


# --- vrais modèles locaux (ignorés s'ils ne sont pas téléchargés) -------------

S = Settings()
HAVE_MODELS = os.path.exists(S.piper_voice) and os.path.isdir(S.whisper_dir)


@pytest.mark.skipif(not HAVE_MODELS, reason="modèles vocaux absents")
def test_real_piper_then_whisper_roundtrip():
    wav = PiperTTS(S.piper_voice).synthesize("Quel temps fait-il demain à Paris ?\n\nSources : conversation 1 (x)")
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnframes() > 8000
    text = WhisperSTT(S.whisper_model, S.whisper_dir).transcribe(wav).lower()
    assert "temps" in text and "paris" in text
    assert "source" not in text
