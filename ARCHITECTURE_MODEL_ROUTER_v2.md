# Routage dynamique des modèles dans Jarvis

**Statut :** proposition d'architecture  
**Date :** 16 novembre 2026

## 1. Objectif

Faire évoluer Jarvis vers une architecture capable d'utiliser plusieurs modèles, sans être définitivement lié à un fournisseur ou à un moteur unique.

**Principe directeur : pour l'instant, le cerveau principal de Jarvis reste le modèle Qwen utilisé via Ollama.** Il conserve la responsabilité générale de la conversation, de la compréhension des demandes, du raisonnement, de la planification et de la coordination des outils.

Les modèles développés dans le projet `mini-llm` pourront être ajoutés progressivement comme des moteurs spécialisés, appelés par Jarvis lorsque leur compétence est pertinente et suffisamment éprouvée. Ils ne remplacent pas Qwen et ne deviennent pas le cerveau principal de Jarvis à ce stade.

Cette approche permet de combiner :

- un modèle généraliste éprouvé pour assurer le fonctionnement quotidien de Jarvis ;
- des modèles spécialisés, notamment ceux développés dans le projet `mini-llm` ;
- des modèles personnels associés à des usages particuliers, par exemple `midi-chapgt` pour la création et la narration d'histoires ;
- éventuellement, d'autres moteurs locaux ou distants ajoutés ultérieurement.

Le projet `jarvis` reste responsable de l'interface, de l'orchestration et de l'utilisation des outils. Le projet `mini-llm` reste indépendant et responsable des architectures neuronales, des données, de l'entraînement et de l'évaluation des modèles.

## 2. Pourquoi Qwen reste le cerveau principal

Le choix de conserver Qwen comme cerveau principal est volontaire et pragmatique.

### 2.1 Un modèle généraliste déjà opérationnel

Qwen dispose de capacités générales qui permettent à Jarvis de traiter des demandes très variées : dialogue, compréhension du langage, synthèse, explication, raisonnement et, selon la variante utilisée, programmation.

Le modèle a déjà été intégré à Jarvis via Ollama. Il constitue donc une base fonctionnelle sur laquelle l'application peut continuer à évoluer, sans attendre que les modèles expérimentaux atteignent un niveau de maturité comparable.

### 2.2 Un écart important de maturité avec le mini-LLM

Le mini-LLM est un projet expérimental et pédagogique. Son entraînement sur un corpus de livres et les expériences menées sur des tâches structurées permettent d'étudier concrètement les mécanismes d'apprentissage.

Mais un corpus de livres, même conséquent, ne suffit pas à obtenir les capacités générales d'un modèle industriel. Celles-ci résultent notamment de volumes d'entraînement très importants, de données diversifiées, de phases de post-entraînement et d'un travail considérable sur le suivi des instructions, le raisonnement et l'utilisation des outils.

À ce stade, il serait donc irréaliste de demander au mini-LLM de remplacer Qwen pour gérer toute la conversation et l'orchestration de Jarvis.

### 2.3 Éviter de fragiliser Jarvis

Jarvis doit rester utilisable et progresser indépendamment des résultats des expérimentations sur le mini-LLM.

Conserver Qwen comme modèle principal permet de :

- maintenir un comportement généraliste cohérent ;
- poursuivre le développement de l'interface et de l'orchestrateur ;
- tester les outils et les fonctionnalités sans dépendre des performances du mini-LLM ;
- expérimenter de nouveaux modèles sans remettre en cause le fonctionnement courant.

### 2.4 Une spécialisation progressive, fondée sur des résultats mesurables

Le mini-LLM n'a pas besoin de devenir meilleur que Qwen dans tous les domaines. Il doit démontrer une compétence précise sur une tâche bien définie.

Par exemple :
- un modèle entraîné spécifiquement pour résoudre des Sudoku pourra prendre en charge cette tâche ;
- le modèle personnel `midi-chapgt` pourra être utilisé pour inventer et raconter des histoires, si ses résultats sont adaptés à cet usage.

Qwen restera responsable de comprendre la demande, de choisir la compétence pertinente, de transmettre les informations utiles et de présenter le résultat dans le fil de la conversation.

**Qwen est donc le cerveau généraliste ; les modèles personnels de mini-llm deviennent des compétences spécialisées que Jarvis peut apprendre à mobiliser.**

## 3. Principe d'architecture

