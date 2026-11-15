# Historique — tests documentaires réels (Kensaku + Qwen)

Date : 2026-11-15, après le garde-fou documentaire (commit e0a2b508 et corrections suivantes).
Serveur Jarvis sur `http://127.0.0.1:8001`, appels `curl -X POST /chat`. Réponses reproduites telles que reçues.

## Test 1 — Architecture générale (conversation 2)

**Question** : Quelle est l'architecture générale de Jarvis ? Décris les principaux modules Python et leurs
responsabilités, en t'appuyant sur le code.

**Réponse** :
> Le fichier `README.md` ne contient pas d'informations directes sur l'architecture de Jarvis. Voici ce que l'on peut déduire des éléments disponibles :
>
> 1. **Modules principaux** :
>    - **`function_registry.py`** : Gère l'enregistrement et l'appel des fonctions (comme `calendar_list_events`, `docs_search`, etc.). Chaque fonction est associée à une commande spécifique.
>    - **`kensaku`** : Module de recherche d'informations (via un serveur local), utilisé pour interroger des documents et exécuter des requêtes.
>    - **`alarmes`** : Gère la programmation d'alarmes et de rappels (via des scripts externes).
>
> 2. **Structure de base** :
>    - Le projet repose sur une **mémoire locale** (base SQLite) pour stocker des données personnelles.
>    - Les **fonctions** sont déclenchées via des commandes textuelles (ex : `Mets une alarme`), et les résultats sont renvoyés sous forme de texte.
>
> Pour des détails techniques sur les modules, il faudrait explorer le code source directement (ex : `app/core/function_registry.py` ou `app/functions/`).

**Observation** : réponse prudente et partielle ; elle reconnaît ne pas avoir lu le code. Pas de lecture réelle des modules.

## Test 2 — Mémoire épisodique (conversation 3)

**Question** : Lis les fichiers concernés et explique précisément comment fonctionne la mémoire épisodique de Jarvis,
depuis l'indexation des conversations jusqu'à la recherche des souvenirs.

**Réponse** :
> La mémoire épisodique de Jarvis repose sur une base de données SQLite locale et une logique de gestion des commandes. Voici les étapes clés :
>
> 1. **Indexation des conversations** — Les échanges utilisateur-assistant sont stockés dans la mémoire personnelle (base SQLite) via des commandes comme « Retiens que… ». Ces données sont structurées en entrées clé-valeur, avec des timestamps pour la chronologie.
> 2. **Stockage des souvenirs** — Les informations sont sauvegardées dans un profil utilisateur distinct de l'historique de conversation. Les commandes « Note que… » ou « Souviens-toi que… » activent l'enregistrement, tandis que « Oublie… » permet de supprimer des données.
> 3. **Recherche des souvenirs** — L'application utilise des requêtes SQL pour récupérer des informations en fonction de mots-clés ou de dates. La fonction `docs_search` permet d'explorer les fichiers de documentation pour des détails techniques (ex. : gestion des conflits de mise à jour ou optimisation de la base de données).
> 4. **Intégration avec d'autres systèmes** — Bien que non activé ici, la mémoire peut interagir avec l'agenda Google (via `calendar_list_events`) ou des fichiers locaux (via `docs_read`) pour enrichir les rappels ou les réponses.
>
> Pour des détails techniques (ex. : schéma de la base de données ou algorithmes de recherche), consultez les fichiers `BACKLOG.md` et `README.md` via `docs_read`.

**Observation** : réponse **inexacte** (confond mémoire personnelle et mémoire épisodique ; ne décrit ni les chunks,
ni FTS5, ni les embeddings). Aucun avertissement ⚠ : le message ne contient ni nom de fichier ni « le fichier »
(« les fichiers concernés » ne déclenche pas le garde-fou actuel).

## Test 3 — Google Calendar (conversation 4)

**Question** : Lis les fichiers concernant Google Calendar et explique comment Jarvis gère l'authentification OAuth,
le stockage des tokens et la récupération des événements.

