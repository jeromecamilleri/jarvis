# Jarvis

Assistant personnel local sous Ubuntu (FastAPI + Ollama + SQLite).
Machine cible : i7-8700K, 32 Go RAM, GTX 1070 8 Go.

## Sprint 1 — cerveau textuel
- [ ] Réponse via Ollama
- [ ] Conversations persistantes après redémarrage
- [ ] Retrouver une information enregistrée
- [ ] Mémoire personnelle (profil) distincte de l'historique

## Installation
    python3 -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env   # puis: set -a; . ./.env; set +a
    ollama pull qwen3:8b
    uvicorn app.main:app --port 8000

## Utilisation
    curl -XPOST localhost:8000/chat -H 'Content-Type: application/json' \
      -d '{"message":"Retiens que je travaille sur Kubernetes"}'
    curl localhost:8000/profile

## Mémoire personnelle (sprint 2)
Commandes explicites uniquement (sans LLM) ; le reste n'alimente que l'historique.

| Message | Effet |
|---|---|
| « Retiens que mon employeur est Atos. » | `employeur = Atos` |
| « Retiens que mon employeur est maintenant Eviden. » | remplace la valeur (« Note que… » et « Souviens-toi que… » aussi) |
| « Mon employeur est maintenant Eviden. » | aucun effet sur le profil (historique seul) |
| « Oublie mon employeur. » | supprime la clé (message si inconnue) |

Phrases non structurables (« Retiens que je travaille sur Kubernetes ») : clé dérivée de la phrase.
API : `GET /profile`, `GET /profile/{key}`, `PUT /profile`, `DELETE /profile/{key}`.

## Tests
    pytest