Le composant central est un **routeur de modèles** (*Model Router*).

Il ne remplace pas le cerveau principal : il permet à Jarvis, sous la coordination de Qwen et de l'orchestrateur, de sélectionner un moteur spécialisé lorsque cela est utile.

```text
Utilisateur
    |
    v
Interface Jarvis
    |
    v
Orchestrateur Jarvis
    |
    v
Qwen via Ollama
CERVEAU PRINCIPAL
- comprend la demande
- raisonne et planifie
- décide si un outil ou un modèle spécialisé est nécessaire
    |
    v
Model Router / registre des capacités
    |
    +----------------------+-----------------------+
    |                      |                       |
    v                      v                       v
Ollama                 API mini-LLM           Autres moteurs
- modèle code          - Sudoku               - à définir
- autres modèles       - logique
                       - tâches spécialisées
    |                      |                       |
    +----------------------+-----------------------+
                           |
                           v
                 Résultat retourné à Qwen
                           |
                           v
              Vérification / interprétation
                           |
                           v
                  Réponse de Jarvis
```

Le chemin général reste donc celui qui existe déjà : Jarvis utilise Qwen via Ollama. Le recours à un modèle spécialisé constitue une délégation ponctuelle. Le résultat revient ensuite dans le flux principal afin que Qwen puisse l'interpréter et formuler la réponse destinée à l'utilisateur.

Pour certaines tâches, une validation déterministe (tests, contraintes, vérificateur dédié) sera préférable à une simple vérification par Qwen.

## 4. Exemples de routage

| Type de demande | Traitement envisagé |
|---|---|
| Conversation générale | Qwen via Ollama |
| Explication ou synthèse technique | Qwen via Ollama |
| Analyse ou génération de code | Qwen ou un modèle de code via Ollama |
| Résolution de Sudoku | Qwen comprend la demande et délègue au mini-LLM Sudoku |
| « Raconte-moi une histoire… » | Qwen identifie une demande de narration et délègue la génération à `midi-chapgt` |
| Autre problème logique spécialisé | Qwen délègue au mini-LLM correspondant, après entraînement et évaluation |
| Action simple et déterministe | Fonction ou outil Jarvis, sans nécessairement appeler un autre LLM |

Ces associations sont des exemples de configuration, pas des choix figés dans le code.

## 5. Stratégie de routage : commencer simplement

### 5.1 Qwen reste le point d'entrée général

Dans un premier temps, toutes les demandes continuent d'être traitées par Qwen comme aujourd'hui.

Lorsque des compétences spécialisées seront disponibles, l'orchestrateur pourra lui permettre de les appeler selon des règles explicites. Il ne faut pas ajouter une couche de classification complexe avant d'avoir identifié des cas d'usage réels.

### 5.2 Routage déterministe

La première version doit utiliser des règles explicites et faciles à tester :

- identifier les tâches qui disposent réellement d'un moteur spécialisé ;
- vérifier que ce moteur est disponible ;
- transmettre la tâche au moteur concerné ;
- récupérer et contrôler son résultat ;
- revenir dans le flux principal de Qwen ;
- conserver Qwen comme solution par défaut lorsqu'aucune délégation n'est nécessaire.

Cette approche est prévisible, observable et simple à déboguer.

### 5.3 Routage intelligent, dans une évolution ultérieure

Une évolution pourra permettre à Qwen de proposer lui-même l'utilisation d'une compétence spécialisée, à partir d'un registre décrivant les capacités disponibles.

Le choix devra rester contrôlé par l'orchestrateur : un modèle ne doit pas pouvoir invoquer librement n'importe quel outil ou service. Les permissions, validations et limites d'exécution de Jarvis restent applicables.

## 6. Configuration des modèles

Les modèles et leurs caractéristiques doivent être déclarés dans une configuration externe, plutôt que codés en dur dans l'orchestrateur.

Exemple indicatif :

```yaml
models:
  main:
    provider: ollama
    name: qwen
    role: primary
    capabilities:
      - conversation
      - reasoning
      - explanation
      - orchestration

  coding:
    provider: ollama
    name: qwen-coder
    role: specialist
    capabilities:
      - code

  sudoku:
    provider: local_api
    endpoint: http://localhost:8001/predict
    role: specialist
    capabilities:
      - sudoku

  story_writer:
    provider: local_api
    endpoint: http://localhost:8002/generate
    role: specialist
    capabilities:
      - storytelling
      - creative_writing
    description: "Modèle personnel midi-chapgt du projet mini-llm"

routing:
  primary_model: main
  specialists:
    sudoku: sudoku
    storytelling: story_writer
    code: coding
```

