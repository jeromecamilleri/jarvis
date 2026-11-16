"""Pont vers le projet indépendant mini-llm. À lancer avec le Python de mini-llm (celui qui a torch) :

    <mini-llm>/.venv/bin/python tools/minillm_bridge.py < requête.json

Entrée (JSON sur stdin) : minillm_dir, checkpoint, tokenizer, prompt, max_tokens, temperature, top_k, top_p, seed.
Sortie (JSON sur stdout) : {"text": suite générée} ou {"error": message} avec un code de retour non nul.
Ne dépend d'aucun module de Jarvis et ne modifie rien dans mini-llm : il importe seulement ses outils.
"""
import json
import sys
import time
from pathlib import Path


def run(req: dict) -> str:
    import torch  # noqa: PLC0415 - uniquement disponible dans l'environnement de mini-llm

    src = Path(req["minillm_dir"]) / "src"
    for sub in ("06_corpus", "04_training", "05_inference"):
        sys.path.insert(0, str(src / sub / "tools"))
    from bpe_rapide import BPE  # noqa: PLC0415
    from generation import choisir, filtrer_top_k, filtrer_top_p, probabilites, scores_suivant  # noqa: PLC0415
    from gpt_torch import GPT  # noqa: PLC0415

    bpe = BPE.charger(req["tokenizer"])
    contenu = torch.load(req["checkpoint"], map_location="cpu")
    modele = GPT(**contenu["config"])
    modele.load_state_dict(contenu["poids"])
    modele.eval()

    ids = bpe.encoder(req["prompt"])
    if not ids:
        raise ValueError("amorce vide après encodage")
    seed = req.get("seed")
    generateur = torch.Generator().manual_seed(int(seed if seed is not None else time.time_ns() % 2**31))
    nouveaux = []
    for _ in range(int(req["max_tokens"])):
        probas = probabilites(scores_suivant(modele, ids + nouveaux), float(req["temperature"]))
        if req.get("top_k"):
            probas = filtrer_top_k(probas, int(req["top_k"]))
        if req.get("top_p"):
            probas = filtrer_top_p(probas, float(req["top_p"]))
        nouveaux.append(choisir(probas, generateur))
    return bpe.decoder(nouveaux)


def main() -> int:
    try:
        print(json.dumps({"text": run(json.load(sys.stdin))}, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001 - toute erreur est renvoyée à Jarvis sous forme structurée
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
