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
    python -m piper.download_voices fr_FR-siwis-medium --download-dir data/models/piper   # voix (≈60 Mo)
    uvicorn app.main:app --port 8000
    # Whisper « small » (≈460 Mo) se télécharge seul à la première phrase dictée, dans data/models/whisper

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

Garde-fous de fiabilité (déterministes, sans LLM) :
- « Que contient ta mémoire ? », « Que sais-tu de moi ? », « Qu'as-tu retenu sur moi ? » : liste du profil depuis SQLite.
- « Enregistre… », « Mémorise… », « Rappelle-toi… », « N'oublie pas… » n'écrivent **jamais** : réponse fixe
  rappelant « Retiens que », « Note que », « Souviens-toi que ».
- Si Qwen prétend avoir mémorisé quelque chose, une correction est ajoutée à sa réponse (détection par regex).
- Le prompt système distingue Jarvis (application, mémoire) de Qwen (rédaction) ; l'historique n'est jamais une source du profil.

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

## Voix (push-to-talk)
Ouvrir **http://localhost:8000/ui** (Chrome/Firefox, micro autorisé). Tout est local.
- Bouton : un clic démarre, un clic arrête ; ou maintenir enfoncé (> 0,5 s) et relâcher.
- `POST /voice/chat` (audio) : ffmpeg → faster-whisper (`small`, CPU, int8, français) → **le même orchestrateur
  que `/chat`** (profil, commandes « Retiens que… », mémoire épisodique inchangés) → texte.
- `POST /voice/speak` (`{"text": ...}`) : Piper (`fr_FR-siwis-medium`) → WAV. La voix lit la réponse
  **sans la ligne « Sources : … »** et sans symboles Markdown, sans la tronquer ; l'écran garde la réponse complète.
- Phrase vide, bruit ou « sous-titrage… » inventé par Whisper : ignorés, rien n'est envoyé à l'orchestrateur.
- Modèles chargés à la première utilisation ; le mode texte n'est pas affecté. Whisper reste sur CPU (la VRAM est prise par Qwen).
- Réglages : `JARVIS_WHISPER_MODEL`, `JARVIS_WHISPER_DIR`, `JARVIS_WHISPER_HINT` (vocabulaire aidant Whisper sur les
  noms propres), `JARVIS_PIPER_VOICE`.
- Limite : Whisper peut mal entendre un nom propre rare (« Atos » → « à taux ») ; vérifier la transcription affichée.
  Un « Retiens que… » mal transcrit est mémorisé tel quel.

Attribution : les réglages Whisper (langue, beam search, VAD) et le filtre des phrases inventées sur du bruit
s'inspirent de `core/oreilles_locales.py` de [fredo-jarvis/jarvis-fr](https://github.com/fredo-jarvis/jarvis-fr)
(édition de « Mark LV » par FatihMakes, licence CC BY-NC 4.0, usage non commercial). Le code est réécrit ici, non copié.

Améliorations futures : voir `BACKLOG.md`.

## Tests
    pytest