Les noms de modèles, les endpoints, les paramètres et les capacités ci-dessus sont des exemples à adapter aux modèles réellement installés. Les secrets éventuels ne doivent pas être stockés en clair dans ce fichier de configuration.

Le modèle `main` est le modèle utilisé par défaut. Les modèles `specialist` ne sont appelés que pour les tâches qui leur sont explicitement associées.

## 7. Interface commune entre Jarvis et les moteurs

Le routeur doit masquer autant que possible les différences entre les fournisseurs.

Une requête interne pourrait contenir :

```json
{
  "task": "sudoku",
  "prompt": "Résous cette grille...",
  "context": {},
  "preferred_model": "sudoku"
}
```

Un résultat normalisé pourrait contenir :

```json
{
  "model": "sudoku",
  "provider": "local_api",
  "status": "success",
  "content": "...",
  "structured_output": {},
  "duration_ms": 125
}
```

Ces structures sont des contrats indicatifs. Elles devront être ajustées aux besoins réels de l'application.

Pour les modèles spécialisés, une réponse structurée est préférable à du texte libre lorsque la tâche s'y prête. Par exemple, un solveur Sudoku devrait idéalement retourner une grille et un statut de validation.

Le résultat spécialisé doit être transmis à l'orchestrateur puis rendu disponible à Qwen, afin que le cerveau principal puisse l'expliquer ou l'intégrer à la conversation.

## 8. Sélection manuelle et transparence

Le routage automatique ne doit pas empêcher l'utilisateur de garder le contrôle.

Évolutions souhaitables dans l'interface :

- afficher le modèle principal utilisé ;
- afficher les éventuelles délégations à des modèles spécialisés ;
- permettre de demander explicitement un modèle ou une compétence ;
- conserver Qwen comme comportement automatique par défaut ;
- signaler lorsqu'un modèle spécialisé est indisponible et qu'une solution de repli a été utilisée.

Exemples de commandes possibles :

- « Utilise mon mini-LLM pour cette tâche. »
- « Raconte-moi une histoire avec mon modèle midi-chapgt. »
- « Réponds directement avec Qwen, sans délégation. »
- « Quel modèle as-tu utilisé pour résoudre ce problème ? »

## 9. Utilisation successive de plusieurs modèles

À plus long terme, Jarvis pourra composer plusieurs modèles dans une même opération.

Exemples :

**Résolution de Sudoku**
1. Qwen comprend la demande et identifie une tâche Sudoku.
2. Jarvis transmet la grille au mini-LLM spécialisé.
3. Un vérificateur indépendant contrôle que la grille respecte les règles du Sudoku.
4. Le résultat et le statut de validation sont retournés à Qwen.
5. Qwen explique le résultat à l'utilisateur.

**Création d'une histoire**
1. Qwen comprend que l'utilisateur souhaite une histoire et recueille les éventuelles contraintes (thème, personnages, longueur, ton, public).
2. Jarvis transmet ces éléments à `midi-chapgt`, le modèle personnel de narration du projet `mini-llm`.
3. `midi-chapgt` génère l'histoire.
4. Jarvis retourne le texte à l'utilisateur. Qwen peut éventuellement ajouter une introduction ou poursuivre le dialogue, sans réécrire systématiquement le texte produit par le modèle personnel.

Cette deuxième utilisation illustre une autre forme d'enrichissement personnel : Jarvis ne se contente pas d'appeler des outils techniques, il sait aussi choisir un modèle que l'utilisateur a entraîné pour une activité créative qui lui est propre.

Cette composition doit rester ciblée. Faire intervenir plusieurs grands modèles sur chaque demande augmenterait inutilement la latence et la consommation de ressources.

Un second LLM qui approuve le premier ne constitue pas, à lui seul, une preuve de correction. Les vérifications déterministes doivent être privilégiées lorsque c'est possible.

## 10. Personal Knowledge & Learning

Jarvis doit disposer, en complément du routeur de modèles, d'un véritable module **Personal Knowledge & Learning**. Son rôle est de conserver les informations utiles propres à l'utilisateur, de capitaliser sur les usages et de structurer les connaissances documentaires mobilisées par l'assistant.

