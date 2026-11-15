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

## Alarmes et rappels
Table SQLite `reminders` (indépendante du profil, de l'historique et de l'index épisodique). Commandes
interprétées par un parseur Python déterministe, **sans Qwen** ; l'échange est enregistré en `kind='command'`
(jamais renvoyé au LLM ni indexé). APScheduler 3.x (`AsyncIOScheduler`) ne sert qu'à réveiller le service :
SQLite reste la source de vérité et les jobs sont recréés depuis la table à chaque démarrage.

| Message | Effet |
|---|---|
| « Mets une alarme dans une minute. » / « Préviens-moi dans une minute. » | rappel dans 1 min |
| « Mets un minuteur de 10 minutes. » | rappel dans 10 min |
| « Rappelle-moi dans une heure de sortir le linge. » | message « sortir le linge » |
| « Dans 30 minutes, rappelle-moi de vérifier le serveur. » | idem, durée en tête de phrase |
| « Réveille-moi demain à 7 heures. » | demain 07:00 (heure locale, stocké en UTC) |
| « Quelles alarmes sont programmées ? » | liste des rappels en attente |
| « Annule mon rappel. » | annule le seul rappel ; s'il y en a plusieurs, demande lequel |
| « Annule le rappel 3 » / « Annule tous mes rappels » | annulation ciblée / totale |

Durées : secondes, minutes, heures, « une demi-heure », « un quart d'heure », « une heure et demie ».
Heures : « demain / aujourd'hui à 7 heures », « 7h30 », « 8 heures du soir ». Tout le reste est jugé
ambigu (heure sans jour, « 1h30 », « dans 3 jours », deux échéances, heure passée) : Jarvis le dit et
**ne programme rien**. `dateparser` n'a pas été ajouté (pas de besoin concret pour la première version).

Statuts : `pending` -> `fired` | `cancelled` | `missed`. Le passage `pending -> fired` est une mise à jour
SQL atomique : une notification n'est jamais émise deux fois. Au démarrage, les rappels échus pendant l'arrêt
passent à `missed` (jamais déclenchés, journal `WARNING`, bandeau rouge dans `/ui`) ; les rappels futurs sont reprogrammés.

Notification : Piper synthétise « Rappel : <message> » puis `pw-play` la lit sur la sortie audio de la
session de l'utilisateur qui lance Uvicorn (`JARVIS_AUDIO_PLAYER` pour changer de lecteur). Le déclenchement
s'exécute hors de la boucle asyncio (les autres requêtes ne sont pas bloquées). Si la lecture échoue, le rappel
reste `fired` (pas de répétition), l'erreur est écrite en base (`reminders.error`), dans les journaux et
dans `/ui`. API : `GET /reminders`, `GET /reminders/alerts`, `POST /reminders/{id}/seen`.

