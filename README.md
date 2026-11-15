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
    ollama pull bge-m3      # embeddings de la mémoire épisodique
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

Questions simples (« Quel est mon employeur ? ») : réponse directe depuis SQLite, sans LLM. Les échanges
de commandes mémoire sont enregistrés (`messages.kind = 'command'`) mais jamais renvoyés au LLM.

## Mémoire épisodique (sprint 3)
Retrouver ce qui s'est dit dans d'anciennes conversations, avec citation de la source.
Distincte du profil : un souvenir de conversation n'est jamais présenté comme une information personnelle.

**Indexation** (`app/memory/episodic.py`)
- Chaque échange conversationnel (message utilisateur + réponse) devient un *extrait* dans `chunks`,
  relié à ses messages d'origine, avec sa date. L'historique complet reste dans `messages`.
- Exclus : commandes mémoire, réponses du profil, et questions de rappel (déclencheur + « ? »).
- Les échanges longs sont découpés en extraits d'environ 800 caractères (coupes sur phrases/lignes, recouvrement 100,
  100 extraits max par message). Les textes indexés ne contiennent pas les étiquettes « Utilisateur »/« Jarvis »
  (également mots vides) ; elles ne sont ajoutées qu'à l'affichage (`display`).
- Index mots-clés `chunks_fts` (SQLite FTS5, insensible aux accents) et vecteur `bge-m3` (Ollama, local) par extrait.
- Au démarrage, `backfill()` indexe les anciens échanges (idempotent). Les embeddings manquants sont calculés
  plus tard (jusqu'à 64 par recherche ou par nouvel échange) ; si Ollama est indisponible, FTS5 seul est utilisé.

**Recherche hybride**
1. FTS5 : mots significatifs de la question (sans mots vides), en OR, classés par BM25.
2. Vecteurs : similarité cosinus (numpy), seuil 0,55.
   Les candidats FTS5 sont aussi contrôlés par embedding (seuil 0,45, sans règle rigide de mots communs) ;
   sans embedding disponible, ils sont conservés.
3. Fusion par *Reciprocal Rank Fusion* (k=60), top 5 (`Settings.episodic_k`).
4. Les extraits déjà présents dans la fenêtre de 20 messages envoyée au LLM sont exclus.

**Déclenchement** (`app/core/triggers.py`) : règles explicites, sans LLM — « avons-nous… », « avions-nous… »,
« ce que nous avons… », « te souviens-tu », « la semaine dernière », « hier », « l'autre jour »,
« anciennes conversations »… Sans déclencheur, aucune recherche n'a lieu.

**Réponse et citations** : les extraits (avec n° de conversation et date) sont ajoutés au prompt système
avec la consigne de s'y limiter ; l'orchestrateur ajoute lui-même `Sources : conversation N (date)`.
Si rien n'est retrouvé, le LLM reçoit la consigne de le dire sans rien inventer.

**Budget de contexte** (`app/core/budget.py`, `Settings.num_ctx=8192`, `reply_reserve=1024`, `memory_budget=1500`)
Priorité : prompt système + règles + profil (jamais tronqués) > message courant (tronqué au milieu avec marqueur
s'il dépasse 60 % de l'espace restant) > historique récent (les plus récents d'abord) > souvenirs (budget global).
L'estimation en tokens est pessimiste et dépend du texte (lettres ≈ 2,5 car./token, symboles ≈ 1 token) ; ce n'est
qu'une estimation. Mesuré avec Qwen sur 60 000 caractères : 2 504 (prose), 3 519 (YAML), 5 049 (aléatoire) tokens
réels, pour une limite de 7 168. `Sources` ne liste que les extraits réellement injectés.

**SQLite** : une connexion par thread (`Database`, `app/memory/database.py`), `PRAGMA foreign_keys=ON`,
timeout d'attente 30 s, commit après chaque écriture courte, indexation d'un échange en une transaction.
Les stores gardent la même API.

**Migrations** (automatiques, non destructives pour `messages`, `conversations`, `profile`) : ajout de
`messages.kind` si absent ; création de `chunks`/`chunks_fts` ; un ancien `chunks` (sans `display`) est recréé
puis réindexé par `backfill()` (données dérivées uniquement).

**Débogage** : `GET /memory/search?q=...&k=5`. Variable : `JARVIS_EMBED_MODEL` (vide = FTS5 seul).

**Travaux ultérieurs** (non traités) :
4. Sources filtrées par score de pertinence ; dates citées en heure locale (test xfail).
5. Cache mémoire des vecteurs (rechargés à chaque recherche : ~110 ms à 20 000 extraits).
6. Embeddings en arrière-plan (aujourd'hui dans la requête).
7. Pondération/exclusion des réponses de l'assistant indexées (hallucinations possibles).
8. Swap VRAM qwen/bge-m3 sur GPU 8 Go.
Aussi : les mots génériques (« projet ») restent ambigus ; les résumés de conversation sont prévus plus tard.

## Tests
    pytest