Ce module ne remplace ni Qwen ni le Model Router. Il leur fournit du contexte et des connaissances persistantes, avec des règles explicites de gestion et de contrôle.

Il regroupe trois responsabilités complémentaires.

### 10.1 Memory Manager — mémoire personnelle

Le Memory Manager gère les informations durables concernant l'utilisateur et ses interactions avec Jarvis.

Exemples :
- préférences de communication et de fonctionnement ;
- environnement technique et matériel connu ;
- projets en cours, décisions prises et contraintes associées ;
- éléments de contexte utiles issus des échanges précédents.

La mémoire ne doit pas être une simple accumulation de conversations. Jarvis doit distinguer les informations ponctuelles des éléments qui méritent d'être conservés durablement.

Chaque information mémorisée devrait, autant que possible, être associée à des métadonnées :
- source ou origine de l'information ;
- date de création et date de dernière confirmation ;
- niveau de confiance ;
- catégorie et éventuel projet associé ;
- statut (active, à confirmer, obsolète) et durée de conservation éventuelle.

L'utilisateur doit pouvoir consulter, corriger et supprimer les informations mémorisées. Une information ancienne ou incertaine ne doit pas être présentée automatiquement comme une vérité actuelle.

### 10.2 Skill Manager — apprentissage des associations entre tâches, modèles et outils

Le Skill Manager gère les compétences opérationnelles de Jarvis : il apprend quels modèles, outils ou procédures utiliser pour un type de tâche donné.

Exemples :
- associer `storytelling` au modèle personnel `midi-chapgt` ;
- associer la résolution de Sudoku au modèle spécialisé correspondant ;
- associer une opération système à un outil ou à une procédure autorisée.

Une compétence doit décrire au minimum :
- l'usage ou l'intention détectée ;
- le modèle, l'outil ou la procédure à mobiliser ;
- les paramètres et prérequis nécessaires ;
- les limites connues et les règles de validation ;
- les solutions de repli en cas d'indisponibilité.

L'utilisateur doit pouvoir enseigner une association à Jarvis en langage naturel, puis la consulter et la modifier. Par exemple : « Pour raconter des histoires, utilise mon modèle midi-chapgt. »

Il s'agit d'un apprentissage de configuration et de comportement : Jarvis enregistre une règle explicite dans son registre de compétences. Cela ne signifie pas que Qwen est automatiquement réentraîné ou que les poids de `midi-chapgt` sont modifiés.

Le Skill Manager complète donc le Model Router : le premier conserve la connaissance de *quelle compétence utiliser et pourquoi* ; le second exécute la sélection et la délégation vers le moteur approprié.

### 10.3 Knowledge Manager — connaissances documentaires et projets

Le Knowledge Manager organise les connaissances issues des documents, notes, dépôts de code et ressources associées aux projets de l'utilisateur.

Exemples :
- documentation technique et procédures ;
- fichiers Markdown, PDF et notes de travail ;
- documentation et décisions des projets `jarvis` et `mini-llm` ;
- synthèses de documents et liens entre informations.

Il doit permettre à Jarvis de retrouver des éléments pertinents, de les rattacher à leur source et de distinguer les faits documentés des déductions produites par un modèle.

Une recherche sémantique avec index vectoriel pourra être ajoutée pour retrouver des passages pertinents dans un corpus documentaire. Elle ne doit toutefois pas remplacer les sources originales : les réponses importantes doivent pouvoir renvoyer au document et au passage qui les justifient.

### 10.4 Organisation logique

```text
Personal Knowledge & Learning
    |
    +-- Memory Manager
    |     +-- profil et préférences
    |     +-- contexte personnel
    |     +-- mémoire des échanges et décisions
    |
    +-- Skill Manager
    |     +-- associations tâches / modèles / outils
    |     +-- procédures et paramètres
    |     +-- règles de validation et de repli
    |
    +-- Knowledge Manager
          +-- documents et notes
          +-- connaissances des projets
          +-- index de recherche et références aux sources
```

Ces trois gestionnaires ont des responsabilités distinctes, même s'ils peuvent partager une infrastructure de stockage et des mécanismes de recherche.

### 10.5 Stockage et technologies envisageables