Garde-fou : si Qwen prétend avoir programmé une alarme (« j'ai programmé… », « je te préviendrai… »), une
correction déterministe est ajoutée à sa réponse.

## Registre de fonctions

Le LLM ne fait que **demander** l'exécution d'une fonction Python explicitement enregistrée ; il n'exécute
jamais de code. Boucle (`Orchestrator._generate`) : question → Qwen répond ou demande un appel
(`tool_calls`) → `FunctionRegistry.execute` valide les paramètres et exécute → le résultat JSON
(`{"ok":true,"result":…}` ou `{"ok":false,"error":…}`) retourne à Qwen (message `role: "tool"`) → réponse
naturelle. Au plus `JARVIS_MAX_TOOL_ROUNDS` tours (3) et 3 appels par tour ; ensuite réponse forcée sans outil.

Contrat (`app/core/function_registry.py`) : `FunctionSpec(name, description, handler, params, level, trigger)`,
avec `Param(name, type, description, required, enum, minimum, maximum)` (types `string|integer|number|boolean`).
`execute` ne lève jamais : fonction inconnue, paramètre manquant/inconnu/mal typé, erreur du handler
(`FunctionError` = message destiné à l'utilisateur) ou bug → `ok=false`. Niveaux : `read`, `safe_write`
(exécutés sur demande du LLM) et `sensitive` (refusé sans `confirmed=True`, non utilisé pour l'instant).
`trigger` (regex facultative) : si la question la contient et que Qwen répond sans appeler la fonction,
Jarvis relance une fois, puis ajoute « n'a pas été vérifiée » plutôt que de laisser passer une réponse inventée.
Les résultats sont des **données non fiables** (jamais des instructions) et tronqués (~1500 tokens). Les
échanges d'outils ne sont pas stockés ; seule la réponse finale l'est. Les commandes mémoire et rappels
sont traitées avant le LLM et n'utilisent pas le registre. Jarvis injecte la date, l'heure, le décalage UTC
et les 7 prochains jours (Qwen calcule mal les jours de la semaine).

Ollama 0.40 + `qwen3:8b` (capacités `completion, tools, thinking`) : `POST /api/chat` avec `tools`, réponse
`message.tool_calls[].function.{name, arguments}` (dict). `OllamaClient.chat()` (texte) est inchangé ;
`chat_tools()` est ajouté. Un LLM sans `chat_tools` garde l'ancien comportement.

### Ajouter une fonction
1. Écrire un handler dans `app/functions/` (arguments simples, retourne un dict JSON-sérialisable,
   `raise FunctionError("message clair")` pour les erreurs attendues) :

        def meteo(ville: str) -> dict:
            return {"ville": ville, "ciel": "dégagé"}

2. L'enregistrer explicitement dans `create_app` (`app/main.py`) :

        functions.register(FunctionSpec("meteo", "Donne la météo d'une ville.", meteo,
                                        (Param("ville", "string", "Nom de la ville"),), level=READ))

3. Ajouter un test dans `tests/` (sans réseau).

## Google Agenda (lecture seule)
Fonction `calendar_list_events(start, end, max_results)` : agenda `primary`, `events.list`
(`singleEvents=True`, trié par début), événements horaires, journée entière (fin incluse) et récurrents
(`recurring: true`), heures en `Europe/Paris`, titre/début/fin/lieu/description (300 car. max). Scope unique
`https://www.googleapis.com/auth/calendar.events.readonly` : aucune écriture possible, pas de Gmail.

Guide détaillé pas à pas (avec dépannage) : [GOOGLE_CALENDAR_SETUP.md](GOOGLE_CALENDAR_SETUP.md).

Configuration Google Cloud :
1. console.cloud.google.com → nouveau projet.
2. APIs et services → Bibliothèque → activer **Google Calendar API**.
3. Écran de consentement OAuth : type « Externe », ajouter ton compte Google comme **utilisateur test**.
4. Identifiants → Créer → **ID client OAuth**, type **Application de bureau** → télécharger le JSON.
5. Le placer dans `data/google_calendar/credentials.json` (dossier ignoré par Git ; chemins modifiables via
   `JARVIS_GOOGLE_CREDENTIALS` et `JARVIS_GOOGLE_TOKEN`).
6. Première autorisation (ouvre le navigateur) : `python -m app.functions.calendar auth` → crée `token.json`.
7. Vérification : `python -m app.functions.calendar check` (événements des 7 prochains jours).

Fichiers en 0600 (dossier 0700) ; le jeton n'est jamais transmis à Qwen ni écrit dans les journaux. Sans
configuration, Jarvis répond que l'agenda n'est pas configuré / autorisé ; jeton expiré ou révoqué : relancer `auth`.

Limites : lecture seule, calendrier principal uniquement, 30 événements max, période ≤ 1 an. Qwen peut
encore se tromper de période ; la réponse finale (avec des infos d'agenda) est indexée dans la mémoire
épisodique (cf. BACKLOG 7). Le flux OAuth réel n'est testé que par mocks.

## Recherche documentaire (Kensaku, expérimental)
Jarvis interroge l'index local de [Kensaku](https://kensaku.app) (fichiers du projet) via son serveur MCP
`kensaku-cli mcp` (JSON-RPC 2.0 sur stdio, `protocolVersion 2024-11-05`). Aucun port, aucun service systemd :
Jarvis lance le processus au démarrage (`lifespan`), garde **une session persistante** (échanges
sérialisés par un verrou), et le ferme à l'arrêt. Processus mort : une seule relance par appel, puis message
d'indisponibilité. Kensaku absent ou en panne : Jarvis démarre et fonctionne normalement.
Aucune dépendance Python ajoutée (client minimal `subprocess`, pas de SDK `mcp`).

Fonctions (niveau `read`, enregistrées dans le registre) :
- `docs_search(query, max_results)` → 3 à 5 résultats (Jarvis demande 10 à Kensaku puis reclasse) : chemin, score,
  type de correspondance, `name_match` (`file`/`path` si le nom ou le chemin correspond à la requête, sauf contenu
  manifestement hors sujet), extrait (≤ 200 car.).
- `docs_read(path, query, max_tokens ≤ 512)` → passage d'un fichier. Le chemin doit venir d'un `docs_search`
  précédent, être absolu, sans `..`, sous une racine indexée (`index_state`) ; Kensaku refuse en plus tout
  fichier hors index (ex. `token.json` de Google).
Seuls `search`, `read_snippet` et `index_state` sont appelés : jamais `reindex`, `regex_search`, `find_symbol`.
Le LLM décide seul quand chercher (pas de déclencheur). Les résultats sont des données non fiables.
`docs_read` ajoute `query_found` : simple correspondance **littérale** du texte `query` dans le passage, pas une
mesure de pertinence. Sans occurrence, le passage est le début du fichier.
Les extraits ne sont jamais écrits en base. Les échanges qui ont appelé `docs_search`/`docs_read` restent dans
l'historique (`kind = 'document'`) mais ne sont **jamais indexés en mémoire épisodique** (ni au rattrapage au démarrage).

**Garde-fou « réponse documentaire »** : pour une demande explicite de lire/ouvrir/expliquer/résumer un fichier
(verbe + nom de fichier avec extension ou « le fichier/document/module/code »), si le LLM répond sans outil, Jarvis le
relance une fois. Si, au final, aucune lecture `docs_read` n'a réellement réussi (non appelée, erreur, texte vide, ou
fichier lu différent du nom explicitement demandé), Jarvis ajoute « ⚠ Je n'ai pas pu vérifier le contenu du fichier
demandé… ». Le contrôle porte sur l'exécution réelle, pas sur ce que dit le LLM.
**Limite** : une lecture techniquement réussie ne garantit pas que le passage (≤ 512 tokens) contient la réponse ;
une lecture partielle peut encore mener à une réponse erronée ou enjolivée. Le contrôle du « bon fichier » ne
fonctionne que si le message contient un nom de fichier explicite.
Journal : une ligne INFO par fonction (nom, arguments utiles tronqués, statut, durée) ; jamais le contenu lu.

Configuration (`.env.example`) : `JARVIS_KENSAKU=1` (0 pour désactiver), `JARVIS_KENSAKU_CLI=~/.local/bin/kensaku-cli`,
`JARVIS_KENSAKU_TIMEOUT=15` (secondes par appel).

**L'index peut être obsolète** : le watcher de Kensaku ne tourne que si l'application Kensaku est ouverte.
Jarvis ne réindexe pas et ne lance pas l'interface ; pour actualiser, ouvrir Kensaku et attendre la fin de
l'indexation.

Test manuel réel : `python -m app.functions.kensaku check "architecture générale de Jarvis"`
(ouvre une session, cherche, lit le premier passage, ferme le processus). Les tests (`tests/test_kensaku.py`)
utilisent un faux serveur MCP (`tests/fake_kensaku_server.py`), jamais l'index réel.

## Tests
    pytest
