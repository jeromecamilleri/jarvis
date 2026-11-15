# Google Agenda — configuration pas à pas

Jarvis lit ton agenda Google principal en **lecture seule** (scope
`https://www.googleapis.com/auth/calendar.events.readonly`). Rien n'est jamais créé, modifié ou supprimé.

## 1. Environnement Python

Le projet utilise un environnement virtuel `.venv` (Ubuntu refuse `pip install` hors venv : erreur
`externally-managed-environment`).

    cd /dataSSD/travailJerome/jarvis
    source .venv/bin/activate        # le prompt affiche (.venv)
    pip install -r requirements.txt

## 2. Projet Google Cloud

1. Ouvrir https://console.cloud.google.com et créer un projet.
2. « APIs et services » → « Bibliothèque » → activer **Google Calendar API**.
3. « Google Auth Platform » → **Branding / Audience** : type **Externe**, état de publication **Test**.
4. **Audience** → « Utilisateurs test » → **Add users** → ajouter ton adresse Google → Enregistrer.
   Sans cette étape : `Erreur 403 : access_denied — jarvis n'a pas terminé la procédure de validation`.
5. **Clients** → « Créer un client » → type **Application de bureau** → télécharger le JSON.

## 3. Placer les identifiants

    mkdir -p data/google_calendar
    mv ~/Téléchargements/client_secret_*.json data/google_calendar/credentials.json
    chmod 600 data/google_calendar/credentials.json

Le dossier `data/google_calendar/` est ignoré par Git. Chemins modifiables via `JARVIS_GOOGLE_CREDENTIALS`
et `JARVIS_GOOGLE_TOKEN`.

## 4. Première autorisation

    python -m app.functions.calendar auth

Le navigateur s'ouvre. Écran « application non validée » (normal en mode Test) : **Paramètres avancés** →
**Accéder à jarvis (non sécurisé)** → autoriser la lecture de l'agenda. Le message
« Autorisation enregistrée (data/google_calendar/token.json, droits 0600) » confirme la réussite.

## 5. Vérification

    python -m app.functions.calendar check     # JSON des événements des 7 prochains jours

Erreur 403 « API not enabled » : activer Google Calendar API (étape 2) puis relancer.

## 6. Utilisation dans Jarvis

    uvicorn app.main:app --port 8000

Ouvrir `http://localhost:8000/ui`, puis (texte ou voix) :
« Quels sont mes rendez-vous aujourd'hui ? », « Qu'est-ce que j'ai demain ? »,
« Quel est mon prochain rendez-vous ? », « Quels sont mes rendez-vous cette semaine ? »,
« Ai-je quelque chose vendredi après-midi ? ».

À vérifier : réponse conforme au vrai agenda (heures de Paris), « rien de prévu » un jour vide, pas d'appel à
l'agenda pour une conversation normale, mémoire et rappels toujours fonctionnels. Si la réponse dit que
l'information « n'a pas été vérifiée », Qwen n'a pas appelé la fonction : noter la phrase exacte.

## 7. Dépannage

| Symptôme | Cause / action |
|---|---|
| `externally-managed-environment` | Activer `.venv` (étape 1) |
| `Fichier introuvable : …credentials.json` | Étapes 2 et 3 |
| `Erreur 403 : access_denied` | Ajouter ton compte en utilisateur test (étape 2.4) |
| « L'autorisation Google a expiré ou a été révoquée » | Relancer `auth`. En mode Test, le jeton expire après **7 jours** ; passer l'application « En production » (avertissement « non validée » conservé) pour l'éviter |
| 403 « API not enabled » | Activer Google Calendar API |
| `git status` montre `data/google_calendar/` | Vérifier `.gitignore` : ne jamais committer ces fichiers |