Pour une première version, il est préférable de commencer simplement :
- **SQLite** pour les informations structurées : profil, mémoire, projets, compétences et métadonnées ;
- fichiers originaux conservés dans leur emplacement de référence ;
- historique des conversations séparé de la mémoire durable ;
- index vectoriel (FAISS ou ChromaDB, par exemple) uniquement lorsque la recherche sémantique dans les documents devient nécessaire.

Il n'est pas nécessaire d'introduire immédiatement une base vectorielle ou une infrastructure complexe. Une base SQLite bien structurée permet déjà de construire et de tester les trois gestionnaires.

Les données personnelles doivent être cloisonnées par utilisateur si Jarvis devient multi-utilisateur. Les accès aux documents et aux outils doivent respecter les permissions existantes.

### 10.6 Cycle d'apprentissage et de mémorisation

Le fonctionnement peut suivre un cycle contrôlé :

1. **Observer :** repérer une information, une préférence, une décision ou une association exprimée par l'utilisateur.
2. **Qualifier :** déterminer si elle relève de la mémoire personnelle, d'une compétence ou de la connaissance documentaire.
3. **Proposer ou enregistrer :** appliquer le niveau de validation adapté ; demander confirmation lorsque l'information est ambiguë ou sensible.
4. **Consolider :** éviter les doublons, gérer les contradictions et marquer les informations devenues obsolètes.
5. **Réutiliser :** fournir à Qwen uniquement le contexte utile à la demande en cours, avec sa provenance lorsque c'est pertinent.

La mémorisation doit rester maîtrisée : tout ce qui apparaît dans une conversation n'a pas vocation à devenir une donnée permanente.

## 11. Organisation du code envisagée

Proposition indicative, à adapter à l'organisation actuelle du dépôt :

```text
jarvis/
├── README.md
├── ARCHITECTURE_MODEL_ROUTER.md
├── config/
│   └── models.yaml
└── app/
    └── ...
```

Le routeur pourrait être isolé dans un module tel que `model_router.py`, avec des adaptateurs distincts pour les fournisseurs :

- adaptateur Ollama ;
- adaptateur HTTP pour le mini-LLM ;
- adaptateurs futurs.

Il faut éviter que l'orchestrateur connaisse les détails propres à chaque API. Il doit appeler une interface commune.

Cette évolution devra s'intégrer à l'architecture et aux conventions déjà présentes dans le dépôt, sans imposer prématurément une nouvelle organisation.

## 12. Plan de mise en œuvre progressif

### Étape 1 — Préserver et documenter le fonctionnement actuel

- Identifier où Jarvis appelle actuellement Qwen via Ollama.
- Recenser le modèle et les paramètres réellement utilisés.
- Documenter Qwen comme modèle principal dans la configuration.
- Identifier les points d'entrée de l'orchestrateur et les tests existants.
- Ne pas modifier le fonctionnement courant à ce stade.

### Étape 2 — Introduire une interface de modèle

- Définir un contrat commun pour les requêtes et les réponses.
- Isoler l'appel Qwen/Ollama dans un adaptateur.
- Vérifier que Jarvis conserve exactement son comportement actuel.
- Ajouter des tests de non-régression.

### Étape 3 — Ajouter le registre des modèles spécialisés

- Déclarer les modèles disponibles et leurs capacités.
- Conserver Qwen comme modèle par défaut.
- Ajouter les règles de délégation pour les tâches réellement couvertes.
- Tester les cas sans correspondance et les modèles indisponibles.

### Étape 4 — Connecter le mini-LLM

- Exposer les modèles du mini-LLM par une API HTTP stable dans leur propre dépôt.
- Ajouter un adaptateur dans Jarvis.
- Définir les formats d'entrée et de sortie propres à chaque tâche spécialisée.
- Ajouter `midi-chapgt` au registre des modèles avec une capacité de narration (`storytelling`).
- Définir les paramètres de narration transmis au modèle : sujet, personnages, style, longueur et contraintes éventuelles.
- Tester les erreurs réseau, délais d'attente et indisponibilités.
- Retourner les résultats au flux principal de Qwen, en préservant le texte original produit par le modèle spécialisé.

### Étape 5 — Rendre le routage visible

- Afficher Qwen comme cerveau principal.
- Afficher les éventuelles délégations et le résultat de chaque modèle spécialisé.
- Permettre une sélection manuelle.
- Journaliser le fournisseur, le modèle, la durée et le statut, sans enregistrer inutilement les contenus sensibles.

### Étape 6 — Introduire Personal Knowledge & Learning

