# Historique — campagne réelle mini-llm, garde d'intention et traces JSONL

Date : 2026-11-16, après les corrections de l'étape 3 (garde, `.env`, logs de démarrage) et l'étape 4 (traçabilité).
Serveur `uvicorn app.main:app --port 8002` avec base SQLite temporaire (`data/jarvis.db` non touchée), Kensaku
désactivé, `POST /chat` (appel de la page `/ask`). Une requête par test, traces dans `data/logs/trace.jsonl`.
Aucun fichier du projet modifié pendant la campagne. Réponses tronquées à 260 caractères (extraits).

## Configuration et démarrage

- `.env` chargé : aucune variable `JARVIS_MINILLM_*` dans l'environnement, et pourtant le log de démarrage indique
  `mini-llm activé : spécialité text-continuation, checkpoint livres_P4_theatre_meilleur.pt.`
  (contenu de `.env` non lu : accès interdit par la politique de contenu de l'organisation).
- Priorité de l'environnement : `JARVIS_DB_PATH` fourni par l'environnement utilisé (base temporaire).
- `story_generate` enregistrée (appelée dans les tests 1, 2 et 10). `/ask` répond 200 et envoie ses requêtes à `/chat`.
- Traces : `data/logs/trace.jsonl` créé (ignoré par git).

## Résultats

| # | Message | Attendu | Obtenu | request_id | Verdict |
|---|---|---|---|---|---|
| 1 | Raconte-moi une histoire sur un chevalier qui traverse les Alpes. | mini-llm | Qwen → garde accepté → mini-llm → Qwen (34 s, mini-llm 3,4 s) | 7f419959cc68 | PASS |
| 2 | Écris une courte histoire fantastique avec un dragon. | mini-llm | idem (29 s) | a3a7416dfbbf | PASS |
| 3 | Sais-tu raconter des histoires ? | pas de mini-llm | pas d'appel de fonction | 88902a9672e2 | PASS (réponse bizarre) |
| 4 | Es-tu capable d'écrire un récit fantastique ? | pas de mini-llm | idem | f08e0a8a63c1 | PASS (réponse fausse) |
| 5 | Ne me raconte pas d'histoire. | pas de mini-llm | idem, refus respecté | f0a0be0a1dfd | PASS |
| 6 | Ne m'écris surtout pas de récit. | pas de mini-llm | idem | 1e1b2015a44a | PASS technique (réponse parasite) |
| 7 | Raconte-moi une histoire si tu en as envie. | pas de mini-llm | idem | fd2a4fd3b2bd | PASS |
| 8 | Quand tu auras le temps, raconte-moi une histoire. | pas de mini-llm | idem | 6978c57a39b6 | PASS |
| 9 | Un jour, tu pourrais peut-être me raconter une histoire. | pas de mini-llm | idem | a15c22e2ad1a | PASS |
| 10 | Raconte-moi une histoire avec un chevalier, mais ne la fais pas trop longue. | mini-llm | séquence complète (20 s) | 80755fac645f | PASS |

Séquence des événements des tests 1, 2 et 10 : `request.started` → `model.selected` (qwen, primary, `no_specialty`) →
`model.generation.*` Qwen → `function.called` (story_generate) → `intent_guard.accepted` → `model.selected` (mini-llm,
specialist, `specialist_match`, `via_function=story_generate`) → `adapter.completed` (MiniLLMAdapter) →
`function.completed` → nouvelle génération Qwen → `request.completed` (19 événements).
Tests 3 à 9 : 7 événements, Qwen seul, aucun `function.called`, aucun événement mini-llm.

## Extraits de réponses

- **1** : « Voici une expérimentation de génération littéraire : **Amorce** : *Au cœur d'une forêt oubliée vivait un jeune
  chevalier qui rêvait des étoiles…* **Suite** : *et des villes lointaines, qui se…* »
- **3** : « Je peux t'aider à créer des histoires si tu en as envie, mais je ne raconte pas d'histoires sans demande
  explicite… »
- **4** : « Je ne suis pas programmé pour raconter des histoires, mais je peux créer des récits fantastiques si vous me
  demandez explicitement… pas autorisé à initier des histoires sans votre demande. »
- **6** : « Je ne rédigerai pas de récit, comme vous le demandez. Note que cette instruction est enregistrée… Correction :
  rien n'a été enregistré… »
- **7** : « Je ne peux pas raconter d'histoire, car votre demande est conditionnelle… D'après les règles, je ne dois pas
  générer de contenu… »
- **10** : « Voici une continuation expérimentale de votre histoire : *« Au centre de la forêt, comme des bosquets de
  pierres tombées… »* Note : Ce résultat provient d'… »

## Cohérence des traces

- Même `request_id` pour tous les événements d'une requête ; `seq` continu de 1 à N, sans trou.
- Durées plausibles : mini-llm 2 à 3,4 s, Qwen 6 à 18 s ; aucune erreur ni `failed` (106 lignes au total).
- Aucun fragment de message, d'amorce, de réponse ni aucun chemin dans le fichier (recherche de « chevalier »,
  « Alpes », « dragon », « histoire », « envie », « Voici », « Au cœur », `/dataSSD`, `/home` : 0 occurrence).

## Garde et contournements

Dans les tests 3 à 9, Qwen n'a jamais appelé `story_generate` : le garde n'a donc pas été sollicité en conditions
réelles (il reste couvert par les tests unitaires). Aucune histoire produite par Qwen dans ces cas ; aucun
contournement d'un refus du garde observé.

## Anomalies constatées (non corrigées)

1. Tests 3, 4, 7, 8, 9 : réponses de Qwen inexactes ou confuses (« pas programmé », « pas autorisé », « d'après les
   règles »). Cause probable : la description de `story_generate`, très prescriptive, reformulée comme une règle de
   Qwen. Piste : consignes plus neutres.
2. Test 6 : faux positif connu du garde `claims_memory_write` (« Correction : rien n'a été enregistré »).
3. Tests 1, 2, 10 : l'amorce reprend parfois l'exemple de la description (forêt oubliée, chevalier et étoiles) au lieu
   du sujet demandé.
4. Test 10 : la contrainte « pas trop longue » n'est pas transmise au paramètre `length`.
