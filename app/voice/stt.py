"""Reconnaissance vocale locale (faster-whisper, CPU).

Réglages Whisper et filtre des « inventions » sur du bruit inspirés de
core/oreilles_locales.py de fredo-jarvis/jarvis-fr (CC BY-NC 4.0), réécrits ici.
"""
import subprocess
import threading

import numpy as np

SAMPLE_RATE = 16000
MIN_SECONDS = 0.3
# Sur du bruit, Whisper invente souvent ces phrases apprises dans des sous-titres.
HALLUCINATIONS = ("sous-titr", "sous titr", "amara.org", "merci d'avoir regard",
                  "abonnez-vous", "à bientôt pour une prochaine")


DEFAULT_HINT = ("Conversation avec Jarvis, l'assistant personnel. Mots possibles : Atos, Eviden, "
                "Docker, Kubernetes, Linux, Ubuntu, Python, Ollama, Qwen, GitHub.")


class VoiceError(Exception):
    pass


def decode_audio(data: bytes) -> np.ndarray:
    """Tout format lu par ffmpeg (webm/opus du navigateur, wav...) -> float32 mono 16 kHz."""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1",
             "-ar", str(SAMPLE_RATE), "pipe:1"],
            input=data, capture_output=True, timeout=60)
    except FileNotFoundError as e:
        raise VoiceError("ffmpeg est introuvable") from e
    if proc.returncode != 0:
        raise VoiceError("Audio illisible : " + proc.stderr.decode(errors="replace")[:200])
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def normalize(audio: np.ndarray) -> np.ndarray:
    """Remonte le volume : un micro qui capte bas ne doit pas gêner la compréhension."""
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    return audio * (0.5 / peak) if peak > 1e-6 else audio


def clean_transcript(text: str) -> str:
    text = " ".join((text or "").split())
    lowered = text.lower()
    if len("".join(c for c in lowered if c.isalnum())) < 2:
        return ""
    if any(h in lowered for h in HALLUCINATIONS):
        return ""
    return text


class WhisperSTT:
    """Modèle chargé à la première utilisation seulement."""

    def __init__(self, model: str = "small", model_dir: str = "data/models/whisper",
                 hint: str = DEFAULT_HINT):
        self.model_name = model
        self.model_dir = model_dir
        self.hint = hint
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                                       download_root=self.model_dir)
        return self._model

    def transcribe(self, data: bytes) -> str:
        audio = decode_audio(data)
        if len(audio) < MIN_SECONDS * SAMPLE_RATE:
            return ""
        with self._lock:
            segments, _ = self._load().transcribe(
                normalize(audio), language="fr", beam_size=3, vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=self.hint or None)
            text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.7)
        return clean_transcript(text)