- Créer le module et définir les contrats entre Memory Manager, Skill Manager et Knowledge Manager.
- Commencer avec SQLite pour stocker les informations structurées et les compétences.
- Permettre à l'utilisateur de consulter, corriger et supprimer les informations mémorisées.
- Enregistrer explicitement les associations entre usages, modèles et outils (par exemple `storytelling` → `midi-chapgt`).
- Séparer l'historique brut des conversations de la mémoire durable.
- Ajouter progressivement l'indexation documentaire et la recherche sémantique si les besoins le justifient.

### Étape 7 — Explorer les évolutions

- Laisser Qwen proposer l'utilisation de compétences spécialisées, sous le contrôle de l'orchestrateur.
- Ajouter un repli contrôlé vers Qwen lorsqu'une délégation échoue.
- Composer plusieurs modèles et des vérificateurs déterministes.
- Évaluer objectivement les résultats, la latence et les ressources consommées.

## 13. Principes à préserver

1. **Qwen reste le cerveau principal :** il assure le dialogue général, la compréhension, le raisonnement et la coordination.
2. **Les modèles spécialisés restent des auxiliaires :** ils interviennent sur des tâches délimitées et évaluées.
3. **Indépendance des modèles :** changer ou ajouter un moteur ne doit pas imposer de réécrire Jarvis.
4. **Séparation des projets :** Jarvis orchestre ; `mini-llm` développe et entraîne les modèles.
5. **API explicite :** la communication entre les deux projets repose sur un contrat documenté et versionnable.
6. **Comportement testable :** le routage doit pouvoir être testé sans charger réellement tous les modèles.
7. **Repli maîtrisé :** une indisponibilité ne doit pas provoquer un changement silencieux de comportement.
8. **Sécurité inchangée :** aucun modèle ne peut contourner les permissions et contrôles des outils Jarvis.
9. **Mesure objective :** les modèles spécialisés doivent être évalués sur des jeux de tests adaptés à leur tâche.
10. **Enrichissement personnel :** Jarvis doit pouvoir apprendre à associer un modèle personnel à un usage que l'utilisateur lui indique, comme `midi-chapgt` pour raconter des histoires. Cette association doit être explicite, enregistrée dans le registre des capacités et modifiable par l'utilisateur.
11. **Mémoire maîtrisée :** les informations personnelles doivent être consultables, corrigibles et supprimables, avec leur provenance et leur niveau de confiance lorsque c'est possible.
12. **Séparation des connaissances :** mémoire personnelle, compétences opérationnelles et connaissances documentaires ont des rôles distincts et ne doivent pas être confondues.
13. **Respect des sources :** les connaissances documentaires doivent conserver un lien vers leurs documents d'origine ; une réponse générée ne doit pas être confondue avec une source vérifiée.

## 14. Vision

Jarvis conserve un cerveau généraliste fiable, Qwen via Ollama, et s'enrichit progressivement de compétences spécialisées développées localement.

Le mini-LLM n'a pas besoin de rivaliser avec Qwen sur toutes les tâches. Il doit apporter une compétence mesurable sur un domaine précis. Certaines compétences peuvent être techniques (résoudre un Sudoku), d'autres créatives (écrire une histoire avec `midi-chapgt`).

Jarvis doit aussi pouvoir **apprendre les associations entre les usages et les modèles personnels**. Par exemple, l'utilisateur lui indique que `midi-chapgt` est son modèle de narration ; Jarvis enregistre cette capacité et peut ensuite le sélectionner lorsqu'une demande consiste à raconter une histoire. Il s'agit ici d'apprendre une règle d'utilisation et de routage, pas de réentraîner automatiquement le modèle.

Jarvis fournit l'environnement qui rend ces compétences utilisables : interface, orchestration, outils et suivi des résultats. Le module **Personal Knowledge & Learning** lui apporte une mémoire personnelle contrôlée, un registre de compétences évolutif et une base de connaissances documentaires liée aux projets de l'utilisateur.

À terme, Jarvis pourra devenir un environnement local capable de mobiliser plusieurs moteurs d'intelligence, tout en conservant un point central de compréhension et de coordination.

**La stratégie n'est donc pas de remplacer Qwen par le mini-LLM, mais de faire grandir Jarvis autour de Qwen en lui ajoutant progressivement des compétences que nous aurons nous-mêmes développées et validées, y compris des compétences créatives personnelles.**
