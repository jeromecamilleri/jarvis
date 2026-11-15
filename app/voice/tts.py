"""Synthèse vocale locale (Piper)."""
import io
import re
import threading
import wave

from app.voice.stt import VoiceError

_SOURCES_RE = re.compile(r"\n\s*Sources\s*:.*\Z", re.DOTALL)


def speakable(text: str) -> str:
    """Texte à lire : sans la ligne « Sources », sans symboles Markdown. Jamais tronqué."""
    text = _SOURCES_RE.sub("", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"^\s{0,3}(#{1,6}|[-*•]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`#>|~]", "", text)
    return " ".join(text.split())


class PiperTTS:
    """Voix chargée à la première utilisation seulement."""

    def __init__(self, voice_path: str = "data/models/piper/fr_FR-siwis-medium.onnx"):
        self.voice_path = voice_path
        self._voice = None
        self._lock = threading.Lock()

    def _load(self):
        if self._voice is None:
            from piper import PiperVoice
            try:
                self._voice = PiperVoice.load(self.voice_path)
            except Exception as e:
                raise VoiceError(f"Voix Piper introuvable ({self.voice_path}) : {e}") from e
        return self._voice

    def synthesize(self, text: str) -> bytes:
        text = speakable(text)
        if not text:
            raise VoiceError("Rien à lire")
        buf = io.BytesIO()
        with self._lock:
            voice = self._load()
            with wave.open(buf, "wb") as wav:
                voice.synthesize_wav(text, wav)
        return buf.getvalue()
