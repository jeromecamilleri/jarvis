"""Adaptateur du modèle expérimental mini-llm (continuation de texte), exécuté dans un sous-processus.

Contrat : ce modèle ne comprend ni instruction, ni historique, ni outils. Il prolonge un texte.
  - capacités : TEXT uniquement (une requête avec outils est refusée explicitement) ;
  - le prompt est le contenu du DERNIER message `user` de la requête (les autres messages sont ignorés) ;
  - la réponse est la SUITE générée (l'amorce n'est pas répétée) ;
  - `request.options` : max_tokens, temperature, top_k, top_p, seed ;
  - toute erreur (fichier absent, délai dépassé, sortie illisible) lève `LLMError`.
Le projet mini-llm n'est jamais importé dans Jarvis : seul son interpréteur Python est lancé.
"""
import json
import subprocess
from pathlib import Path

from app.core import tracing
from app.core.model_adapter import TEXT, LLMError, ModelRequest, ModelResponse

DEFAULTS = {"max_tokens": 120, "temperature": 0.8, "top_k": 40, "top_p": None, "seed": None}
MAX_TOKENS_LIMIT = 400


class MiniLLMAdapter:
    capabilities = frozenset({TEXT})

    def __init__(self, minillm_dir: str, python: str, checkpoint: str, tokenizer: str,
                 bridge: str, timeout: float = 60.0):
        self.minillm_dir, self.python, self.bridge, self.timeout = minillm_dir, python, bridge, timeout
        self.checkpoint, self.tokenizer = checkpoint, tokenizer

    def missing_files(self) -> list[str]:
        return [str(p) for p in (self.python, self.bridge, self.checkpoint, self.tokenizer)
                if not Path(p).is_file()]

    def generate(self, request: ModelRequest) -> ModelResponse:
        if request.tools:
            raise LLMError("mini-llm ne prend pas en charge les appels de fonctions.")
        prompt = next((m.get("content") for m in reversed(request.messages) if m.get("role") == "user"), None)
        if not isinstance(prompt, str) or not prompt.strip():
            raise LLMError("mini-llm : amorce vide (aucun message utilisateur).")
        missing = self.missing_files()
        if missing:
            raise LLMError("mini-llm indisponible, fichier introuvable : " + ", ".join(missing))
        payload = {"minillm_dir": self.minillm_dir, "checkpoint": self.checkpoint, "tokenizer": self.tokenizer,
                   "prompt": prompt, **self._options(request.options)}
        with tracing.span("adapter", "minillm", adapter="MiniLLMAdapter", max_tokens=payload["max_tokens"]) as info:
            try:
                proc = subprocess.run([self.python, self.bridge], input=json.dumps(payload), capture_output=True,
                                      text=True, timeout=self.timeout, check=False)
            except subprocess.TimeoutExpired as e:
                raise LLMError(f"mini-llm : délai dépassé ({self.timeout:g} s).") from e
            except OSError as e:
                raise LLMError(f"mini-llm : lancement impossible : {e}") from e
            info["returncode"] = proc.returncode
            text = self._parse(proc)
        return ModelResponse(text, [], {"role": "assistant", "content": text})

    @staticmethod
    def _options(options: dict) -> dict:
        opts = {**DEFAULTS, **{k: v for k, v in options.items() if k in DEFAULTS}}
        try:
            opts["max_tokens"] = int(opts["max_tokens"])
            opts["temperature"] = float(opts["temperature"])
        except (TypeError, ValueError) as e:
            raise LLMError(f"mini-llm : option invalide : {e}") from e
        if not 1 <= opts["max_tokens"] <= MAX_TOKENS_LIMIT:
            raise LLMError(f"mini-llm : max_tokens doit être entre 1 et {MAX_TOKENS_LIMIT}.")
        if opts["temperature"] <= 0:
            raise LLMError("mini-llm : temperature doit être positive.")
        return opts

    @staticmethod
    def _parse(proc: subprocess.CompletedProcess) -> str:
        lines = (proc.stdout or "").strip().splitlines()
        try:
            out = json.loads(lines[-1]) if lines else {}
        except json.JSONDecodeError:
            out = {}
        if proc.returncode != 0 or "text" not in out:
            detail = out.get("error") or (proc.stderr or "").strip()[-200:] or f"code {proc.returncode}"
            raise LLMError(f"mini-llm : échec de génération ({detail}).")
        if not isinstance(out["text"], str) or not out["text"].strip():
            raise LLMError("mini-llm : texte généré vide.")
        return out["text"]
