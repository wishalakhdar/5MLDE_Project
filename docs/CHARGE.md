# Tests de charge et dimensionnement

## Protocole (fixé AVANT les mesures, dans `loadtests/configs.json`)
- **Scénario** (`loadtests/locustfile.py`) : chaque utilisateur virtuel enchaîne des appels comme une application
  cliente : prédiction d'une heure (poids 8), d'une journée de 24 heures (1), prédiction + feedback (1),
  entrée invalide `hr = 25` (1, 422 attendu et compté comme succès). Pause de 0,5 à 1,5 s entre deux appels :
  N utilisateurs ≈ N requêtes/s proposées. Entrées tirées au hasard dans les plages du contrat.
- **Paliers** : 10, 20, 40 puis 80 utilisateurs, 45 s chacun, après 10 s d'échauffement non mesuré.
- **Configurations comparées** (conteneur `api`, mêmes paliers pour toutes) :

| Nom | CPU | Processus Uvicorn | Mémoire | Question posée |
|---|---:|---:|---|---|
| A-0.5cpu-1w | 0,5 | 1 | 1 Go | la plus petite configuration suffit-elle ? |
| B-1cpu-1w | 1 | 1 | 1 Go | configuration de référence |
| C-1cpu-2w | 1 | 2 | 1 Go | ajouter un processus sans ajouter de CPU aide-t-il ? |
| D-2cpu-2w | 2 | 2 | 1 Go | doubler les ressources double-t-il le débit ? |
| E-4cpu-4w | 4 | 4 | 2 Go | ajoutée après la 1re série (D frôle l'objectif) : 4 CPU suffisent-ils pour 40 utilisateurs ? |

- **Mesures** : débit (req/s), latences p50 / p95 / p99, taux d'échec (Locust, côté client) ; CPU et mémoire du
  conteneur (`docker stats`, toutes les ~2 s ; 100 % = un cœur).
- **Objectif (SLO)** : au palier cible de **40 utilisateurs**, p95 ≤ **500 ms** et échecs ≤ **1 %**.
- **Règle de recommandation** : la configuration la moins coûteuse (CPU, puis processus) qui respecte l'objectif.

## Lancer
Services Docker lancés, alias `champion` posé, **API locale arrêtée** (le port 8000 est celui du conteneur) :
```
uv run python loadtests/run_config.py          # ~5 min par configuration
uv run python loadtests/summarize.py           # tableau + recommandation -> loadtests/results/summary.md
```
Une seule configuration : `uv run python loadtests/run_config.py --only B-1cpu-1w`.
Interface web de Locust (exploration manuelle) : `uv run locust -f loadtests/locustfile.py --host http://127.0.0.1:8000`
puis http://localhost:8089.

## Limites du protocole
- Locust et l'API tournent sur la même machine : le générateur de charge consomme lui aussi du CPU.
- Les entrées sont aléatoires : le coût d'une prédiction ne dépend pas des valeurs pour une forêt aléatoire,
  mais la distribution des prédictions n'est pas réaliste (sans effet sur la mesure de performance).
- Chaque requête écrit aussi dans PostgreSQL : la base fait partie du système mesuré (c'est voulu).

## Résultats
Voir `loadtests/results/summary.md` (généré) et la synthèse OPS.

| E-4cpu-4w | 4 | 4 | 2 Go | ajoutée après la 1re série (D frôle l'objectif) : 4 CPU suffisent-ils pour 40 utilisateurs ? |