**Réponse** :
> Jarvis gère l'authentification OAuth 2.0 pour Google Calendar via un flux de configuration manuelle :
>
> 1. **Authentification** — L'utilisateur crée un projet Google Cloud, active l'API Calendar, et génère des identifiants (JSON) via le portail Google. Ces identifiants sont placés dans `data/google_calendar/credentials.json` (droits 600). Une autorisation manuelle est requise via le navigateur, avec validation de l'application (mode "Test" pour les utilisateurs non sécurisés).
> 2. **Stockage des tokens** — Le token d'accès est sauvegardé dans `data/google_calendar/token.json` (droits 600). Les chemins des fichiers de credentials/token peuvent être personnalisés via `JARVIS_GOOGLE_CREDENTIALS` et `JARVIS_GOOGLE_TOKEN`.
> 3. **Récupération des événements** — La fonction `calendar_list_events` consulte l'agenda principal en lecture seule (scope `https://www.googleapis.com/auth/calendar.events.readonly`). Elle retourne des événements triés par date, avec possibilité de filtrer par intervalle (ex. 7 prochains jours). Aucune modification ou création d'événements n'est effectuée.
>
> Le système ne stocke pas de données sensibles en clair, et les opérations reposent sur l'API Google Calendar v3 en mode "lecture seule".
>
> Correction : rien n'a été enregistré, modifié ni supprimé dans ta mémoire personnelle. Seules les commandes « Retiens que… », « Note que… », « Souviens-toi que… » et « Oublie… » modifient le profil.

**Observation** : réponse **bien documentée** (chemins, variables d'environnement, scope : cohérents avec
`GOOGLE_CALENDAR_SETUP.md` et le README). Réserve : « ne stocke pas de données sensibles en clair » est une
affirmation non vérifiée (le token est en clair, droits 0600). La « Correction » finale est un faux positif du
détecteur d'écritures mémoire (`claims_memory_write`), sans rapport avec la question.

## Test 4 — Cycle de vie du client MCP (conversation 5)

**Question** : Lis les fichiers concernés et explique comment Jarvis démarre, utilise, redémarre et arrête son client
MCP Kensaku.

**Réponse** :
> Aucun fichier trouvé dans la mémoire de Jarvis qui explique précisément comment gérer le client MCP Kensaku (le terme n'apparaît pas dans les résultats). La documentation disponible décrit le fonctionnement général de Jarvis (démarrage via `uvicorn`, gestion de la mémoire, etc.), mais pas spécifiquement le client MCP Kensaku. Veuillez vérifier si le fichier concerné est indexé ou préciser son chemin pour une recherche plus ciblée.

**Observation** : échec honnête mais **incorrect** : `app/functions/kensaku.py` et le README décrivent ce cycle de vie.
Qwen n'a pas trouvé/lu le bon fichier et l'a dit (pas d'invention), mais sans avertissement ⚠ (même raison que le test 2).

## Bilan

| Test | Documenté | Exact | Commentaire |
|------|-----------|-------|-------------|
| 1 | non | partiel | honnête sur ses limites |
| 2 | non | **non** | invention plausible, aucun ⚠ |
| 3 | oui | oui (sauf « pas en clair ») | faux positif « Correction » en fin de réponse |
| 4 | non | n/a | échec honnête, mauvais fichier cherché |

Pistes (non implémentées, à valider avant) :
- Étendre le déclencheur du garde-fou aux formulations au pluriel / sans nom de fichier (« Lis les fichiers concernés… »).
- Faire corriger le faux positif `claims_memory_write` sur les réponses documentaires (test 3).
- Lecture documentaire multi-passages (déjà au `BACKLOG.md`).

# Garde d'intention de `docs_reindex` — essais réels (2026-11-15)

Contexte : port 8002, base temporaire, vrai Kensaku (daemon géré par l'interface Kensaku), Qwen 3 8B.
Avant le garde, « Peux-tu réindexer si besoin pour répondre à ma question sur le README ? » déclenchait une
réindexation (conditionnel non respecté par Qwen). Garde ajouté : `FunctionSpec.intent_guard` +
`is_explicit_reindex_request()` (message original, refus par défaut).

| # | Message | Résultat | Journal |
|---|---------|----------|---------|
| 1 | « Réindexe si besoin pour répondre au README » | **Refus** : Qwen demande de confirmer (« Réindexe mes documents ») | `docs_reindex statut=erreur durée=0 ms` (aucun appel MCP `reindex`) |
| 2 | « Réindexe mes documents » | **Déclenchée** : « La réindexation de tes documents a démarré. » | `docs_reindex statut=ok durée=298 ms` |
| 3 | « Quel est l'état de l'index ? » | **Consultation seule** : phase idle, pas d'indexation en cours | `docs_index_state statut=ok` |

Remarques :
- Dans le test 1, Qwen a aussi appelé `docs_index_state` (lecture seule, sans effet) avant l'appel refusé.
- « 60 fichiers indexés sur 49 » (test 3) vient des données de Kensaku, restituées telles quelles par Jarvis.
- Limite assumée : un « Oui » seul, après une demande de confirmation, est refusé ; il faut répéter l'ordre
  (« Réindexe mes documents »).
- Suite complète au moment de l'essai : 353 passés, 1 xfailed.
