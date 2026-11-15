# BACKLOG

Améliorations non indispensables, à traiter plus tard.

## Voix
- Wake word et écoute continue.
- Réponse vocale en streaming (phrase par phrase) pour réduire la latence ; Piper commence après la fin de Qwen.
- Interrompre Jarvis pendant qu'il parle.
- Whisper `medium`, ou sur GPU si de la VRAM se libère ; comparer la qualité sur les noms propres.
- Vérifier la transcription avant exécution d'un « Retiens que… » (confirmation) : un nom propre mal entendu est mémorisé tel quel.
- Message « Pour l'enregistrer, écris « Retiens que… » » : adapter à l'oral (« dis »).
- Choix du micro / des haut-parleurs dans l'interface ; indicateur de niveau sonore.
- Autres voix Piper, réglage de la vitesse.

## Mémoire épisodique (revue du sprint 3, points 4 à 8)
- 4. Sources filtrées par score de pertinence ; dates citées en heure locale (test xfail existant).
- 5. Cache mémoire des vecteurs (rechargés à chaque recherche).
- 6. Embeddings en arrière-plan plutôt que dans la requête.
- 7. Pondérer ou exclure les réponses de l'assistant indexées (hallucinations possibles).
- 8. Alternance VRAM qwen / bge-m3 sur la GTX 1070 (8 Go).
- Résumés de conversation (sprint ultérieur).

## Reportés (tests vocaux)
- Corrections automatiques de « Atos » dans la transcription (on garde la transcription brute).
- Suppression des emojis avant Piper.

## Rappels (première version)
- Rappels récurrents, agenda, jours de la semaine (« lundi à 9 h »), « ce soir », « dans 3 jours ».
- Formulations plus libres (évaluer `dateparser` si le besoin se confirme).
- Sonnerie persistante / commande d'arrêt, répétition (snooze).
- Notification dans le navigateur lorsque le serveur n'a pas accès à l'audio de la session.
- Modifier un rappel existant ; annuler par texte (« annule le rappel du linge »).

## Registre de fonctions / Google Agenda
- Fonctions `safe_write` / `sensitive` avec confirmation explicite (création d'événements).
- Autres calendriers, plusieurs comptes Google.
- Détection d'intention agenda déterministe (sans dépendre du choix d'outil par Qwen).
- Ne pas indexer en mémoire épisodique les réponses issues d'outils.
- Streaming des réponses avec outils.

## Kensaku (recherche documentaire)
- Pagination (`cursor`) et filtres `path` / `ext` / `modified_after` pour `docs_search`.
- Exposer `regex_search` / `find_symbol` si le besoin se confirme.
- Signaler à l'utilisateur un index périmé (`index_state`, `last_reconcile`) ; actualisation périodique de l'index.
- Indexer d'autres répertoires (docs personnelles) avec un périmètre explicite.
- Vérifier qu'un passage lu répond réellement à la question (pertinence, lecture de plusieurs passages / pagination) ; le garde-fou ne vérifie que la lecture technique.
- Détecter le « mauvais fichier » sans nom explicite dans le message.
- Relance du processus avec délai (backoff) si Kensaku tombe souvent.
